"""例規の日付。取得日を制定日・最終改正日として出さない。

古い例規は law-date に西暦が併記されず和暦しか無い。西暦だけを見ていたので
公布日が読めず、そこへ取得日を入れていた。昭和 26 年の規則が「2026-08-30」と
表示され、日付順で並べると最新に見える。実データで 72 件あった。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scraped_source_records import extract_date_from_html, wareki_to_iso  # noqa: E402


class WarekiDateTest(unittest.TestCase):
    def test_western_year_alongside_wareki(self):
        self.assertEqual(
            extract_date_from_html('<div class="law-date">昭和12年6月25日 (1937-06-25)</div>'),
            "1937-06-25",
        )

    def test_wareki_only(self):
        self.assertEqual(
            extract_date_from_html('<div class="law-date">昭和26年10月1日</div>'),
            "1951-10-01",
        )

    def test_full_width_digits(self):
        self.assertEqual(
            extract_date_from_html('<div class="law-date">昭和２９年６月１日</div>'),
            "1954-06-01",
        )

    def test_first_year_of_era(self):
        self.assertEqual(wareki_to_iso("令和元年5月1日"), "2019-05-01")

    def test_missing_day_is_not_guessed(self):
        # 日が無いなら日付にしない。1 日と決めつけると、公布日を作ってしまう。
        self.assertEqual(extract_date_from_html('<div class="law-date">昭和26年10月</div>'), "")

    def test_no_date_block(self):
        self.assertEqual(extract_date_from_html("<div>本文だけ</div>"), "")

    def test_each_era_base(self):
        for text, expected in [
            ("明治22年4月1日", "1889-04-01"),
            ("大正3年7月28日", "1914-07-28"),
            ("昭和64年1月7日", "1989-01-07"),
            ("平成31年4月30日", "2019-04-30"),
            ("令和8年1月1日", "2026-01-01"),
        ]:
            self.assertEqual(wareki_to_iso(text), expected, text)


if __name__ == "__main__":
    unittest.main()
