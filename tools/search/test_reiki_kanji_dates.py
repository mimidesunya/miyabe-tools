"""漢数字で書かれた公布日を読む。

古い例規は「昭和三一年九月二九日」と漢数字で書かれる。算用数字しか見て
いなかったので公布日が読めず、本番で **37,325 件**が空だった
（東京都 2,418・千葉県 1,989・埼玉県 1,702）。

日付が空だと、制定順に並べられず、期間で絞り込めない。
題名も本文も正しいので、収録件数を数えても出てこない。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scraped_source_records import (  # noqa: E402
    extract_date_from_html,
    kanji_number_to_int,
    wareki_to_iso,
)


class KanjiNumberTest(unittest.TestCase):
    def test_digits_in_a_row(self) -> None:
        # 「三一」は 31。桁を並べただけの書き方。
        self.assertEqual(kanji_number_to_int("三一"), 31)
        self.assertEqual(kanji_number_to_int("二〇"), 20)
        self.assertEqual(kanji_number_to_int("一二"), 12)

    def test_a_single_digit(self) -> None:
        self.assertEqual(kanji_number_to_int("九"), 9)

    def test_positional_form(self) -> None:
        self.assertEqual(kanji_number_to_int("三十一"), 31)
        self.assertEqual(kanji_number_to_int("十"), 10)


class KanjiWarekiTest(unittest.TestCase):
    def test_kanji_dates(self) -> None:
        cases = {
            "昭和三一年九月二九日": "1956-09-29",
            "昭和五五年一二月二五日": "1980-12-25",
            "昭和二四年一月二〇日": "1949-01-20",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(wareki_to_iso(text), expected)

    def test_arabic_dates_still_work(self) -> None:
        self.assertEqual(wareki_to_iso("昭和32年7月30日"), "1957-07-30")
        self.assertEqual(wareki_to_iso("令和元年5月1日"), "2019-05-01")

    def test_a_date_without_a_day_is_not_guessed(self) -> None:
        self.assertEqual(wareki_to_iso("昭和三一年九月"), "")


class BodyHeadDateTest(unittest.TestCase):
    def test_the_body_head_carries_the_date(self) -> None:
        # `law-date` を持たない取得元がある。条例は題名のすぐあとに
        # 公布日と番号を並べる。
        html = (
            "<html><body>条例<br>■ 第2編 人事<br>"
            "職員の退職手当に関する条例<br><br>昭和三一年九月二九日<br><br>"
            "条例第六五号<br></body></html>"
        )
        self.assertEqual(extract_date_from_html(html), "1956-09-29")

    def test_law_date_still_wins(self) -> None:
        html = '<div class="law-date">昭和12年6月25日 (1937-06-25)</div>'
        self.assertEqual(extract_date_from_html(html), "1937-06-25")

    def test_an_enforcement_date_is_not_the_promulgation_date(self) -> None:
        # 「昭和四〇年四月一日から施行する」を公布日として拾っていた。
        from scraped_source_records import first_wareki_date_in_head

        body = (
            "第1条 この条例は昭和四〇年四月一日から施行する。" + chr(10)
            + "昭和三一年九月二九日" + chr(10) + "条例第六五号"
        )
        self.assertEqual(first_wareki_date_in_head(body), "1956-09-29")

    def test_a_last_amendment_date_is_not_the_promulgation_date(self) -> None:
        from scraped_source_records import first_wareki_date_in_head

        body = (
            "最終改正 平成二〇年三月三一日" + chr(10)
            + "昭和三一年九月二九日" + chr(10) + "条例第六五号"
        )
        self.assertEqual(first_wareki_date_in_head(body), "1956-09-29")

    def test_only_an_enforcement_date_means_no_promulgation_date(self) -> None:
        # 公布日が読めないなら空にする。施行日で埋めない。
        from scraped_source_records import first_wareki_date_in_head

        self.assertEqual(
            first_wareki_date_in_head("第1条 この条例は昭和四〇年四月一日から施行する。"),
            "",
        )

    def test_a_number_with_hundred_is_not_a_date(self) -> None:
        from scraped_source_records import first_wareki_date_in_head

        body = "条例第百二十号" + chr(10) + "昭和三一年九月二九日"
        self.assertEqual(first_wareki_date_in_head(body), "1956-09-29")

    def test_a_zero_date_is_not_a_date(self) -> None:
        # 取得元が `(0000-00-00)` を併記することがある（千葉県 1,989 件・
        # 埼玉県 1,702 件）。そのまま返すと、同じ行にある和暦を読まない。
        html = (
            '<div class="law-date">昭和四十九年九月二十七日規則第六十六号'
            " (0000-00-00)</div>"
        )
        self.assertEqual(extract_date_from_html(html), "1974-09-27")

    def test_an_amendment_block_does_not_supply_the_promulgation_date(self) -> None:
        # 「改正」が独立した行として置かれ、その下に改正の履歴が新しい順に並ぶ
        # 取得元がある。行ごとに見ていたので、いちばん新しい改正日を公布日に
        # していた。出雲市の行政不服審査会設置条例は制定 2016 年なのに
        # 2025-03-18 が入っていた。履歴の中でいちばん古い日付が公布日である。
        from scraped_source_records import first_wareki_date_in_head

        body = (
            "行政不服審査会設置条例" + chr(10) + "改正" + chr(10)
            + "令和七年三月一八日条例第五号" + chr(10)
            + "平成二八年三月二四日条例第二八号"
        )
        self.assertEqual(first_wareki_date_in_head(body), "2016-03-24")

    def test_a_term_of_office_is_not_the_promulgation_date(self) -> None:
        # 「委員の任期は令和一〇年三月三一日までとする」を公布日にしていた。
        # 本番で唯一の未来の公布日がこれだった。
        from scraped_source_records import first_wareki_date_in_head

        body = (
            "委員の任期は令和一〇年三月三一日までとする" + chr(10)
            + "平成二八年三月二四日条例第二八号"
        )
        self.assertEqual(first_wareki_date_in_head(body), "2016-03-24")

    def test_a_title_that_amends_another_ordinance_keeps_its_date(self) -> None:
        # 題名に「一部を改正する条例」が入るのは普通のこと。その行の日付は捨てない。
        from scraped_source_records import first_wareki_date_in_head

        body = "○○条例の一部を改正する条例 平成二八年三月二四日条例第二八号"
        self.assertEqual(first_wareki_date_in_head(body), "2016-03-24")

    def test_no_date_stays_empty(self) -> None:
        self.assertEqual(extract_date_from_html("<p>本文だけ</p>"), "")


if __name__ == "__main__":
    unittest.main()
