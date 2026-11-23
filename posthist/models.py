"""资料记录的数据类。

所有日期在装载时即转为 :class:`datetime.date`；抄录原话始终保留在
``raw_text`` 中，解析层只做结构化，不改写原文。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class Cover:
    """馆内封件标识。"""

    id: str
    image: str
    insert: str
    note: str


@dataclass(frozen=True)
class Office:
    """邮局及其历史简称，``open``/``close`` 含首尾。"""

    id: str
    name: str
    aliases: tuple[str, ...]
    open: date
    close: date

    def active_on(self, day: date) -> bool:
        return self.open <= day <= self.close


@dataclass(frozen=True)
class Route:
    """开放通路与普通件/退件的最短通达小时数。"""

    id: str
    origin: str
    destination: str
    min_hours: int
    direction: str
    open: date
    close: date

    def active_on(self, day: date) -> bool:
        return self.open <= day <= self.close


@dataclass(frozen=True)
class EraNote:
    """馆内核准的年代对照原文。"""

    id: str
    text: str


@dataclass(frozen=True)
class Mark:
    """同一邮封某一面上的物理戳迹。

    ``order_hint`` 只是馆员按叠印推得的可见先后，空值代表未辨清；
    它不是邮路先后，核订时只能作弱参考。
    """

    id: str
    cover: str
    side: str
    order_hint: Optional[int]


@dataclass(frozen=True)
class Reading:
    """一位抄录人对同一物理戳迹的原始释读。"""

    id: str
    mark: str
    reader: str
    text: str
    certainty: str


@dataclass(frozen=True)
class DisplayCase:
    """借展组合与说明草稿。"""

    id: str
    covers: tuple[str, ...]
    time: str
    draft: str


@dataclass
class Catalog:
    """整套资料的内存表示。"""

    covers: dict[str, Cover]
    offices: dict[str, Office]
    routes: dict[str, Route]
    era_notes: list[EraNote]
    marks: dict[str, Mark]
    readings: dict[str, Reading]
    displays: dict[str, DisplayCase]
    marks_by_cover: dict[str, list[Mark]] = field(default_factory=dict)
    readings_by_mark: dict[str, list[Reading]] = field(default_factory=dict)

    def marks_of(self, cover_id: str) -> list[Mark]:
        return list(self.marks_by_cover.get(cover_id, ()))

    def readings_of(self, mark_id: str) -> list[Reading]:
        return list(self.readings_by_mark.get(mark_id, ()))
