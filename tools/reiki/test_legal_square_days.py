"""単月まで割っても上限に届かないときの、日単位の分割。

飛騨市は合併月（平成16年2月）に条例・規則が 100 件超まとめて制定されており、
月単位では割り切れなかった。実際に取りこぼしとして検出された。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / "reiki" / "scrapers"), str(ROOT / "tools" / "reiki")]

import legal_square  # noqa: E402


def slot_index(era: str, year: int, month: int) -> int:
    for index, slot in enumerate(legal_square.MONTH_SLOTS):
        if slot.era == era and slot.year == year and slot.month == month:
            return index
    raise AssertionError(f"{era}{year}年{month}月 のスロットが無い")


class DaySplitTest(unittest.TestCase):
    def test_leap_february_has_29_days(self):
        index = slot_index("平成", 16, 2)
        self.assertEqual(legal_square.slot_day_range((index, index)), (1, 29))
        self.assertEqual(legal_square.slot_day_count((index, index)), 29)

    def test_era_boundary_month_starts_partway(self):
        # 令和元年5月は 1 日からではなく、改元日から始まる。
        index = slot_index("令和", 1, 5)
        first, last = legal_square.slot_day_range((index, index))
        self.assertEqual(first, 1)
        self.assertEqual(last, 31)

    def test_labels_distinguish_month_range_and_day(self):
        index = slot_index("平成", 16, 2)
        span = (index, index)
        self.assertEqual(legal_square.span_label(span), "平成16.2〜平成16.2")
        self.assertEqual(legal_square.span_label(span, (1, 15)), "平成16.2.1〜15")
        self.assertEqual(legal_square.span_label(span, (1, 1)), "平成16.2.1")

    def test_day_range_is_ignored_across_months(self):
        # 複数月にまたがる範囲では日で絞らない。絞ると間の月が丸ごと落ちる。
        low = slot_index("平成", 16, 1)
        high = slot_index("平成", 16, 3)
        self.assertEqual(
            legal_square.span_label((low, high), (1, 5)), "平成16.1〜平成16.3"
        )

    def test_single_day_cannot_be_split_further(self):
        index = slot_index("平成", 16, 2)
        # 単日まで来たら、それ以上は割れない。ここで未取得として記録する。
        self.assertEqual(legal_square.slot_day_count((index, index)), 29)
        self.assertGreater(legal_square.slot_day_count((index, index)), 1)


if __name__ == "__main__":
    unittest.main()


class TitleSplitWordTest(unittest.TestCase):
    """単日でも上限に張り付く区間を、件名の語で分けられるようにする。"""

    def test_candidates_shrink_as_words_are_used(self) -> None:
        first = legal_square.title_split_candidates(())
        self.assertEqual(first[0], "の")
        used = (("の", True),)
        self.assertNotIn("の", legal_square.title_split_candidates(used))

    def test_stops_at_the_number_of_keyword_fields(self) -> None:
        # 詳細検索の件名欄は 5 つしかない。それ以上は AND でつなげない。
        words = tuple((word, False) for word in legal_square.TITLE_SPLIT_WORDS[:5])
        self.assertEqual(legal_square.title_split_candidates(words), [])

    def test_kind_words_are_not_candidates(self) -> None:
        # 種別で既に絞っているので、同じ語で割っても分かれない。
        self.assertNotIn("条例", legal_square.TITLE_SPLIT_WORDS)
        self.assertNotIn("規則", legal_square.TITLE_SPLIT_WORDS)

    def test_label_shows_both_sides(self) -> None:
        self.assertEqual(legal_square.words_label(()), "")
        self.assertEqual(
            legal_square.words_label((("の", False), ("市", True))),
            " 件名[の][除く市]",
        )


class SplitWordChoiceTest(unittest.TestCase):
    """件名の語は、「含む」側が上限未満でいちばん大きく分かれるものを採る。

    以前は最初に分かれた語を採っていた。栃木市の合併日の条例は「等」2 件・
    「職員」2 件で枠を使い切り、残り 100 件超を取り切れなかった。取得元で
    確かめると、最後の枠を「センター」（18 件）にすれば残りは 86 件だった。
    """

    def test_the_largest_split_under_the_cap_wins(self) -> None:
        probes = [("職員", 2), ("管理", 2), ("センター", 18), ("基金", 16)]
        self.assertEqual(legal_square.pick_split_word(probes, 100), "センター")

    def test_a_word_that_keeps_everything_is_not_a_split(self) -> None:
        # 「含む」側も上限のままなら、その語では割れていない。
        probes = [("要綱", 100), ("補助", 25)]
        self.assertEqual(legal_square.pick_split_word(probes, 100), "補助")

    def test_nothing_splits(self) -> None:
        self.assertEqual(legal_square.pick_split_word([("補助", 0), ("館", 100)], 100), "")

    def test_half_is_good_enough_to_stop_probing(self) -> None:
        # 「の」が 62/100 なら、ほかの語を試すまでもない（検索を増やさない）。
        self.assertTrue(legal_square.split_is_good_enough(62, 100))
        self.assertFalse(legal_square.split_is_good_enough(18, 100))
        self.assertFalse(legal_square.split_is_good_enough(100, 100))

    def test_merger_day_words_are_candidates(self) -> None:
        # 取得元で最後の枠を取り切れた語（栃木市・久喜市・静岡市）。
        for word in ("センター", "要綱", "施行"):
            self.assertIn(word, legal_square.title_split_candidates(()))


class SplitConsistencyTest(unittest.TestCase):
    """上限に張り付いた区間を割った先の合計は、元の件数に届くはず。

    久喜市 9/29 は「規則 平成19.4〜平成24.3」が上限の 100 件なのに、割った
    先がどちらも 0 件と読めて、規則 193 件が黙って一覧から落ちた。
    """

    def test_two_empty_halves_cannot_make_up_a_capped_range(self) -> None:
        self.assertFalse(legal_square.split_accounts_for(100, [0, 0]))

    def test_halves_that_reach_the_cap_are_fine(self) -> None:
        self.assertTrue(legal_square.split_accounts_for(100, [0, 100]))
        self.assertTrue(legal_square.split_accounts_for(100, [58, 46]))

    def test_an_unresolved_half_is_judged_elsewhere(self) -> None:
        # 取り切れなかった側は、そちらで未完了として記録している。
        self.assertTrue(legal_square.split_accounts_for(100, [None, 0]))


class KindContradictionTest(unittest.TestCase):
    """検索した種別と結果の番号が食い違うなら、前の検索の結果を読んでいる。

    栃木市 9/28 は「規則 全期間」が直前の条例と同じ 64 件を返し、上限に
    届かないので取り切れたと扱われ、規則 291 件が一覧から消えた。
    """

    def test_ordinances_returned_for_a_rule_search(self) -> None:
        self.assertTrue(
            legal_square.numbers_contradict_kind("規則", ["条例第3号", "条例第64号", ""])
        )

    def test_matching_numbers_are_fine(self) -> None:
        self.assertFalse(legal_square.numbers_contradict_kind("規則", ["規則第1号", "条例第2号"]))
        self.assertFalse(
            legal_square.numbers_contradict_kind("委員会等規則", ["教育委員会規則第1号"])
        )
        self.assertFalse(legal_square.numbers_contradict_kind("規則／財務", ["規則第9号"]))

    def test_numbers_that_name_no_kind_are_not_judged(self) -> None:
        # 「達第1号」はどちらとも言えない。数えると正しい結果まで捨てる。
        self.assertFalse(legal_square.numbers_contradict_kind("訓令", ["達第1号"]))
        self.assertFalse(legal_square.numbers_contradict_kind("告示", []))

    def test_kinds_without_a_number_word_are_not_judged(self) -> None:
        # 規程・要綱は「訓令第…号」「告示第…号」で出ることが多い。
        self.assertFalse(legal_square.numbers_contradict_kind("規程", ["訓令第1号"]))
        self.assertFalse(legal_square.numbers_contradict_kind("全件", ["条例第1号"]))
