"""把抄录人的原始释读解析到具体戳迹。

关键约定：

- 同一物理戳迹（mark）上的多份抄录不会被"投票合并"。每位抄录的
  说法都带着来源保留；某一位数字只有在所有可成立的释读里都相同
  时才可称确定，票数不能把存疑数字变成定论。
- "后补盖/纪念补盖""退回""所称/疑是始发""后人释注""叠盖"等
  短语都解释到其所在戳迹，成为该戳迹的标记，而不是全封结论。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .dates import DateExpression, DateParseError, parse_date_text
from .models import Catalog, Mark, Reading


# 短语 -> 戳迹标记。匹配顺序即优先级，先匹配更具体的说法。
_FLAG_PHRASES: tuple[tuple[str, str], ...] = (
    ('纪念后补盖', '纪念补盖'),
    ('后补盖', '后补盖'),
    ('补盖', '后补盖'),
    ('后人的', '后人释注'),
    ('后人', '后人释注'),
    ('退回', '退回字样'),
    ('退后再寄', '称退后再寄'),
    ('可能是始发', '所称寄地'),
    ('疑是始发', '所称寄地'),
    ('盖在一起', '叠盖候选'),
    ('叠', '叠盖候选'),
    ('方向与其他墨不同', '墨色方向异'),
    ('颜色与同封展笺铅字一致', '同展笺字迹'),
    ('归属待查', '归属待查'),
)

# 把握程度 -> 抄录人明确不放心的字段
_CERTAINTY_FIELD: dict[str, tuple[str, ...]] = {
    '清楚': (),
    '月日清楚': ('time',),
    '小时存疑': ('hour',),
    '时刻存疑': ('hour',),
    '日期清楚': ('action',),
    '动作存疑': ('action',),
    '年份存疑': ('year',),
    '归属待查': ('attribution',),
}

# 整条释读若带这些词，就不能当作实际递送证据
_NON_DELIVERY_FLAGS = frozenset({'纪念补盖', '后补盖', '后人释注', '归属待查'})
_RETURN_MARK_FLAGS = frozenset({'退回字样'})


@dataclass(frozen=True)
class ParsedReading:
    """单条抄录的结构化结果。"""

    reading: Reading
    date_expr: Optional[DateExpression]
    office_token: Optional[str]
    flags: frozenset[str]
    uncertain_fields: frozenset[str]
    parse_issue: Optional[str] = None

    @property
    def is_delivery_evidence(self) -> bool:
        return not (self.flags & _NON_DELIVERY_FLAGS)

    @property
    def is_return_note(self) -> bool:
        return bool(self.flags & _RETURN_MARK_FLAGS)


@dataclass
class MarkFacts:
    """同一物理戳迹上所有释读的并集（保留分歧，不投票）。"""

    mark: Mark
    parsed: list[ParsedReading]

    # 按字段聚合的候选，值是 (候选值, 来源抄录 id 列表)
    office_candidates: dict[str, list[str]] = field(default_factory=dict)
    year_candidates: dict[str, list[str]] = field(default_factory=dict)
    month_day_candidates: dict[tuple[int, int], list[str]] = field(default_factory=dict)
    hour_candidates: dict[str, list[str]] = field(default_factory=dict)
    flags: dict[str, list[str]] = field(default_factory=dict)
    uncertain_fields: set[str] = field(default_factory=set)
    parse_issues: list[str] = field(default_factory=list)

    @property
    def mark_id(self) -> str:
        return self.mark.id

    @property
    def is_non_delivery(self) -> bool:
        """有明确实物标记时，该痕迹整体不得作为实际递送证据。"""

        return bool(self.flags.keys() & _NON_DELIVERY_FLAGS)

    @property
    def has_return_note(self) -> bool:
        return bool(self.flags.keys() & _RETURN_MARK_FLAGS)

    @property
    def has_overlap(self) -> bool:
        return '叠盖候选' in self.flags

    def provenance(self, candidate_map: dict, value) -> tuple[str, ...]:
        return tuple(candidate_map.get(value, ()))


def _extract_flags(text: str) -> frozenset[str]:
    found: set[str] = set()
    for phrase, flag in _FLAG_PHRASES:
        if phrase in text:
            found.add(flag)
    return frozenset(found)


def _extract_office_token(text: str, catalog: Catalog) -> Optional[str]:
    """用邮局全名与历史简称匹配；全名优先，同级别取最长。

    抄录可能写局名的区别性部分（如 P2 全名"南河湾局"写成"南河湾"），
    因此全名做包含匹配；别名（如"河湾"）只在没有更长的局名命中时采用，
    避免"南河湾"被较短的别名"河湾"抢走。
    """

    full_hits = [
        office.name for office in catalog.offices.values()
        if office.name in text
        or any(len(part) >= 2 and part in text
               for part in (office.name.rstrip('局所站'),))
    ]
    if full_hits:
        return max(full_hits, key=len)
    aliases = {
        alias
        for office in catalog.offices.values()
        for alias in office.aliases
        if alias in text
    }
    return max(aliases, key=len) if aliases else None


def parse_reading(reading: Reading, catalog: Catalog) -> ParsedReading:
    flags = _extract_flags(reading.text)
    uncertain = set(_CERTAINTY_FIELD.get(reading.certainty, ()))
    office_token = _extract_office_token(reading.text, catalog)

    date_expr: Optional[DateExpression] = None
    issue: Optional[str] = None
    try:
        date_expr = parse_date_text(
            reading.text, source_id=reading.id, reader=reading.reader
        )
    except DateParseError as exc:
        issue = str(exc)

    return ParsedReading(
        reading=reading,
        date_expr=date_expr,
        office_token=office_token,
        flags=flags,
        uncertain_fields=frozenset(uncertain),
        parse_issue=issue,
    )


def _add_candidate(bucket: dict, value, source: str) -> None:
    if value is None:
        return
    bucket.setdefault(value, [])
    if source not in bucket[value]:
        bucket[value].append(source)


def build_mark_facts(mark: Mark, readings: list[Reading], catalog: Catalog) -> MarkFacts:
    """聚合同一戳迹上的全部释读，分歧原样保留。"""

    parsed = [parse_reading(r, catalog) for r in readings]
    facts = MarkFacts(mark=mark, parsed=parsed)

    for item in parsed:
        for flag in item.flags:
            _add_candidate(facts.flags, flag, item.reading.id)
        facts.uncertain_fields.update(item.uncertain_fields)
        if item.parse_issue:
            facts.parse_issues.append(item.parse_issue)
        _add_candidate(facts.office_candidates, item.office_token, item.reading.id)
        expr = item.date_expr
        if expr is None:
            continue
        if expr.year is not None:
            _add_candidate(facts.year_candidates, str(expr.year), item.reading.id)
        if expr.year_hint is not None:
            _add_candidate(facts.year_candidates, f'疑{expr.year_hint}', item.reading.id)
        if expr.month is not None and expr.day is not None:
            _add_candidate(facts.month_day_candidates, (expr.month, expr.day), item.reading.id)
        if expr.time is not None:
            if expr.time.hour is not None:
                _add_candidate(facts.hour_candidates, str(expr.time.hour), item.reading.id)
            elif expr.time.label:
                _add_candidate(facts.hour_candidates, expr.time.label, item.reading.id)
    facts.office_candidates.pop(None, None)
    return facts


def all_mark_facts(catalog: Catalog) -> dict[str, MarkFacts]:
    """对资料内全部戳迹建立事实表。"""

    result: dict[str, MarkFacts] = {}
    for mark in catalog.marks.values():
        result[mark.id] = build_mark_facts(
            mark, catalog.readings_of(mark.id), catalog
        )
    return result
