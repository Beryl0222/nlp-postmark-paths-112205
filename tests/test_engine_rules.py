"""规则级测试：同日跨趟、无可行解释、残字不投票（合成内存资料）。"""
import unittest
from datetime import date

from posthist.dates import EraTable
from posthist.engine import verify_all
from posthist.models import (
    Catalog,
    Cover,
    DisplayCase,
    Mark,
    Office,
    Reading,
    Route,
)


def _office(oid: str, name: str, aliases: list[str], open_day: str, close_day: str) -> Office:
    return Office(oid, name, tuple(aliases),
                  date.fromisoformat(open_day), date.fromisoformat(close_day))


class _Builder:
    def __init__(self) -> None:
        self.offices: dict[str, Office] = {}
        self.routes: dict[str, Route] = {}
        self.marks: dict[str, Mark] = {}
        self.readings: dict[str, Reading] = {}
        self.covers: dict[str, Cover] = {}
        self.marks_by_cover: dict[str, list[Mark]] = {}
        self.readings_by_mark: dict[str, list[Reading]] = {}

    def add_office(self, office: Office) -> '_Builder':
        self.offices[office.id] = office
        return self

    def add_route(self, route: Route) -> '_Builder':
        self.routes[route.id] = route
        return self

    def add_cover(self, cover: Cover) -> '_Builder':
        self.covers[cover.id] = cover
        self.marks_by_cover[cover.id] = []
        return self

    def add_mark(self, mark: Mark) -> '_Builder':
        self.marks[mark.id] = mark
        self.marks_by_cover[mark.cover].append(mark)
        self.readings_by_mark[mark.id] = []
        return self

    def add_reading(self, reading: Reading) -> '_Builder':
        self.readings[reading.id] = reading
        self.readings_by_mark[reading.mark].append(reading)
        return self

    def build(self) -> Catalog:
        return Catalog(
            covers=self.covers,
            offices=self.offices,
            routes=self.routes,
            era_notes=[],
            marks=self.marks,
            readings=self.readings,
            displays={},
            marks_by_cover=self.marks_by_cover,
            readings_by_mark=self.readings_by_mark,
        )


ERA = EraTable(
    era_years={'壬17': 1961, '壬18': 1962},
    issue_dates={'测试图样': date(1961, 6, 30)},
)


class SameDayDifferentTripsTests(unittest.TestCase):
    def _build_catalog(self) -> Catalog:
        b = _Builder()
        b.add_office(_office('A', '甲局', ['甲'], '1960-01-01', '1963-01-01'))
        b.add_office(_office('B', '乙局', ['乙'], '1960-01-01', '1963-01-01'))
        b.add_office(_office('C', '丙局', ['丙'], '1960-01-01', '1963-01-01'))
        b.add_cover(Cover('C1', '测试图样封', '无', '测试'))
        # 去程 甲→乙，退件 乙→甲，再寄 甲→丙；m1/m3 同日但不同趟
        b.add_route(Route('r1', 'A', 'B', 4, '普通',
                          date(1960, 1, 1), date(1963, 1, 1)))
        b.add_route(Route('r2', 'B', 'A', 4, '退件',
                          date(1960, 1, 1), date(1963, 1, 1)))
        b.add_route(Route('r3', 'A', 'C', 4, '普通',
                          date(1960, 1, 1), date(1963, 1, 1)))
        b.add_mark(Mark('m1', 'C1', 'front', 1))
        b.add_mark(Mark('m2', 'C1', 'back', 2))
        b.add_mark(Mark('m3', 'C1', 'back', 3))
        b.add_reading(Reading('s1', 'm1', '甲', '甲，壬17 七月一日6时。', '清楚'))
        b.add_reading(Reading('s2', 'm2', '甲', '乙，壬17 七月一日14时，退回。', '清楚'))
        b.add_reading(Reading('s3', 'm3', '甲', '丙，壬17 七月一日23时。', '清楚'))
        return b.build()

    def test_same_day_marks_split_into_two_trips(self):
        verdicts = verify_all(self._build_catalog(), ERA)
        verification = verdicts['C1']
        self.assertTrue(verification.is_feasible)
        record = verification.feasible[0]
        self.assertEqual(len(record.trips), 3)
        self.assertEqual(record.return_runs, 1)
        self.assertEqual(
            [node.mark_id for node in record.timeline], ['m1', 'm2', 'm3']
        )
        self.assertTrue(
            all(node.window[0].date() == date(1961, 7, 1)
                for node in record.timeline)
        )
        # m1 与 m3 同日，但分属不同趟，须出现通达时间护栏
        pairs = {(g.from_mark, g.to_mark) for g in verification.transit_guards}
        self.assertIn(('m1', 'm3'), pairs)


class ContradictionTests(unittest.TestCase):
    def test_infeasible_cover_reports_contradiction_instead_of_picking_route(self):
        b = _Builder()
        b.add_office(_office('A', '甲局', ['甲'], '1960-01-01', '1963-01-01'))
        b.add_office(_office('B', '乙局', ['乙'], '1960-01-01', '1963-01-01'))
        b.add_cover(Cover('C2', '测试图样封', '无', '测试'))
        # 两局之间没有任何通路
        b.add_mark(Mark('m1', 'C2', 'front', 1))
        b.add_mark(Mark('m2', 'C2', 'back', 2))
        b.add_reading(Reading('s1', 'm1', '甲', '甲，壬17 七月一日8时。', '清楚'))
        b.add_reading(Reading('s2', 'm2', '乙', '乙，壬17 七月二日8时。', '清楚'))
        verdicts = verify_all(b.build(), ERA)
        verification = verdicts['C2']
        self.assertFalse(verification.is_feasible)
        self.assertFalse(verification.feasible)
        self.assertTrue(verification.contradictions)
        self.assertIn('没有任何完整递送解释', verification.contradictions[0])


class NoVoteOnIllegibleDigitTests(unittest.TestCase):
    def test_two_readings_same_impression_keep_distinct_days(self):
        b = _Builder()
        b.add_office(_office('A', '甲局', ['甲'], '1960-01-01', '1963-01-01'))
        b.add_cover(Cover('C3', '测试图样封', '无', '测试'))
        b.add_mark(Mark('m1', 'C3', 'front', 1))
        b.add_reading(Reading('s1', 'm1', '甲抄', '甲，壬17 七月一日8时。', '清楚'))
        b.add_reading(Reading('s2', 'm1', '乙抄', '甲，壬17 七月二日8时。', '日期存疑'))
        from posthist.parse import all_mark_facts

        facts = all_mark_facts(b.build())['m1']
        self.assertEqual(
            set(facts.month_day_candidates), {(7, 1), (7, 2)}
        )


if __name__ == '__main__':
    unittest.main()
