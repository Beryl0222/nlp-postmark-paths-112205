"""资料装载、引用校验与历史简称的年代解析。

同一个简称在不同时期可能属于不同邮局（本资料里"河湾"在
1961-07-01 前后分属北河湾局与南河湾局）。因此简称解析必须带着
事件日期与邮局开闭窗口一起判断，不能只做字符串查表。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from .dates import EraTable, build_era_table, resolve_era_year
from .models import Catalog, Cover, DisplayCase, EraNote, Mark, Office, Reading, Route


class CatalogError(ValueError):
    """资料文件结构或引用不合约定。"""


_TABLE_FIELDS = {
    'covers': ('id', 'image', 'insert', 'note'),
    'offices': ('id', 'name', 'aliases', 'open', 'close'),
    'era': ('id', 'text'),
    'routes': ('id', 'origin', 'destination', 'min_hours', 'direction', 'open', 'close'),
    'marks': ('id', 'cover', 'side', 'order_hint'),
    'readings': ('id', 'mark', 'reader', 'text', 'certainty'),
    'display': ('id', 'covers', 'time', 'draft'),
}


def load_catalog(path: str | Path) -> tuple[Catalog, EraTable]:
    """读取并校验 ``records.json``，返回内存资料与馆内年表。"""

    source = Path(path)
    raw = json.loads(source.read_text(encoding='utf-8'))

    missing = set(_TABLE_FIELDS) - set(raw)
    if missing:
        raise CatalogError(f'资料缺少表：{sorted(missing)}')

    for table, fields in _TABLE_FIELDS.items():
        for row in raw[table]:
            extra = set(row) - set(fields)
            absent = set(fields) - set(row)
            if extra or absent:
                raise CatalogError(
                    f'表 {table} 的记录 {row.get("id", row)!r} 字段不符：'
                    f'多余 {sorted(extra)}，缺少 {sorted(absent)}'
                )

    covers = {
        row['id']: Cover(row['id'], row['image'], row['insert'], row['note'])
        for row in raw['covers']
    }
    offices = {
        row['id']: Office(
            id=row['id'],
            name=row['name'],
            aliases=tuple(row['aliases']),
            open=date.fromisoformat(row['open']),
            close=date.fromisoformat(row['close']),
        )
        for row in raw['offices']
    }
    routes = {
        row['id']: Route(
            id=row['id'],
            origin=row['origin'],
            destination=row['destination'],
            min_hours=row['min_hours'],
            direction=row['direction'],
            open=date.fromisoformat(row['open']),
            close=date.fromisoformat(row['close']),
        )
        for row in raw['routes']
    }
    marks = {
        row['id']: Mark(row['id'], row['cover'], row['side'], row['order_hint'])
        for row in raw['marks']
    }
    readings = {
        row['id']: Reading(
            row['id'], row['mark'], row['reader'], row['text'], row['certainty']
        )
        for row in raw['readings']
    }
    displays = {
        row['id']: DisplayCase(row['id'], tuple(row['covers']), row['time'], row['draft'])
        for row in raw['display']
    }

    _check_identities(raw)
    _check_references(raw)

    marks_by_cover: dict[str, list[Mark]] = {}
    for mark in marks.values():
        marks_by_cover.setdefault(mark.cover, []).append(mark)
    readings_by_mark: dict[str, list[Reading]] = {}
    for reading in readings.values():
        readings_by_mark.setdefault(reading.mark, []).append(reading)

    catalog = Catalog(
        covers=covers,
        offices=offices,
        routes=routes,
        era_notes=[EraNote(row['id'], row['text']) for row in raw['era']],
        marks=marks,
        readings=readings,
        displays=displays,
        marks_by_cover=marks_by_cover,
        readings_by_mark=readings_by_mark,
    )
    era = build_era_table([(note.id, note.text) for note in catalog.era_notes])
    return catalog, era


def _check_identities(raw: dict) -> None:
    for table, rows in raw.items():
        ids = [row['id'] for row in rows]
        if len(ids) != len(set(ids)):
            raise CatalogError(f'表 {table} 存在重复 id')


def _check_references(raw: dict) -> None:
    office_ids = {row['id'] for row in raw['offices']}
    cover_ids = {row['id'] for row in raw['covers']}
    mark_ids = {row['id'] for row in raw['marks']}

    for row in raw['routes']:
        if row['origin'] not in office_ids or row['destination'] not in office_ids:
            raise CatalogError(f'路段 {row["id"]} 引用了不存在的邮局')
    for row in raw['marks']:
        if row['cover'] not in cover_ids:
            raise CatalogError(f'戳迹 {row["id"]} 引用了不存在的封件')
    for row in raw['readings']:
        if row['mark'] not in mark_ids:
            raise CatalogError(f'抄录 {row["id"]} 引用了不存在的戳迹')
    for row in raw['display']:
        unknown = set(row['covers']) - cover_ids
        if unknown:
            raise CatalogError(f'展陈 {row["id"]} 引用了不存在的封件 {sorted(unknown)}')


@dataclass(frozen=True)
class OfficeResolution:
    """简称解析结果；``ambiguous`` 为真时说明日期仍不足以定局。"""

    token: str
    offices: tuple[Office, ...]
    possible_years: tuple[int, ...]
    note: str = ''

    @property
    def resolved(self) -> bool:
        return len(self.offices) == 1

    @property
    def office_id(self) -> Optional[str]:
        return self.offices[0].id if self.resolved else None


def offices_matching_token(catalog: Catalog, token: str) -> list[Office]:
    """全名优先，其次历史简称。"""

    exact = [o for o in catalog.offices.values() if o.name == token]
    if exact:
        return exact
    return [o for o in catalog.offices.values() if token in o.aliases]


def resolve_office(
    catalog: Catalog,
    era: EraTable,
    token: Optional[str],
    year_candidates: list[int],
    month: Optional[int],
    day: Optional[int],
) -> OfficeResolution:
    """结合邮局开闭日期解析简称；年份未定则列出所有仍可能的邮局。"""

    if token is None:
        return OfficeResolution(token='', offices=(), possible_years=())

    candidates = offices_matching_token(catalog, token)
    if not candidates:
        return OfficeResolution(token=token, offices=(), possible_years=(),
                                note='馆内邮局表查无此称')

    viable: list[Office] = []
    viable_years: set[int] = set()
    for office in candidates:
        for year in year_candidates:
            if month is None or day is None:
                # 只写年份或连年份也缺：只要该年与局窗口有交集即保留
                if office.open.year <= year <= office.close.year:
                    viable_years.add(year)
                    if office not in viable:
                        viable.append(office)
                continue
            try:
                day_obj = date(year, month, day)
            except ValueError:
                continue
            if office.active_on(day_obj):
                viable_years.add(year)
                if office not in viable:
                    viable.append(office)

    if not viable:
        return OfficeResolution(
            token=token, offices=tuple(candidates),
            possible_years=tuple(year_candidates),
            note='简称对应邮局在所给日期均未开放，存在年代矛盾',
        )

    note = ''
    if len(viable) > 1:
        note = '同一简称在候选年代分属不同邮局，需进一步日期定局'
    return OfficeResolution(
        token=token,
        offices=tuple(viable),
        possible_years=tuple(sorted(viable_years)),
        note=note,
    )


def era_years_for(era: EraTable, expr) -> list[int]:
    """对外的年代换算入口，包一层以集中异常说明。"""

    return resolve_era_year(expr, era)
