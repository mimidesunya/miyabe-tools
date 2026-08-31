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


class ExpiryHeadingTest(unittest.TestCase):
    """失効日を制定日にしない。

    出雲市の要綱は附則がこうなっている。

        (この要綱の失効)
        2
        この要綱は、令和9年3月31日限り、その効力を失う。

    見出しと日付の間に項番号の行が挟まる。手前一行だけを見ていたので
    見出しに届かず、2027-03-31 を制定日として索引に載せていた。
    """

    def test_expiry_heading_above_item_number(self):
        from scraped_source_records import first_wareki_date_in_head

        body = (
            "○出雲市土地改良区運営費補助金交付要綱\n"
            "附 則\n"
            "(この要綱の失効)\n"
            "2\n"
            "この要綱は、令和9年3月31日限り、その効力を失う。\n"
        )
        self.assertEqual(first_wareki_date_in_head(body), "")

    def test_promulgation_below_a_plain_item_number(self):
        """項番号の手前が見出しでなければ、日付はそのまま公布日。"""
        from scraped_source_records import first_wareki_date_in_head

        body = "○○要綱\n本文\n2\n平成24年3月31日告示第386号\n"
        self.assertEqual(first_wareki_date_in_head(body), "2012-03-31")


class ItemNumberNotationTest(unittest.TestCase):
    """項番号の書き方は自治体ごとに違う。

    見出しと日付の間に挟まる番号を `2` としか読めないと、`(2)` や `②` を
    使う自治体では見出しに届かず、失効日をまた公布日にしてしまう。
    """

    def setUp(self):
        from scraped_source_records import first_wareki_date_in_head

        self.extract = first_wareki_date_in_head

    def test_every_notation_reaches_the_heading(self):
        for number in ("2", "２", "2.", "(2)", "（2）", "②", "二", "第2項", "2　"):
            with self.subTest(number=number):
                body = (
                    "○○要綱\n附 則\n(この要綱の失効)\n"
                    f"{number}\n"
                    "この要綱は、令和9年3月31日限り、\n"
                )
                self.assertEqual(self.extract(body), "")

    def test_an_ordinary_line_is_not_a_number(self):
        """番号でない行の下の日付は、公布日のまま残す。"""
        # `第1条` は入れない。本則の始まりなので、そこで探索を打ち切る。
        for previous in ("本文", "出雲市告示", "", "2"):
            with self.subTest(previous=previous):
                body = f"○○要綱\n{previous}\n平成24年3月31日告示第386号\n"
                self.assertEqual(self.extract(body), "2012-03-31")


class BodyStartTest(unittest.TestCase):
    """本則が始まったら公布日探しを打ち切る。

    出雲市の要綱は制定表記が年だけなので、頭では日が読めない。そのまま読み
    進めて第3条の「令和8年4月1日(以下「基準日」)」や、附則の改正日を制定日に
    していた。2012 年の要綱が 2026 年制定として日付順の先頭に並ぶ。空より悪い。
    """

    def setUp(self):
        from scraped_source_records import first_wareki_date_in_head

        self.extract = first_wareki_date_in_head

    def test_a_reference_date_inside_a_clause_is_not_the_promulgation(self):
        body = (
            "○高齢者物価高騰対策支援給付金支給事務実施要綱\n"
            "令和8年出雲市告示第150号\n"
            "(趣旨)\n第1条\nこの要綱は…\n"
            "第3条\n令和8年4月1日(以下「基準日」)\n"
        )
        self.assertEqual(self.extract(body), "")

    def test_an_amending_supplementary_provision_is_not_the_promulgation(self):
        body = (
            "○同和教育推進指定事業実施要綱\n"
            "平成24年出雲市告示第205号\n"
            "改正\n平成27年2月19日告示第52号\n"
            "附則(令和8年3月30日告示第25号)\n"
        )
        self.assertEqual(self.extract(body), "")

    def test_a_real_promulgation_before_the_body_survives(self):
        body = (
            "○条例\n平成24年3月31日告示第386号\n"
            "改正\n令和7年3月18日告示\n(趣旨)\n第1条\n"
        )
        self.assertEqual(self.extract(body), "2012-03-31")


class PromulgationClauseTest(unittest.TestCase):
    """「…をここに公布する。」は除外の理由ではなく、公布日である証拠。

    三重県の条例は題名に「施行に伴う」が入る。公布文がその題名を繰り返すので、
    次行の除外語に当たって本物の公布日 1989-02-21 を捨てていた。
    """

    def setUp(self):
        from scraped_source_records import first_wareki_date_in_head

        self.extract = first_wareki_date_in_head

    def test_the_promulgation_clause_confirms_the_date(self):
        title = "昭和天皇の大喪の礼の行われる日を休日とする法律の施行に伴う関係条例の特例を定める条例"
        body = f"〔旧〕{title}\n平成元年二月二十一日三重県条例第一号\n{title}をここに公布する。\n"
        self.assertEqual(self.extract(body), "1989-02-21")

    def test_a_commencement_line_still_disqualifies(self):
        self.assertEqual(self.extract("○○要綱\n平成24年4月1日\nから施行する\n"), "")
