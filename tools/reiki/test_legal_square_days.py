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
        self.assertEqual(legal_square.next_title_split_word(words), "")

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
