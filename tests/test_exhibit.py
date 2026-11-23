"""展陈核证、同图换件与不相容年代。"""
import unittest

from tests._support import load
from posthist.engine import verify_all
from posthist.exhibit import (
    adjudicate_all,
    analyze_swap,
    parse_draft_claims,
)


class ExhibitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, cls.era = load()
        cls.verdicts, cls.display_verdicts = adjudicate_all(cls.catalog, cls.era)

    def test_d1_fails_on_era_incompatibility_and_timing(self):
        d1 = self.display_verdicts['D1']
        self.assertEqual(d1.overall, 'unsupported')
        self.assertTrue(d1.era_conflict)
        blocker_kinds = {b.kind for b in d1.blockers}
        self.assertIn('era', blocker_kinds)
        self.assertIn('timing', blocker_kinds)
        era_blocker = next(b for b in d1.blockers if b.kind == 'era')
        self.assertEqual(era_blocker.cover_id, 'K3')
        self.assertEqual(era_blocker.route_id, 'E1')

    def test_d1_keeps_k1_route_but_rejects_joint_corroboration(self):
        d1 = self.display_verdicts['D1']
        route_findings = {f.cover_id: f.status for f in d1.findings
                          if f.claim_type == 'route'}
        self.assertEqual(route_findings['K1'], 'supported')
        self.assertEqual(route_findings['K3'], 'unsupported')
        corr = [f for f in d1.findings if f.claim_type == 'corroboration']
        self.assertTrue(corr)
        self.assertEqual(corr[0].status, 'unsupported')

    def test_d2_first_day_claim_refuted_by_k2_own_evidence(self):
        d2 = self.display_verdicts['D2']
        self.assertEqual(d2.overall, 'unsupported')
        first_day = {f.cover_id: f.status for f in d2.findings
                     if f.claim_type == 'first_day'}
        self.assertEqual(first_day['K2'], 'refuted')
        # D2 草稿没有路由说法，不得误报年代冲突
        self.assertFalse(d2.era_conflict)

    def test_swap_k1_to_k2_names_cover_reading_and_limit(self):
        reports = analyze_swap(
            self.catalog, self.catalog.displays['D2'],
            'K1', 'K2', self.verdicts,
        )
        first_day = next(r for r in reports if r.claim_text == '首日实寄')
        self.assertTrue(first_day.changed)
        self.assertEqual((first_day.status_before, first_day.status_after),
                         ('pending', 'refuted'))
        marker = next(b for b in first_day.blockers if b.mark_id == 'M6')
        self.assertEqual(marker.reading_sources, ['R7'])

    def test_swap_report_scopes_blockers_to_claim_type(self):
        reports = analyze_swap(
            self.catalog, self.catalog.displays['D1'],
            'K1', 'K2', self.verdicts,
        )
        # D1 草稿不含首日说法；含路由说法的报告只挂路由类阻断
        self.assertFalse(any(r.claim_text == '首日实寄' for r in reports))
        for report in reports:
            if report.claim_text.startswith('经'):
                self.assertTrue(
                    all(b.kind in {'route', 'era', 'mark'} for b in report.blockers)
                )

    def test_draft_claim_parsing_preserves_wording(self):
        draft = self.catalog.displays['D1'].draft
        claims = parse_draft_claims(self.catalog, draft)
        types = {claim['type'] for claim in claims}
        self.assertEqual(types, {'timing', 'route', 'corroboration'})
        route = next(c for c in claims if c['type'] == 'route')
        self.assertEqual(route['tokens'], ['河湾', '岱中', '山阁'])


if __name__ == '__main__':
    unittest.main()
