"""日期与时刻解析。

设计原则（对应编目约定）：

- 只写月日、年号纪年、公元纪年、括号里的后人释读分别保留来源，
  解析结果始终带着它出自哪条抄录。
- 残字（``□``、"残"、"缺"）不猜补，只标记不可辨。
- 年份缺失时不擅自补全年份，而是给出馆内年表中的候选年份，
  由邮局开闭日期与路段时段进一步收窄。
- 馆内记时用当地零时，跨日边界由日期加时刻的普通时间差自然处理。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional


class DateParseError(ValueError):
    """日期文字无法按馆内约定解析。"""


_CN_DIGITS = {
    '〇': 0, '零': 0, '一': 1, '二': 2, '三': 3, '四': 4,
    '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
}
_SPECIAL_MONTHS = {'正': 1, '腊': 12}
_ILLEGIBLE_MARKS = ('□', '残', '缺', '漫漶')


@dataclass(frozen=True)
class TimeExpression:
    """时刻；小时不可辨时保留区间性描述（如"午后"）。"""

    text: str
    hour: Optional[int] = None
    label: str = ''

    def window_on(self, day: date) -> tuple[datetime, datetime]:
        """返回该时刻在给定日期上的最早、最晚可能时间（含端点）。"""

        if self.hour is not None:
            instant = datetime.combine(day, datetime.min.time()).replace(hour=self.hour)
            return instant, instant
        if self.label == '午后':
            start = datetime.combine(day, datetime.min.time()).replace(hour=12)
            end = datetime.combine(day, datetime.min.time()).replace(hour=17, minute=59)
            return start, end
        start = datetime.combine(day, datetime.min.time())
        end = datetime.combine(day, datetime.min.time()).replace(hour=23, minute=59)
        return start, end


@dataclass(frozen=True)
class DateExpression:
    """结构化的日期释读，字段缺失即代表该抄录没有给出这一位。"""

    raw_text: str
    source_id: str
    reader: str
    year: Optional[int] = None
    year_hint: Optional[int] = None
    era_token: Optional[str] = None
    month: Optional[int] = None
    day: Optional[int] = None
    time: Optional[TimeExpression] = None
    bracket_note: Optional[str] = None
    illegible: bool = False
    flags: frozenset[str] = field(default_factory=frozenset)

    @property
    def complete(self) -> bool:
        return self.year is not None and self.month is not None and self.day is not None

    def as_date_range(self, year: Optional[int] = None) -> tuple[date, date]:
        """在指定年份下把月日变成单日闭区间；缺月日则抛错。"""

        chosen = year if year is not None else self.year
        if chosen is None or self.month is None or self.day is None:
            raise DateParseError(
                f'{self.source_id} 的日期不完整（{self.raw_text!r}），无法落为具体日期'
            )
        day = date(chosen, self.month, self.day)
        return day, day

    def candidate_years(self, era: 'EraTable') -> list[int]:
        """给出所有仍可成立的年份候选，绝不替残字投票。"""

        if self.year is not None:
            return [self.year]
        if self.year_hint is not None:
            return [self.year_hint]
        return list(era.years)

    def describe(self) -> str:
        parts = []
        if self.year is not None:
            parts.append(f'{self.year}年')
        elif self.year_hint is not None:
            parts.append(f'疑为{self.year_hint}年')
        if self.month is not None and self.day is not None:
            parts.append(f'{self.month}月{self.day}日')
        if self.time is not None:
            if self.time.hour is not None:
                parts.append(f'{self.time.hour}时')
            elif self.time.label:
                parts.append(self.time.label)
        if self.illegible:
            parts.append('含残字')
        if self.bracket_note:
            parts.append(f'括号注：{self.bracket_note}')
        return ''.join(parts) or self.raw_text


@dataclass(frozen=True)
class EraTable:
    """馆内核准的合成年表：年号对照与图样启用日。"""

    era_years: dict[str, int]
    issue_dates: dict[str, date]
    raw_notes: tuple[str, ...] = ()

    @property
    def years(self) -> list[int]:
        return sorted(set(self.era_years.values()))

    def issue_date_for(self, image: str) -> Optional[date]:
        if image in self.issue_dates:
            return self.issue_dates[image]
        # 封件 image 可能带"首日封/纪念封"后缀，按图样名前缀回退
        for pattern, day in self.issue_dates.items():
            if image.startswith(pattern):
                return day
        return None


def _cn_number(token: str) -> int:
    """把卅、廿一、十二、三等中文数字转为整数（支持 1..31）。"""

    if token.isdigit():
        return int(token)
    if token in _SPECIAL_MONTHS:
        return _SPECIAL_MONTHS[token]
    if token.startswith('卅'):
        return 30 + (_CN_DIGITS[token[1]] if len(token) > 1 else 0)
    if token.startswith('廿'):
        return 20 + (_CN_DIGITS[token[1]] if len(token) > 1 else 0)
    if '十' in token:
        left, _, right = token.partition('十')
        tens = _CN_DIGITS[left] if left else 1
        units = _CN_DIGITS[right] if right else 0
        return tens * 10 + units
    if len(token) == 1 and token in _CN_DIGITS:
        return _CN_DIGITS[token]
    raise DateParseError(f'无法识别的中文数字：{token!r}')


_BRACKET_RE = re.compile(r'[（(]([^（）()]+)[）)]')
_GREGORIAN_RE = re.compile(r'(\d{4})\s*[.\-年]\s*(\d{1,2})\s*[.\-月]\s*(\d{1,2})\s*日?')
# 年号后须紧跟"="，避免把"壬17=1960"误截成"壬1=8"
_ERA_RE = re.compile(r'壬\s*(\d+)\s*=')
_ERA_TOKEN_RE = re.compile(r'壬\s*(\d+)(?!\d)')
_YEAR_HINT_RE = re.compile(r'疑[为是]\s*(\d{4})')
_MONTH_RE = re.compile(r'(正|腊|[一二三四五六七八九十廿卅]+|\d{1,2})\s*月')
_DAY_RE = re.compile(r'初\s*([一二三四五六七八九十])|([一二三四五六七八九十廿卅]+|\d{1,2})\s*日')
_HOUR_RE = re.compile(r'(\d{1,2}|[一二三四五六七八九十]+)\s*时')
_AFTERNOON_RE = re.compile(r'午后|下午')


def parse_date_text(text: str, source_id: str, reader: str) -> DateExpression:
    """从一条抄录文字中抽取日期/时刻；无日期成分时抛 :class:`DateParseError`。"""

    flags: set[str] = set()
    illegible = any(mark in text for mark in _ILLEGIBLE_MARKS)
    if illegible:
        flags.add('含残字')

    bracket_match = _BRACKET_RE.search(text)
    bracket_note = bracket_match.group(1).strip() if bracket_match else None
    work = _BRACKET_RE.sub(' ', text)

    year: Optional[int] = None
    year_hint: Optional[int] = None
    era_token: Optional[str] = None

    greg = _GREGORIAN_RE.search(work)
    if greg:
        year, month, day = (int(greg.group(i)) for i in range(1, 4))
        work = work[:greg.start()] + ' ' + work[greg.end():]
    else:
        month = day = None
        era_match = _ERA_TOKEN_RE.search(work)
        if era_match:
            era_token = f'壬{era_match.group(1)}'
            work = work[:era_match.start()] + ' ' + work[era_match.end():]

    hint_match = _YEAR_HINT_RE.search(work)
    if hint_match:
        year_hint = int(hint_match.group(1))
        flags.add('年份系推测')
        work = work[:hint_match.start()] + ' ' + work[hint_match.end():]

    month_match = _MONTH_RE.search(work)
    if month_match and month is None:
        token = month_match.group(1)
        month = _SPECIAL_MONTHS.get(token, _cn_number(token))
        work = work[:month_match.start()] + ' ' + work[month_match.end():]

    day_match = _DAY_RE.search(work)
    if day_match and day is None:
        token = day_match.group(1) or day_match.group(2)
        day = _cn_number(token)
        work = work[:day_match.start()] + ' ' + work[day_match.end():]

    time: Optional[TimeExpression] = None
    afternoon = _AFTERNOON_RE.search(work)
    hour_match = _HOUR_RE.search(work)
    if hour_match:
        token = hour_match.group(1)
        hour = int(token) if token.isdigit() else _cn_number(token)
        time = TimeExpression(text=hour_match.group(0), hour=hour)
    elif afternoon:
        time = TimeExpression(text=afternoon.group(0), label='午后')

    if year is None and month is None and day is None and time is None and bracket_note is None:
        raise DateParseError(f'{source_id} 文字中未找到日期成分：{text!r}')

    if not (1 <= (month or 1) <= 12) or not (1 <= (day or 1) <= 31):
        raise DateParseError(f'{source_id} 的月日越界：{text!r}')

    return DateExpression(
        raw_text=text,
        source_id=source_id,
        reader=reader,
        year=year,
        year_hint=year_hint,
        era_token=era_token,
        month=month,
        day=day,
        time=time,
        bracket_note=bracket_note,
        illegible=illegible,
        flags=frozenset(flags),
    )


def build_era_table(notes: list[tuple[str, str]]) -> EraTable:
    """从 ``era`` 表的原文中抽取年号对照与图样启用日。"""

    era_years: dict[str, int] = {}
    issue_dates: dict[str, date] = {}
    raw: list[str] = []
    for _, text in notes:
        raw.append(text)
        for token, greg_year in re.findall(r'壬\s*(\d+)\s*=\s*(\d{4})', text):
            era_years[f'壬{token}'] = int(greg_year)
        for issue in re.finditer(r'([一-鿿]+?)样式于\s*(\d{4}-\d{2}-\d{2})\s*启用', text):
            issue_dates[issue.group(1).strip()] = date.fromisoformat(issue.group(2))
    return EraTable(era_years=era_years, issue_dates=issue_dates, raw_notes=tuple(raw))


def resolve_era_year(expr: DateExpression, era: EraTable) -> list[int]:
    """把年号纪年换算为公历年；无年号者回退到候选年份。"""

    if expr.era_token is not None:
        if expr.era_token not in era.era_years:
            raise DateParseError(
                f'{expr.source_id} 的年号 {expr.era_token} 不在馆内年表中'
            )
        return [era.era_years[expr.era_token]]
    return expr.candidate_years(era)


def datetime_window(expr: DateExpression, year: int) -> tuple[datetime, datetime]:
    """日期+时刻合并为当地时间窗口；无时刻按整日，跨日由时间差处理。"""

    day, _ = expr.as_date_range(year)
    if expr.time is None:
        start = datetime.combine(day, datetime.min.time())
        return start, start + timedelta(hours=23, minutes=59)
    return expr.time.window_on(day)
