"""历史简称按年代定局：同一称谓在不同时期属于不同邮局。"""
import unittest
from datetime import date

from tests._support import load
from posthist.catalog import resolve_office


class OfficeResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, cls.era = load()

    def test_hewan_alias_splits_around_1961_07_01(self):
        before = resolve_office(self.catalog, self.era, '河湾', [1961], 6, 30)
        self.assertTrue(before.resolved)
        self.assertEqual(before.office_id, 'P1')

        after = resolve_office(self.catalog, self.era, '河湾', [1961], 7, 2)
        self.assertTrue(after.resolved)
        self.assertEqual(after.office_id, 'P2')

    def test_unknown_year_keeps_both_offices_as_candidates(self):
        result = resolve_office(self.catalog, self.era, '河湾', [1960, 1961, 1962], 7, 1)
        self.assertFalse(result.resolved)
        self.assertEqual({office.id for office in result.offices}, {'P1', 'P2'})
        self.assertIn('不同邮局', result.note)

    def test_token_unknown_to_office_table(self):
        result = resolve_office(self.catalog, self.era, '不存在的局', [1961], 1, 1)
        self.assertEqual(result.offices, ())

    def test_closed_office_is_era_contradiction(self):
        # P1 于 1961-06-30 关闭；1962 年的“河湾”只能是 P2
        result = resolve_office(self.catalog, self.era, '河湾', [1962], 1, 3)
        self.assertEqual([office.id for office in result.offices], ['P2'])

    def test_full_name_resolves_directly(self):
        result = resolve_office(self.catalog, self.era, '岱中转运所', [1961], 7, 1)
        self.assertEqual(result.office_id, 'P3')


if __name__ == '__main__':
    unittest.main()
