"""日期解析：年号、只写月日、残字、括号释读、跨日时刻。"""
import unittest
from datetime import date

from tests._support import load
from posthist.dates import (
    DateParseError,
    build_era_table,
    datetime_window,
    parse_date_text,
    resolve_era_year,
)


class DateParsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, cls.era = load()

    def test_era_token_resolves_to_calendar_year(self):
        expr = parse_date_text('河湾，壬17 六月卅日，21时启。', 'R1', '编目甲')
        self.assertEqual(expr.era_token, '壬17')
        self.assertEqual((expr.month, expr.day), (6, 30))
        self.assertEqual(expr.time.hour, 21)
        self.assertEqual(resolve_era_year(expr, self.era), [1961])

    def test_gregorian_and_hint_kept_distinct(self):
        explicit = parse_date_text('南河湾 1962.1.2，纪念后补盖。', 'R7', '编目丙')
        self.assertEqual(explicit.year, 1962)
        hinted = parse_date_text('岱站，1月3日，疑为1962。', 'R8', '编目丙')
        self.assertIsNone(hinted.year)
        self.assertEqual(hinted.year_hint, 1962)
        self.assertIn('年份系推测', hinted.flags)
        self.assertEqual(resolve_era_year(hinted, self.era), [1962])

    def test_month_day_only_offers_all_era_years_without_guessing(self):
        expr = parse_date_text('岱中，七月初一，二时。', 'R2', '编目甲')
        self.assertIsNone(expr.year)
        self.assertEqual(expr.candidate_years(self.era), [1960, 1961, 1962])
        with self.assertRaises(DateParseError):
            expr.as_date_range()

    def test_chinese_first_day_and_afternoon_window(self):
        first = parse_date_text('山阁壬18正月二日10时。', 'R9', '编目甲')
        self.assertEqual((first.month, first.day), (1, 2))
        afternoon = parse_date_text('沙嘴，七月一日，午后。', 'R4', '编目甲')
        start, end = datetime_window(afternoon, 1961)
        self.assertEqual(start.date(), date(1961, 7, 1))
        self.assertEqual((start.hour, end.hour), (12, 17))

    def test_bracket_note_is_separate_provenance(self):
        expr = parse_date_text('七月二日（后人补注：退回）。', 'X1', '测试人')
        self.assertEqual(expr.bracket_note, '后人补注：退回')
        self.assertEqual((expr.month, expr.day), (7, 2))

    def test_illegible_mark_does_not_invent_digits(self):
        expr = parse_date_text('岱中，七月□日，时刻残。', 'X2', '测试人')
        self.assertTrue(expr.illegible)
        self.assertIsNone(expr.day)

    def test_era_table_issue_dates(self):
        self.assertEqual(self.era.era_years, {'壬16': 1960, '壬17': 1961, '壬18': 1962})
        self.assertEqual(
            self.era.issue_date_for('河湾叶纹首日封'), date(1961, 6, 30)
        )
        self.assertEqual(
            self.era.issue_date_for('山阁小船纪念封'), date(1962, 1, 2)
        )


if __name__ == '__main__':
    unittest.main()
