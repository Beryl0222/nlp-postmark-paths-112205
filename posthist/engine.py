"""邮路核订引擎。

引擎不挑一条"最顺"的行程来消除矛盾，而是枚举所有同时满足下列
约束的完整递送解释，再取它们的交集作为可确认内容：

1. 每个戳迹选择一种仍可成立的释读（含"后人释注/纪念补盖，不作
   递送证据"这一类角色选择）；
2. 邮局在所给日期处于开放期（历史简称按年代定局）；
3. 相邻戳迹之间在开放路网上存在走得通的路径，允许经过未留戳的
   中转局，且累计最短通达时间不超过两个时间窗口所能容纳的差；
4. 全程至多一段连续的退件路程（即一次退回），其后允许再次寄出；
5. 同一天的两个戳不被强制属于同一趟，跨日按当地零时的时间差判断。

任何字段都不因多一份抄录而被"投票确定"：同一印痕上的分歧原样
进入候选，只有在所有可行解释中都相同的站才能确认。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import product
from typing import Optional

from .catalog import era_years_for, resolve_office
from .dates import datetime_window
from .models import Catalog, Mark, Route
from .parse import MarkFacts, ParsedReading, all_mark_facts

MAX_RETURN_RUNS = 1
MAX_PATH_DEPTH = 6
# 同一实寄流程中，相邻留戳节点若年份本身未被释读确定，时间窗不得
# 无限拉长；超过该天数即视为跨年度的误配年份，而不是可信的邮路停留。
MAX_UNDATED_GAP_DAYS = 30


# ---------------------------------------------------------------------
# 戳迹释读选项
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class MarkOption:
    """一枚戳迹在某条解释中的角色与具体读法。"""

    mark_id: str
    role: str  # postal | non_delivery | unexplained
    office_id: Optional[str] = None
    year: Optional[int] = None
    year_explicit: bool = False
    month_day: Optional[tuple[int, int]] = None
    hour_key: Optional[str] = None  # 数字小时或"午后"等原文标签
    sources: tuple[str, ...] = ()
    note: str = ''

    @property
    def signature(self) -> tuple:
        return (self.role, self.office_id, self.year, self.month_day, self.hour_key)


@dataclass(frozen=True)
class TimedNode:
    """时间线上的一个戳迹节点。"""

    option: MarkOption
    window: tuple[datetime, datetime]

    @property
    def mark_id(self) -> str:
        return self.option.mark_id

    @property
    def office_id(self) -> str:
        return self.option.office_id  # type: ignore[return-value]


@dataclass(frozen=True)
class PathChoice:
    """两个留戳节点之间实际取用的路网走法。"""

    edges: tuple[Route, ...]
    offices: tuple[str, ...]
    total_min_hours: int
    return_runs: int


@dataclass(frozen=True)
class ExplanationRecord:
    """一条完整且仍可成立的递送解释。"""

    index: int
    mark_choices: dict[str, MarkOption]
    timeline: tuple[TimedNode, ...]
    paths: tuple[PathChoice, ...]
    trips: tuple[tuple[str, ...], ...]
    return_runs: int
    first_day: bool
    rejected_reason: Optional[str] = None


@dataclass
class Pivot:
    """可复查原字：该处改读后会改变路线或"首日实寄"归类。"""

    mark_id: str
    field: str
    alternatives: list[str]
    reading_sources: list[str]
    effect: str


@dataclass
class TransitGuard:
    """不能用来证明普通通达时间的跨趟节点对。"""

    from_mark: str
    to_mark: str
    reason: str


@dataclass
class CoverVerification:
    """单件封的核订结论。"""

    cover_id: str
    feasible: list[ExplanationRecord] = field(default_factory=list)
    confirmed_marks: list[dict] = field(default_factory=list)
    disputed_marks: list[dict] = field(default_factory=list)
    confirmed_edges: list[dict] = field(default_factory=list)
    disputed_edges: list[dict] = field(default_factory=list)
    first_day_status: str = 'pending'  # confirmed | refuted | pending
    first_day_reason: str = ''
    return_status: str = 'unknown'
    physical_notes: list[dict] = field(default_factory=list)
    pivots: list[Pivot] = field(default_factory=list)
    transit_guards: list[TransitGuard] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)

    @property
    def is_feasible(self) -> bool:
        return bool(self.feasible)


# ---------------------------------------------------------------------
# 选项构造
# ---------------------------------------------------------------------

def _postal_options(
    facts: MarkFacts, catalog: Catalog, era
) -> list[MarkOption]:
    options: list[MarkOption] = []
    tokens = list(facts.office_candidates)
    month_days = list(facts.month_day_candidates)
    hour_keys = list(facts.hour_candidates) or [None]

    # 年份来自全部释读的并集（含"疑为1962"这类推测），不靠票数定局
    explicit_years: list[int] = []
    hinted_years: list[int] = []
    for parsed in facts.parsed:
        expr = parsed.date_expr
        if expr is None:
            continue
        if expr.year is not None or expr.era_token is not None:
            # 公元年或年号纪年（壬17→1961）都属于释读明写的年份
            explicit_years.extend(era_years_for(era, expr))
        elif expr.year_hint is not None:
            hinted_years.append(expr.year_hint)
        else:
            # 释读完全没给年份：年表中的每个年份都先保留为候选
            hinted_years.extend(era.years)
    year_choices = sorted(
        (year, True) for year in set(explicit_years)
    ) + sorted((year, False) for year in set(hinted_years) - set(explicit_years))

    # 落不进日历的戳迹不能进入时间线，交由 unexplained 角色处理
    if not tokens or not month_days or not year_choices:
        return []

    for token, month_day, hour_key in product(tokens, month_days, hour_keys):
        month, day = month_day
        for year, explicit in year_choices:
            resolution = resolve_office(catalog, era, token, [year], month, day)
            for office in resolution.offices:
                sources = sorted(
                    set(facts.office_candidates.get(token, ()))
                    | set(facts.month_day_candidates.get(month_day, ()))
                    | set(facts.hour_candidates.get(hour_key, ()) if hour_key else ())
                )
                options.append(
                    MarkOption(
                        mark_id=facts.mark_id,
                        role='postal',
                        office_id=office.id,
                        year=year,
                        year_explicit=explicit,
                        month_day=month_day,
                        hour_key=hour_key,
                        sources=tuple(sources),
                        note=resolution.note,
                    )
                )
    # 同签名去重（不同抄录支撑同一读法时合并来源）
    merged: dict[tuple, MarkOption] = {}
    for opt in options:
        if opt.signature not in merged:
            merged[opt.signature] = opt
        else:
            old = merged[opt.signature]
            merged[opt.signature] = MarkOption(
                mark_id=old.mark_id, role=old.role, office_id=old.office_id,
                year=old.year, month_day=old.month_day, hour_key=old.hour_key,
                sources=tuple(sorted(set(old.sources) | set(opt.sources))),
                note=old.note or opt.note,
            )
    return list(merged.values())


def mark_options(facts: MarkFacts, catalog: Catalog, era) -> list[MarkOption]:
    """列出一枚戳迹所有仍可成立的角色/读法选项。"""

    postal = _postal_options(facts, catalog, era)

    # 只有当存在明确的非递送性释读（后人释注、纪念补盖等）时才给该角色
    non_delivery_sources: list[str] = []
    for flag, sources in facts.flags.items():
        if flag in {'纪念补盖', '后补盖', '后人释注', '归属待查'}:
            non_delivery_sources.extend(sources)
    options: list[MarkOption] = []
    if non_delivery_sources:
        forced = all(not item.is_delivery_evidence for item in facts.parsed)
        options.append(
            MarkOption(
                mark_id=facts.mark_id,
                role='non_delivery',
                sources=tuple(sorted(set(non_delivery_sources))),
                note='实物说明痕迹，不作实际递送证据' + ('（释读一致）' if forced else '（与邮戳释读并存）'),
            )
        )
        if forced:
            return options
    options.extend(postal)
    if not options:
        options.append(
            MarkOption(
                mark_id=facts.mark_id, role='unexplained',
                sources=tuple(r.reading.id for r in facts.parsed),
                note='无可落日期/局名的释读，暂不进入邮路时间线',
            )
        )
    return options


# ---------------------------------------------------------------------
# 路网走法
# ---------------------------------------------------------------------

def _edge_window_ok(route: Route, start: datetime, end: datetime) -> bool:
    edge_start = datetime.combine(route.open, datetime.min.time())
    edge_end = datetime.combine(route.close, datetime.min.time()).replace(hour=23, minute=59)
    return edge_start <= end and edge_end >= start


def _all_paths(
    catalog: Catalog, origin: str, destination: str,
    start: datetime, end: datetime,
) -> list[PathChoice]:
    """在开放路网上枚举两局之间所有可行简单路径（含未留戳中转）。"""

    results: list[PathChoice] = []

    def walk(current: str, edges: list[Route], visited: set[str], hours: int) -> None:
        if len(edges) >= MAX_PATH_DEPTH:
            return
        for route in catalog.routes.values():
            if route.origin != current or route.destination in visited:
                continue
            if not _edge_window_ok(route, start, end):
                continue
            new_hours = hours + route.min_hours
            if end < start or end - start < timedelta(hours=new_hours):
                continue
            new_edges = edges + [route]
            if route.destination == destination:
                runs = _count_return_runs(tuple(new_edges))
                offices = tuple([e.origin for e in new_edges] + [new_edges[-1].destination])
                results.append(
                    PathChoice(tuple(new_edges), offices, new_hours, runs)
                )
                continue
            visited.add(route.destination)
            walk(route.destination, new_edges, visited, new_hours)
            visited.remove(route.destination)

    if origin == destination:
        return results
    walk(origin, [], {origin}, 0)
    return results


def _count_return_runs(edges: tuple[Route, ...]) -> int:
    runs = 0
    in_run = False
    for edge in edges:
        is_return = edge.direction == '退件'
        if is_return and not in_run:
            runs += 1
        in_run = is_return
    return runs


def _split_trips(paths: list[PathChoice]) -> tuple[tuple[str, ...], ...]:
    """按退件段把完整路径切成 去程/退件/再寄 三趟。"""

    edges = [edge for path in paths for edge in path.edges]
    offices: list[str] = []
    for edge in edges:
        if not offices or offices[-1] != edge.origin:
            offices.append(edge.origin)
    if edges:
        offices.append(edges[-1].destination)

    trips: list[list[str]] = [[offices[0]]]
    prev_return = False
    for edge, office in zip(edges, offices[1:]):
        is_return = edge.direction == '退件'
        if (is_return and not prev_return) or (not is_return and prev_return):
            trips.append([])
        trips[-1].append(office)
        prev_return = is_return
    return tuple(tuple(trip) for trip in trips)


# ---------------------------------------------------------------------
# 枚举与核订
# ---------------------------------------------------------------------

def _node_window(option: MarkOption) -> tuple[datetime, datetime]:
    from datetime import date as _date

    day_obj = _date(option.year, option.month_day[0], option.month_day[1])
    if option.hour_key is None:
        start = datetime.combine(day_obj, datetime.min.time())
        return start, start.replace(hour=23, minute=59)
    if option.hour_key.isdigit():
        instant = datetime.combine(day_obj, datetime.min.time()).replace(
            hour=int(option.hour_key))
        return instant, instant
    if option.hour_key == '午后':
        return (
            datetime.combine(day_obj, datetime.min.time()).replace(hour=12),
            datetime.combine(day_obj, datetime.min.time()).replace(hour=17, minute=59),
        )
    start = datetime.combine(day_obj, datetime.min.time())
    return start, start.replace(hour=23, minute=59)


def _evaluate_combo(
    catalog: Catalog, era, cover_id: str, combo: tuple[MarkOption, ...]
) -> Optional[ExplanationRecord]:
    postal = [opt for opt in combo if opt.role == 'postal']
    nodes = [TimedNode(opt, _node_window(opt)) for opt in postal]
    nodes.sort(key=lambda node: node.window[0])

    # 全局年代一致性：释读未写年份的戳迹，必须落在同封明写年份锚点的
    # 合理时间窗内，不能借"只写月日"漂到另一年代的邮局体系里。
    anchor_dates = [
        _node_window(opt)[0].date() for opt in postal if opt.year_explicit
    ]
    if anchor_dates:
        for opt in postal:
            if opt.year_explicit:
                continue
            day_obj = _node_window(opt)[0].date()
            if all(abs((day_obj - anchor).days) > MAX_UNDATED_GAP_DAYS
                   for anchor in anchor_dates):
                return None

    paths: list[PathChoice] = []
    total_return_runs = 0
    for left, right in zip(nodes, nodes[1:]):
        # 两端年份都不是释读明写时，禁止把同封戳迹跨年拉长
        if not left.option.year_explicit and not right.option.year_explicit:
            if (right.window[1].date() - left.window[0].date()).days > MAX_UNDATED_GAP_DAYS:
                return None
        choices = _all_paths(
            catalog, left.office_id, right.office_id,
            left.window[0], right.window[1],
        )
        if not choices:
            return None
        # 时间窗口能否容纳：最晚到达 - 最早出发
        budget = right.window[1] - left.window[0]
        choices = [c for c in choices if timedelta(hours=c.total_min_hours) <= budget]
        if not choices:
            return None
        # 任何走法都不得制造第二段退回；同等条件下取退回少、里程短者
        choices = [c for c in choices if total_return_runs + c.return_runs <= MAX_RETURN_RUNS]
        if not choices:
            return None
        chosen = min(choices, key=lambda c: (c.return_runs, c.total_min_hours))
        paths.append(chosen)
        total_return_runs += chosen.return_runs

    if paths:
        trips = _split_trips(paths)
    elif nodes:
        trips = (tuple(n.office_id for n in nodes),)
    else:
        trips = ()

    issue_date = era.issue_date_for(catalog.covers[cover_id].image)
    first_day = False
    if issue_date and nodes:
        origin = nodes[0].option
        first_day = (
            origin.year is not None and origin.month_day is not None
            and datetime.combine(
                datetime.min.replace(
                    year=origin.year, month=origin.month_day[0],
                    day=origin.month_day[1],
                ).date(),
                datetime.min.time(),
            ).date() == issue_date
        )

    return ExplanationRecord(
        index=0,
        mark_choices={opt.mark_id: opt for opt in combo},
        timeline=tuple(nodes),
        paths=tuple(paths),
        trips=trips,
        return_runs=total_return_runs,
        first_day=first_day,
    )


def verify_cover(
    catalog: Catalog,
    era,
    facts_by_mark: dict[str, MarkFacts],
    cover_id: str,
) -> CoverVerification:
    """核订单件封：枚举全部可行解释并汇总确认/分歧/复查点。"""

    marks = catalog.marks_of(cover_id)
    result = CoverVerification(cover_id=cover_id)

    option_lists: list[list[MarkOption]] = []
    for mark in marks:
        option_lists.append(mark_options(facts_by_mark[mark.id], catalog, era))

    seen_signatures: set[tuple] = set()
    records: list[ExplanationRecord] = []
    for combo in product(*option_lists):
        signature = tuple(sorted((opt.mark_id, opt.signature) for opt in combo))
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        record = _evaluate_combo(catalog, era, cover_id, combo)
        if record is not None:
            record = ExplanationRecord(
                index=len(records) + 1,
                mark_choices=record.mark_choices,
                timeline=record.timeline,
                paths=record.paths,
                trips=record.trips,
                return_runs=record.return_runs,
                first_day=record.first_day,
            )
            records.append(record)

    result.feasible = records
    if not records:
        result.contradictions.append(
            '没有任何完整递送解释能同时满足戳迹、邮局开闭、路段时间与先后关系；'
            '不得挑选单一行程消除矛盾，需复查原字或历史资料'
        )
        _summarize_physical_notes(result, marks, facts_by_mark)
        result.first_day_status = 'pending'
        return result

    _summarize_marks(result, marks, facts_by_mark, records, catalog, era)
    _summarize_edges(result, records)
    _summarize_first_day(result, records, catalog, cover_id, era)
    _summarize_returns(result, records)
    _build_pivots(result, marks, facts_by_mark, records, catalog, era)
    _build_transit_guards(result, records)
    _summarize_physical_notes(result, marks, facts_by_mark)
    return result


def _summarize_physical_notes(
    result: CoverVerification, marks: list[Mark], facts_by_mark: dict[str, MarkFacts]
) -> None:
    for mark in marks:
        facts = facts_by_mark[mark.id]
        for flag, sources in sorted(facts.flags.items()):
            if flag in {'纪念补盖', '后补盖', '后人释注', '归属待查'}:
                result.physical_notes.append(
                    {'mark': mark.id, 'side': mark.side, 'flag': flag,
                     'sources': sources,
                     'texts': [p.reading.text for p in facts.parsed
                               if flag in p.flags]}
                )


def _summarize_marks(
    result: CoverVerification, marks: list[Mark],
    facts_by_mark: dict[str, MarkFacts], records: list[ExplanationRecord],
    catalog: Catalog, era,
) -> None:
    for mark in marks:
        choices = [rec.mark_choices[mark.id] for rec in records]
        signatures = {opt.signature for opt in choices}
        facts = facts_by_mark[mark.id]
        entry = {
            'mark': mark.id,
            'side': mark.side,
            'order_hint': mark.order_hint,
            'readings': [p.reading.id for p in facts.parsed],
            'alternatives': sorted({_describe_option(opt, catalog) for opt in choices}),
        }
        if len(signatures) == 1:
            opt = choices[0]
            entry['status'] = 'confirmed' if opt.role == 'postal' else opt.role
            entry['office'] = opt.office_id
            entry['date'] = _option_date_text(opt)
            result.confirmed_marks.append(entry)
        else:
            entry['status'] = 'disputed'
            result.disputed_marks.append(entry)


def _describe_option(opt: MarkOption, catalog: Catalog) -> str:
    if opt.role != 'postal':
        return f'{opt.role}（{opt.note}）'
    office = catalog.offices[opt.office_id]
    date_text = _option_date_text(opt)
    hour = f' {opt.hour_key}时' if opt.hour_key and opt.hour_key.isdigit() else (
        f' {opt.hour_key}' if opt.hour_key else '')
    return f'{office.name} {date_text}{hour}'


def _option_date_text(opt: MarkOption) -> str:
    if opt.month_day is None:
        return '日期残缺'
    year = f'{opt.year}年' if opt.year is not None else '年份未定'
    return f'{year}{opt.month_day[0]}月{opt.month_day[1]}日'


def _summarize_edges(result: CoverVerification, records: list[ExplanationRecord]) -> None:
    edge_sets = [
        tuple(sorted((e.origin, e.destination, e.id) for path in rec.paths for e in path.edges))
        for rec in records
    ]
    common = set(edge_sets[0])
    for edge_set in edge_sets[1:]:
        common &= set(edge_set)
    for edge_set in edge_sets:
        for origin, destination, route_id in edge_set:
            entry = {'route': route_id, 'from': origin, 'to': destination}
            if (origin, destination, route_id) in common:
                if entry not in result.confirmed_edges:
                    result.confirmed_edges.append(entry)
            elif entry not in result.disputed_edges:
                result.disputed_edges.append(entry)


def _summarize_first_day(
    result: CoverVerification, records: list[ExplanationRecord],
    catalog: Catalog, cover_id: str, era,
) -> None:
    issue = era.issue_date_for(catalog.covers[cover_id].image)
    if issue is None:
        result.first_day_status = 'pending'
        result.first_day_reason = '馆内年表未载该图样启用日，无法核首日'
        return
    votes = {rec.first_day for rec in records}
    if votes == {True}:
        result.first_day_status = 'confirmed'
        result.first_day_reason = (
            f'所有可行解释的始发戳日期均为图样启用日 {issue.isoformat()}'
        )
    elif votes == {False}:
        result.first_day_status = 'refuted'
        result.first_day_reason = (
            f'没有任何可行解释在图样启用日 {issue.isoformat()} 留有始发邮政戳；'
            '纪念补盖与后写注记不计入递送证据'
        )
    else:
        result.first_day_status = 'pending'
        result.first_day_reason = '仅部分可行解释满足首日实寄，需先解决分歧'


def _summarize_returns(result: CoverVerification, records: list[ExplanationRecord]) -> None:
    runs = {rec.return_runs for rec in records}
    if runs == {0}:
        result.return_status = 'none'
    elif 0 in runs:
        result.return_status = 'disputed'
    else:
        result.return_status = 'one_return_confirmed'


def _build_pivots(
    result: CoverVerification, marks: list[Mark],
    facts_by_mark: dict[str, MarkFacts], records: list[ExplanationRecord],
    catalog: Catalog, era,
) -> None:
    for mark in marks:
        facts = facts_by_mark[mark.id]
        choices = [rec.mark_choices[mark.id] for rec in records]
        if len({opt.signature for opt in choices}) <= 1:
            continue
        effects: set[str] = set()
        roles = {opt.role for opt in choices}
        if len(roles) > 1:
            effects.add('该痕迹是否计入实际邮路（退回/再寄结构）')
        if len({opt.office_id for opt in choices}) > 1:
            effects.add('路线经过的邮局')
        if len({opt.hour_key for opt in choices}) > 1:
            effects.add('路段通达时间能否成立')
        if len({opt.year for opt in choices}) > 1:
            effects.add('“首日实寄”归类与邮局年代定局')
        if not effects:
            effects.add('可行解释的具体行程')
        result.pivots.append(
            Pivot(
                mark_id=mark.id,
                field='、'.join(sorted(facts.uncertain_fields)) or '角色/读法',
                alternatives=sorted({_describe_option(opt, catalog) for opt in choices}),
                reading_sources=[p.reading.id for p in facts.parsed],
                effect='；'.join(sorted(effects)),
            )
        )


def _node_trip_indices(record: ExplanationRecord) -> list[int]:
    """按每个留戳节点跨越的边数与退件边位置确定趟次。

    路径中可经过未留戳的中转局，因此趟次归属按边遍历计算，
    不把中转局当成时间线节点。
    """

    flat_edges = [edge for path in record.paths for edge in path.edges]
    boundaries = {0}
    for edge_index, edge in enumerate(flat_edges):
        prev_return = flat_edges[edge_index - 1].direction == '退件' if edge_index else False
        this_return = edge.direction == '退件'
        if edge_index and prev_return != this_return:
            boundaries.add(edge_index)

    def trip_at_edge(edge_index: int) -> int:
        return sum(1 for boundary in boundaries if boundary <= edge_index) - 1

    indices = [0]
    edge_cursor = 0
    for path in record.paths:
        edge_cursor += len(path.edges)
        indices.append(trip_at_edge(edge_cursor - 1))
    return indices[:len(record.timeline)]


def _build_transit_guards(
    result: CoverVerification, records: list[ExplanationRecord]
) -> None:
    guards = {(g.from_mark, g.to_mark): g for g in result.transit_guards}
    for rec in records:
        if rec.return_runs == 0 or len(rec.timeline) < 2:
            continue
        trip_indices = _node_trip_indices(rec)
        for i, left in enumerate(rec.timeline):
            for j in range(i + 1, len(rec.timeline)):
                if trip_indices[j] != trip_indices[i]:
                    right = rec.timeline[j]
                    key = (left.mark_id, right.mark_id)
                    if key not in guards:
                        guards[key] = TransitGuard(
                            from_mark=key[0],
                            to_mark=key[1],
                            reason='两戳分属退回前后的不同趟次，其日期差不能作为普通通达时间',
                        )
                    break
    result.transit_guards = list(guards.values())


def verify_all(catalog: Catalog, era) -> dict[str, CoverVerification]:
    facts_by_mark = all_mark_facts(catalog)
    return {
        cover_id: verify_cover(catalog, era, facts_by_mark, cover_id)
        for cover_id in catalog.covers
    }
