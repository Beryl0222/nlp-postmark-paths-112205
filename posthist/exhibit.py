"""展陈草稿核证、换展差异分析与展签稿生成。

展签必须根据柜中实际封件各自的证据来写，区分三层：

- 已核实的递送（所有可行解释的交集）；
- 题签/展笺上的说法（作为说法呈现，不冒充事实）；
- 未知段与实物说明（纪念补盖只留作实物说明）。

两件展品共同解释一种邮路时，必须两件都支持且年代相容；
换入同图另一件后，结论按新封自己的证据重新裁决。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .engine import CoverVerification, ExplanationRecord, verify_all
from .models import Catalog, DisplayCase


@dataclass
class ClaimFinding:
    """草稿中一个说法在单件封上的裁决。"""

    cover_id: str
    claim_type: str
    claim_text: str
    status: str  # supported | pending | unsupported | refuted
    detail: str


@dataclass
class BlockingReason:
    """使某个说法从可讲变为悬而未决/不能讲的具体原因。"""

    cover_id: str
    kind: str  # era | route | timing | first_day | mark | guard
    detail: str
    reading_sources: list[str] = field(default_factory=list)
    office_id: Optional[str] = None
    route_id: Optional[str] = None
    mark_id: Optional[str] = None


@dataclass
class DisplayVerdict:
    """一组展陈草稿的核证结论。"""

    display_id: str
    draft: str
    claims: list[dict]
    findings: list[ClaimFinding]
    blockers: list[BlockingReason]
    overall: str  # supported | pending | unsupported
    era_conflict: bool


@dataclass
class CoverLabel:
    """单件封的扫码说明稿。"""

    cover_id: str
    image: str
    verified: list[str]
    insert_claims: list[str]
    unknown: list[str]
    physical_notes: list[str]
    first_day_line: str


# ---------------------------------------------------------------------
# 草稿解析
# ---------------------------------------------------------------------

def _office_tokens_in_text(catalog: Catalog, text: str) -> list[str]:
    """按称谓在草稿中出现的先后返回，全名优先于短别名。"""

    hits: list[tuple[int, str]] = []
    for office in catalog.offices.values():
        candidates = [office.name, *office.aliases]
        for token in candidates:
            pos = text.find(token)
            if pos >= 0:
                hits.append((pos, token))
    # 同位置时全名（较长者）优先，再按出现位置排序
    hits.sort(key=lambda item: (item[0], -len(item[1])))
    ordered: list[str] = []
    occupied: list[tuple[int, int]] = []
    for pos, token in hits:
        span = (pos, pos + len(token))
        if any(not (span[1] <= start or span[0] >= end) for start, end in occupied):
            continue
        occupied.append(span)
        ordered.append(token)
    return ordered


def parse_draft_claims(catalog: Catalog, draft: str) -> list[dict]:
    """把说明草稿拆成可核的说法，原文说法逐字保留。"""

    claims: list[dict] = []
    if '首日实寄' in draft:
        claims.append({'type': 'first_day', 'text': '首日实寄'})
    if '当晚就到' in draft or '当日' in draft:
        claims.append({'type': 'timing', 'text': '当晚就到（普通通达时间）'})
    tokens = _office_tokens_in_text(catalog, draft)
    if len(tokens) >= 2:
        claims.append({
            'type': 'route',
            'text': '经 ' + '、'.join(tokens) + ' 的邮路',
            'tokens': tokens,
        })
    if '相互印证' in draft:
        claims.append({'type': 'corroboration', 'text': '两件展品相互印证同一邮路'})
    return claims


# ---------------------------------------------------------------------
# 单件封层面的证据查询
# ---------------------------------------------------------------------

def _office_name(catalog: Catalog, office_id: str) -> str:
    office = catalog.offices.get(office_id)
    return office.name if office else office_id


def confirmed_route_edge_ids(verification: CoverVerification) -> set[str]:
    return {edge['route'] for edge in verification.confirmed_edges}


def disputed_route_edge_ids(verification: CoverVerification) -> set[str]:
    return {edge['route'] for edge in verification.disputed_edges}


def explanation_edge_ids(record: ExplanationRecord) -> set[str]:
    return {edge.id for path in record.paths for edge in path.edges}


def explanation_offices(record: ExplanationRecord) -> list[str]:
    offices: list[str] = []
    for trip in record.trips:
        for office in trip:
            if not offices or offices[-1] != office:
                offices.append(office)
    return offices


def node_date_span(verification: CoverVerification) -> tuple[Optional[str], Optional[str]]:
    if not verification.feasible:
        return None, None
    starts, ends = [], []
    for record in verification.feasible:
        for node in record.timeline:
            opt = node.option
            if opt.year is None:
                continue
            starts.append(f'{opt.year}-{opt.month_day[0]:02d}-{opt.month_day[1]:02d}')
            ends.append(starts[-1])
    return (min(starts) if starts else None), (max(ends) if ends else None)


# ---------------------------------------------------------------------
# 说法核证
# ---------------------------------------------------------------------

def _adjudicate_first_day(
    catalog: Catalog, verification: CoverVerification
) -> ClaimFinding:
    status_map = {'confirmed': 'supported', 'refuted': 'refuted', 'pending': 'pending'}
    return ClaimFinding(
        cover_id=verification.cover_id,
        claim_type='first_day',
        claim_text='首日实寄',
        status=status_map[verification.first_day_status],
        detail=verification.first_day_reason or '证据不足',
    )


def _draft_token_order(catalog: Catalog, tokens: list[str]) -> list[str]:
    """把草稿里的称谓按出现顺序换成邮局 id（同简称多局时全部保留）。"""

    ordered: list[str] = []
    for token in tokens:
        offices = [
            office.id for office in catalog.offices.values()
            if office.name == token or token in office.aliases
        ]
        ordered.extend(offices)
    return ordered


def _record_office_sequence(record: ExplanationRecord) -> list[str]:
    offices: list[str] = []
    for trip in record.trips:
        for office in trip:
            if not offices or offices[-1] != office:
                offices.append(office)
    return offices


def _sequence_contains(sequence: list[str], wanted: list[str]) -> bool:
    """wanted 的局序（允许同名多局的任一选择）按顺序出现在 sequence 中。"""

    cursor = 0
    for target in wanted:
        found = -1
        for pos in range(cursor, len(sequence)):
            if sequence[pos] == target:
                found = pos
                break
        if found < 0:
            return False
        cursor = found + 1
    return True


def _adjudicate_route(
    catalog: Catalog, verification: CoverVerification, claim: dict
) -> tuple[ClaimFinding, list[BlockingReason]]:
    blockers: list[BlockingReason] = []
    if not verification.feasible:
        return (
            ClaimFinding(verification.cover_id, 'route', claim['text'],
                         'pending', '该封无可行递送解释'),
            blockers,
        )

    # 同一简称在不同年代分属不同邮局：优先找一条把该称谓定局到
    # 单局的解释；找不到再按年代窗口过滤
    lo, hi = node_date_span(verification)
    wanted: list[str] = []
    for token in claim['tokens']:
        alias_offices = [
            office.id for office in catalog.offices.values()
            if office.name == token or token in office.aliases
        ]
        chosen: list[str] = []
        for record in verification.feasible:
            record_offices = {node.office_id for node in record.timeline}
            settled = [oid for oid in alias_offices if oid in record_offices]
            if len(settled) == 1:
                chosen = settled
                break
        if not chosen:
            chosen = [
                oid for oid in alias_offices
                if not lo or (
                    catalog.offices[oid].open.isoformat() <= hi
                    and catalog.offices[oid].close.isoformat() >= lo
                )
            ]
        for office_id in chosen:
            if office_id not in wanted:
                wanted.append(office_id)
    if not wanted:
        wanted = _draft_token_order(catalog, claim['tokens'])

    confirmed = confirmed_route_edge_ids(verification)

    def chain_through(edge_pool: set[str], nodes: list[str]) -> bool:
        """草稿局序能否用给定路段池（允许经过未留戳中转局）走通。"""
        if len(nodes) < 2:
            return True
        for start, goal in zip(nodes, nodes[1:]):
            reachable = {start}
            frontier = [start]
            while frontier:
                current = frontier.pop()
                for route in catalog.routes.values():
                    if route.id not in edge_pool or route.origin != current:
                        continue
                    if route.destination not in reachable:
                        reachable.add(route.destination)
                        frontier.append(route.destination)
            if goal not in reachable:
                return False
        return True

    # 先用所有解释共同成立的路段核主脉（退件分歧不拖累主脉）
    if chain_through(confirmed, wanted):
        return (
            ClaimFinding(verification.cover_id, 'route', claim['text'],
                         'supported',
                         '草稿局序可由所有可行解释共同成立的路段连通'),
            blockers,
        )

    supported_records = [
        record for record in verification.feasible
        if _sequence_contains(_record_office_sequence(record), wanted)
    ]
    if supported_records:
        return (
            ClaimFinding(verification.cover_id, 'route', claim['text'],
                         'pending',
                         '草稿局序只在部分可行解释中走通，需先解决戳迹分歧'),
            blockers,
        )

    actual = sorted({
        office
        for record in verification.feasible
        for office in _record_office_sequence(record)
    })
    detail = (
        '该封所有可行解释都不按草稿局序走；实际出现的局为 '
        + '、'.join(_office_name(catalog, office) for office in actual)
    )
    blockers.append(BlockingReason(
        cover_id=verification.cover_id, kind='route', detail=detail,
    ))
    return (
        ClaimFinding(verification.cover_id, 'route', claim['text'],
                     'unsupported', detail),
        blockers,
    )


def _adjudicate_timing(
    catalog: Catalog, verification: CoverVerification
) -> tuple[ClaimFinding, list[BlockingReason]]:
    blockers: list[BlockingReason] = []
    if not verification.feasible:
        return (
            ClaimFinding(verification.cover_id, 'timing',
                         '当晚就到（普通通达时间）', 'pending', '无可行解释'),
            blockers,
        )

    guarded_pairs = {(g.from_mark, g.to_mark) for g in verification.transit_guards}
    same_day_ok = False
    for record in verification.feasible:
        mark_ids = [node.mark_id for node in record.timeline]
        for i, left in enumerate(record.timeline):
            for right in record.timeline[i + 1:]:
                if (left.mark_id, right.mark_id) in guarded_pairs:
                    continue
                if left.window[0].date() == right.window[1].date():
                    same_day_ok = True
                    break

    if same_day_ok:
        return (
            ClaimFinding(verification.cover_id, 'timing',
                         '当晚就到（普通通达时间）', 'supported',
                         '存在同趟同日可达的节点对'),
            blockers,
        )
    detail = '同趟节点之间均跨日；同日戳若存在也分属退回前后，不能证明普通通达时间'
    blockers.append(BlockingReason(
        cover_id=verification.cover_id, kind='timing', detail=detail,
    ))
    return (
        ClaimFinding(verification.cover_id, 'timing',
                     '当晚就到（普通通达时间）', 'unsupported', detail),
        blockers,
    )


def _era_conflict(
    catalog: Catalog,
    verdicts: dict[str, CoverVerification],
    relevant_offices: Optional[set[str]] = None,
) -> tuple[bool, list[BlockingReason]]:
    """两件都在场却分属不相容年代。

    只检查草稿共同援引的局（``relevant_offices``）相关路段：一条路段
    若在某封的戳迹年代内根本不开放（无论该封解释是否走到它），
    即构成年代不相容。
    """

    reasons: list[BlockingReason] = []
    seen: set[tuple] = set()
    referenced_routes = set()
    for route in catalog.routes.values():
        if relevant_offices is None or (
            route.origin in relevant_offices and route.destination in relevant_offices
        ):
            referenced_routes.add(route.id)

    for cover_id, verification in verdicts.items():
        lo, hi = node_date_span(verification)
        if not lo:
            continue
        for route_id in sorted(referenced_routes):
            route = catalog.routes[route_id]
            if route.open.isoformat() <= hi and route.close.isoformat() >= lo:
                continue
            key = (cover_id, route_id, lo, hi)
            if key in seen:
                continue
            seen.add(key)
            reasons.append(BlockingReason(
                cover_id=cover_id, kind='era',
                detail=(
                    f'路段 {route_id}（{_office_name(catalog, route.origin)}→'
                    f'{_office_name(catalog, route.destination)}）开放于 '
                    f'{route.open.isoformat()}..{route.close.isoformat()}，'
                    f'与该封戳迹年代 {lo}..{hi} 不相容'
                ),
                route_id=route_id,
            ))
    return bool(reasons), reasons


def _adjudicate_corroboration(
    catalog: Catalog,
    local_verdicts: dict[str, CoverVerification],
    claim: dict,
) -> tuple[ClaimFinding, list[BlockingReason]]:
    """两件共同解释一种邮路：共同的已核实路段与相容年代缺一不可。"""

    blockers: list[BlockingReason] = []
    per_cover_confirmed = {
        cover_id: confirmed_route_edge_ids(verification)
        for cover_id, verification in local_verdicts.items()
    }
    common: set[str] = set.intersection(*per_cover_confirmed.values()) \
        if per_cover_confirmed else set()
    relevant = set()
    for edge_ids in per_cover_confirmed.values():
        for route_id in edge_ids:
            relevant.add(catalog.routes[route_id].origin)
            relevant.add(catalog.routes[route_id].destination)
    era_conflict, era_reasons = _era_conflict(
        catalog, local_verdicts, relevant_offices=relevant or None)
    blockers.extend(era_reasons)

    if era_conflict:
        detail = '两件展品分属不相容年代，无法共同解释同一邮路'
        for cover_id in local_verdicts:
            return (
                ClaimFinding(cover_id, 'corroboration', claim['text'],
                             'unsupported', detail),
                blockers,
            )
    if common:
        names = '、'.join(sorted(common))
        return (
            ClaimFinding(next(iter(local_verdicts)), 'corroboration',
                         claim['text'], 'supported',
                         f'两件在所有可行解释中共同经过路段 {names}'),
            blockers,
        )
    detail = '两件没有共同的、在所有可行解释中都成立的路段，互证不成立'
    for cover_id in local_verdicts:
        blockers.append(BlockingReason(
            cover_id=cover_id, kind='route', detail=detail,
        ))
    return (
        ClaimFinding(next(iter(local_verdicts)), 'corroboration',
                     claim['text'], 'unsupported', detail),
        blockers,
    )


def _claim_offices(catalog: Catalog, claims: list[dict]) -> Optional[set[str]]:
    offices: set[str] = set()
    found = False
    for claim in claims:
        if claim['type'] != 'route':
            continue
        found = True
        for token in claim.get('tokens', ()):
            for office in catalog.offices.values():
                if office.name == token or token in office.aliases:
                    offices.add(office.id)
    return offices if found else None


def _dedupe_blockers(blockers: list[BlockingReason]) -> list[BlockingReason]:
    seen: set[tuple] = set()
    unique: list[BlockingReason] = []
    for blocker in blockers:
        key = (blocker.cover_id, blocker.kind, blocker.detail,
               blocker.route_id, blocker.mark_id, tuple(blocker.reading_sources))
        if key in seen:
            continue
        seen.add(key)
        unique.append(blocker)
    return unique


def adjudicate_display(
    catalog: Catalog,
    display: DisplayCase,
    verdicts: dict[str, CoverVerification],
) -> DisplayVerdict:
    """核证一组展陈草稿。"""

    claims = parse_draft_claims(catalog, display.draft)
    findings: list[ClaimFinding] = []
    blockers: list[BlockingReason] = []
    local_verdicts = {cover_id: verdicts[cover_id] for cover_id in display.covers}

    for claim in claims:
        if claim['type'] == 'corroboration':
            # 互证是展陈级说法，只裁决一次
            finding, local_blockers = _adjudicate_corroboration(
                catalog, local_verdicts, claim)
            findings.append(finding)
            blockers.extend(local_blockers)
            continue
        for cover_id in display.covers:
            verification = local_verdicts[cover_id]
            if claim['type'] == 'first_day':
                findings.append(_adjudicate_first_day(catalog, verification))
            elif claim['type'] == 'route':
                finding, local_blockers = _adjudicate_route(
                    catalog, verification, claim)
                findings.append(finding)
                blockers.extend(local_blockers)
            elif claim['type'] == 'timing':
                finding, local_blockers = _adjudicate_timing(
                    catalog, verification)
                findings.append(finding)
                blockers.extend(local_blockers)

    relevant_offices = _claim_offices(catalog, claims)
    era_conflict = False
    era_reasons: list[BlockingReason] = []
    if relevant_offices is not None:
        era_conflict, era_reasons = _era_conflict(
            catalog, local_verdicts, relevant_offices=relevant_offices
        )
    blockers.extend(era_reasons)
    blockers = _dedupe_blockers(blockers)

    statuses = {f.status for f in findings}
    if era_conflict or 'unsupported' in statuses or 'refuted' in statuses:
        overall = 'unsupported'
    elif 'pending' in statuses:
        overall = 'pending'
    else:
        overall = 'supported'

    return DisplayVerdict(
        display_id=display.id,
        draft=display.draft,
        claims=claims,
        findings=findings,
        blockers=blockers,
        overall=overall,
        era_conflict=era_conflict,
    )


def adjudicate_all(
    catalog: Catalog, era
) -> tuple[dict[str, CoverVerification], dict[str, DisplayVerdict]]:
    verdicts = verify_all(catalog, era)
    display_verdicts = {
        display_id: adjudicate_display(catalog, display, verdicts)
        for display_id, display in catalog.displays.items()
    }
    return verdicts, display_verdicts


# ---------------------------------------------------------------------
# 展签稿
# ---------------------------------------------------------------------

def build_label(catalog: Catalog, verification: CoverVerification) -> CoverLabel:
    cover = catalog.covers[verification.cover_id]

    verified: list[str] = []
    for mark in verification.confirmed_marks:
        verified.append(
            f"{mark['mark']}（{mark['side']}面）：{'；'.join(mark['alternatives'])}"
        )
    for edge in verification.confirmed_edges:
        verified.append(
            f"路段 {edge['route']}：{_office_name(catalog, edge['from'])}→"
            f"{_office_name(catalog, edge['to'])} 在所有可行解释中均成立"
        )
    if verification.return_status == 'one_return_confirmed':
        verified.append('所有可行解释都包含一次退回后再寄')
    if verification.transit_guards:
        pairs = '、'.join(f"{g.from_mark}→{g.to_mark}" for g in verification.transit_guards)
        verified.append(f'跨趟节点对 {pairs} 的日期差不得作为普通通达时间')

    insert_claims = [f"题签/展笺原文：{cover.insert}"]

    unknown: list[str] = []
    for mark in verification.disputed_marks:
        unknown.append(
            f"{mark['mark']} 尚存分歧：{' ／ '.join(mark['alternatives'])}"
            f"（复查抄录 {','.join(mark['readings'])} 的原字）"
        )
    for edge in verification.disputed_edges:
        unknown.append(
            f"路段 {edge['route']}（{_office_name(catalog, edge['from'])}→"
            f"{_office_name(catalog, edge['to'])}）仅部分解释成立"
        )
    for pivot in verification.pivots:
        unknown.append(
            f"复查点 {pivot.mark_id}（{pivot.field}）：改读后会影响{pivot.effect}"
        )

    physical: list[str] = []
    for note in verification.physical_notes:
        physical.append(
            f"{note['mark']}（{note['side']}面）带“{note['flag']}”标记："
            f"{'；'.join(note['texts'])}；仅作物件说明，排除其递送证据资格"
        )

    first_day_map = {
        'confirmed': '“首日实寄”可讲：' + verification.first_day_reason,
        'refuted': '“首日实寄”不能讲：' + verification.first_day_reason,
        'pending': '“首日实寄”悬而未决：' + verification.first_day_reason,
    }
    return CoverLabel(
        cover_id=cover.id,
        image=cover.image,
        verified=verified,
        insert_claims=insert_claims,
        unknown=unknown,
        physical_notes=physical,
        first_day_line=first_day_map[verification.first_day_status],
    )


# ---------------------------------------------------------------------
# 换展分析
# ---------------------------------------------------------------------

@dataclass
class SwapReport:
    """换入同图另一件后，某个说法为何从可讲变成悬而未决。"""

    display_id: str
    out_cover: str
    in_cover: str
    claim_text: str
    status_before: str
    status_after: str
    blockers: list[BlockingReason]
    changed: bool


def analyze_swap(
    catalog: Catalog,
    display: DisplayCase,
    out_cover: str,
    in_cover: str,
    verdicts: dict[str, CoverVerification],
) -> list[SwapReport]:
    """逐说法比较换展前后的裁决，并点名涉及的封、释读与年代限制。"""

    claims = parse_draft_claims(catalog, display.draft)
    reports: list[SwapReport] = []

    swapped_display = DisplayCase(
        id=display.id,
        covers=tuple(in_cover if c == out_cover else c for c in display.covers),
        time=display.time,
        draft=display.draft,
    )
    before = adjudicate_display(catalog, display, verdicts)
    after = adjudicate_display(catalog, swapped_display, verdicts)

    # 每类说法只看与自己有关的阻断原因，避免把年代问题堆到首日说法上
    relevant_kinds = {
        'first_day': {'first_day', 'mark'},
        'route': {'route', 'era', 'mark'},
        'timing': {'timing', 'guard', 'mark'},
        'corroboration': {'route', 'era', 'mark'},
    }
    for claim in claims:
        before_findings = [f for f in before.findings
                           if f.claim_type == claim['type'] and f.cover_id == out_cover]
        after_findings = [f for f in after.findings
                          if f.claim_type == claim['type'] and f.cover_id == in_cover]
        status_before = before_findings[0].status if before_findings else 'pending'
        status_after = after_findings[0].status if after_findings else 'pending'

        kinds = relevant_kinds.get(claim['type'], set())
        blockers = [b for b in after.blockers
                    if b.cover_id == in_cover and b.kind in kinds]
        for pivot in verdicts[in_cover].pivots:
            blockers.append(BlockingReason(
                cover_id=in_cover, kind='mark',
                detail=f"复查 {pivot.mark_id} 原字（抄录 {','.join(pivot.reading_sources)}）"
                       f"可改变：{pivot.effect}",
                mark_id=pivot.mark_id,
                reading_sources=pivot.reading_sources,
            ))
        for note in verdicts[in_cover].physical_notes:
            blockers.append(BlockingReason(
                cover_id=in_cover, kind='mark',
                detail=f"{note['mark']} 为“{note['flag']}”，不具递送证据资格",
                mark_id=note['mark'], reading_sources=note['sources'],
            ))
        blockers = _dedupe_blockers(blockers)

        reports.append(SwapReport(
            display_id=display.id,
            out_cover=out_cover,
            in_cover=in_cover,
            claim_text=claim['text'],
            status_before=status_before,
            status_after=status_after,
            blockers=blockers,
            changed=status_before != status_after,
        ))
    return reports
