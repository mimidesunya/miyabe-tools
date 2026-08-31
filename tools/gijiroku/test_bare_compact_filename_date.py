"""元号の記号が無い 6 桁のファイル名日付。

下田市は `会議録本文（010513）.pdf` と書く。令和元年5月13日である。`R010513`
のような元号記号が無いので、ファイル名からは読めていなかった。本文はページの
途中から始まる抜粋で日付を持たないため、拾えるのはファイル名だけだった。

6 桁の並びは議案番号にも見えるので、**年ラベルの元号年と先頭 2 桁が一致する
ときだけ**日付として読む。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from minutes_kind import _candidates_from_filename, _era_parts_from_label  # noqa: E402


class EraPartsTest(unittest.TestCase):
    def test_reads_the_era_and_its_base(self):
        self.assertEqual(_era_parts_from_label("令和1年"), (1, 2018))
        self.assertEqual(_era_parts_from_label("平成28年"), (28, 1988))
        self.assertEqual(_era_parts_from_label("令和元年"), (1, 2018))

    def test_a_committee_name_is_not_a_year(self):
        """荒尾市は年ラベルの位置に `市民病院` を入れる。"""
        self.assertEqual(_era_parts_from_label("市民病院"), (None, None))


class BareCompactFilenameDateTest(unittest.TestCase):
    def test_reads_a_bare_six_digit_date(self):
        found = _candidates_from_filename("会議録本文（010513）.pdf", era_year=1, era_base=2018)
        self.assertEqual([(c.year, c.month, c.day) for c in found], [(2019, 5, 13)])

    def test_a_number_whose_head_is_not_the_era_year_is_ignored(self):
        self.assertEqual(
            _candidates_from_filename("会議録本文（050513）.pdf", era_year=1, era_base=2018), []
        )

    def test_a_longer_run_of_digits_is_not_a_date(self):
        self.assertEqual(
            _candidates_from_filename("議案第123456号.pdf", era_year=1, era_base=2018), []
        )

    def test_an_impossible_month_is_ignored(self):
        self.assertEqual(
            _candidates_from_filename("資料（011713）.pdf", era_year=1, era_base=2018), []
        )

    def test_without_a_year_label_nothing_is_read(self):
        """年ラベルが無ければ照合できないので、6 桁は読まない。"""
        self.assertEqual(_candidates_from_filename("会議録本文（010513）.pdf"), [])

    def test_an_era_marked_name_still_wins(self):
        """`R010513` のような元号記号つきが先。6 桁の推測へは落ちない。"""
        found = _candidates_from_filename("R010513_honbun.pdf", era_year=1, era_base=2018)
        self.assertEqual([(c.year, c.month, c.day) for c in found], [(2019, 5, 13)])


class TitleMonthDayWeightTest(unittest.TestCase):
    """題名だけが月日を持つときも、本文のよその日付より強くする。

    横須賀市の `12月19日－02号`（年ラベル `令和 ８年 ３月定例議会 広報広聴会議`）は、
    本文の 2026-01-09 が年ラベル一致の加点で勝ち、題名の 12月19日 を捨てていた。
    実データ 7 自治体・10,224 ファイルで、変わったのは 34 件。すべて題名の月日に
    一致する側へ動いた（`11月26日－05号` が 2021-01-05 → 2020-11-26）。
    """

    def setUp(self):
        from minutes_kind import extract_plausible_held_on

        self.extract = extract_plausible_held_on

    def test_the_title_month_day_beats_a_body_date(self):
        body = "12月19日－02号\n令和 ８年 ３月定例議会 広報広聴会議\n令和7年12月19日\n令和8年1月9日に資料を配付した。\n"
        self.assertEqual(
            self.extract(body, title="12月19日－02号", year_label="令和8年", filename="12月19日－02号.txt"),
            "2025-12-19",
        )

    def test_an_opening_line_still_wins_over_the_title(self):
        """題名は会期の初日を指すことがある。開議行のほうが強い。"""
        body = "第5号 6月10日\n令和5年6月17日（土曜日） 午前10時00分開議\n"
        self.assertEqual(
            self.extract(body, title="第5号 6月10日", year_label="令和5年", filename="第5号.txt"),
            "2023-06-17",
        )
