"""释读解析：同印痕多释读不投票、标记落到具体戳迹、补盖资格排除。"""
import unittest

from tests._support import load
from posthist.parse import all_mark_facts, parse_reading


class ParsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, cls.era = load()
        cls.facts = all_mark_facts(cls.catalog)

    def test_full_office_name_beats_short_alias(self):
        r5 = next(r for r in self.catalog.readings.values() if r.id == 'R5')
        parsed = parse_reading(r5, self.catalog)
        # 抄录写区别性局名"南河湾"，标准化到 P2 全名，而不是短别名"河湾"
        self.assertEqual(parsed.office_token, '南河湾局')
        r1 = next(r for r in self.catalog.readings.values() if r.id == 'R1')
        self.assertEqual(parse_reading(r1, self.catalog).office_token, '河湾')

    def test_overlapping_readings_keep_both_hours_without_vote(self):
        m2 = self.facts['M2']
        self.assertEqual(set(m2.hour_candidates), {'2', '6'})
        self.assertIn('hour', m2.uncertain_fields)
        self.assertTrue(m2.has_overlap)
        # 两个候选都保留，没有任何一个因票数被升格为确定值
        self.assertEqual(len(m2.hour_candidates), 2)

    def test_supplemental_cancel_loses_delivery_evidence_status(self):
        m6 = self.facts['M6']
        self.assertTrue(m6.is_non_delivery)
        for parsed in m6.parsed:
            self.assertFalse(parsed.is_delivery_evidence)
            self.assertIn('纪念补盖', parsed.flags)

    def test_later_annotation_is_flagged_on_its_own_mark(self):
        m4 = self.facts['M4']
        self.assertIn('后人释注', m4.flags)
        self.assertIn('退回字样', m4.flags)
        # 后人释注本身不含日期成分，问题被记录而不是静默吞掉
        self.assertTrue(any('未找到日期成分' in issue for issue in m4.parse_issues))

    def test_claimed_origin_is_a_claim_not_a_confirmed_role(self):
        m9 = self.facts['M9']
        self.assertIn('所称寄地', m9.flags)
        self.assertIn('action', m9.uncertain_fields)

    def test_return_note_detected(self):
        self.assertTrue(self.facts['M4'].has_return_note)


if __name__ == '__main__':
    unittest.main()
