"""核订引擎：可行解释交集、退回再寄、首日归类、通达时间护栏。"""
import unittest

from tests._support import load
from posthist.engine import verify_all
from posthist.exhibit import build_label


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, cls.era = load()
        cls.verdicts = verify_all(cls.catalog, cls.era)

    def test_every_cover_has_at_least_one_feasible_explanation(self):
        for cover_id, verification in self.verdicts.items():
            with self.subTest(cover=cover_id):
                self.assertTrue(verification.is_feasible, verification.contradictions)

    # ---- K1：退回再寄 + 分歧不投票 -----------------------------------

    def test_k1_origin_is_confirmed_but_hour_dispute_remains(self):
        k1 = self.verdicts['K1']
        confirmed_marks = {entry['mark']: entry for entry in k1.confirmed_marks}
        self.assertIn('M1', confirmed_marks)
        self.assertIn('北河湾局 1961年6月30日 21时', confirmed_marks['M1']['alternatives'])

        disputed = {entry['mark']: entry for entry in k1.disputed_marks}
        self.assertIn('M2', disputed)
        # 2时与6时必须同时保留，票数不能定局
        joined = ' '.join(disputed['M2']['alternatives'])
        self.assertIn('2时', joined)
        self.assertIn('6时', joined)

    def test_k1_m4_keeps_both_annotation_and_postal_roles(self):
        k1 = self.verdicts['K1']
        disputed = {entry['mark']: entry for entry in k1.disputed_marks}
        self.assertIn('M4', disputed)
        joined = ' '.join(disputed['M4']['alternatives'])
        self.assertIn('non_delivery', joined)
        self.assertIn('南河湾局', joined)
        # 后人释注仍作为实物说明保留
        self.assertTrue(any(note['mark'] == 'M4' for note in k1.physical_notes))

    def test_k1_all_explanations_contain_one_return(self):
        k1 = self.verdicts['K1']
        self.assertEqual(k1.return_status, 'one_return_confirmed')
        self.assertTrue(k1.feasible)
        self.assertEqual({record.return_runs for record in k1.feasible}, {1})

    def test_k1_first_day_is_confirmed(self):
        self.assertEqual(self.verdicts['K1'].first_day_status, 'confirmed')

    def test_k1_transit_guard_blocks_mixed_trip_elapsed_time(self):
        k1 = self.verdicts['K1']
        pairs = {(guard.from_mark, guard.to_mark) for guard in k1.transit_guards}
        # 第一次起日 M1 与第二次到日 M5 不得拼成普通通达时间
        self.assertIn(('M1', 'M5'), pairs)

    def test_k1_confirmed_edges_cover_main_chain_only(self):
        k1 = self.verdicts['K1']
        confirmed = {edge['route'] for edge in k1.confirmed_edges}
        self.assertEqual(confirmed, {'E1', 'E3', 'E5', 'E6'})
        disputed = {edge['route'] for edge in k1.disputed_edges}
        self.assertEqual(disputed, {'E2', 'E4'})

    # ---- K2：纪念补盖排除证据，首日被驳 ------------------------------

    def test_k2_supplemental_cancel_excluded_and_first_day_refuted(self):
        k2 = self.verdicts['K2']
        confirmed = {entry['mark']: entry for entry in k2.confirmed_marks}
        self.assertEqual(confirmed['M6']['status'], 'non_delivery')
        self.assertEqual(k2.first_day_status, 'refuted')
        self.assertIn('纪念补盖', k2.first_day_reason + str(k2.physical_notes))

    def test_k2_hinted_year_remains_single_1962_reading(self):
        k2 = self.verdicts['K2']
        m7 = next(entry for entry in k2.confirmed_marks if entry['mark'] == 'M7')
        self.assertTrue(any('1962年1月3日' in alt for alt in m7['alternatives']))

    # ---- K3：封背第二戳不自动等于第二次投递 --------------------------

    def test_k3_two_marks_are_one_trip_without_second_delivery(self):
        k3 = self.verdicts['K3']
        self.assertEqual(k3.return_status, 'none')
        self.assertEqual(len(k3.feasible[0].trips), 1)
        self.assertEqual(
            [node.mark_id for node in k3.feasible[0].timeline], ['M9', 'M8']
        )

    def test_k3_claimed_origin_does_not_become_first_day(self):
        # 山阁小船 1962-01-02 启用；M8 是当日到达戳而非始发，M9 所称寄地在前一天
        self.assertEqual(self.verdicts['K3'].first_day_status, 'refuted')

    # ---- 展签三层分述 -------------------------------------------------

    def test_label_separates_verified_claims_unknown_and_physical(self):
        label = build_label(self.catalog, self.verdicts['K1'])
        self.assertTrue(any('M1' in line for line in label.verified))
        self.assertTrue(any('首日实寄' in line for line in label.insert_claims)
                        or '首日实寄' in label.first_day_line)
        self.assertTrue(any('M2' in line for line in label.unknown))
        self.assertTrue(any('M4' in line for line in label.physical_notes))


if __name__ == '__main__':
    unittest.main()
