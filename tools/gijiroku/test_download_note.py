"""リンク文言の「をダウンロードする」は会議の名前ではない。

川本町 168 件・大江町 50 件の題名が
`「予算特別委員会（令和3年3月9日～11日）」をダウンロードする` の形で公開に
出ていた。括弧の中は会議の名前として使える。操作の説明だけ落とす。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from minutes_kind import strip_download_note, strip_pdf_notes  # noqa: E402


class StripDownloadNoteTest(unittest.TestCase):
    def test_unwraps_the_quoted_name(self):
        self.assertEqual(
            strip_pdf_notes("「予算特別委員会（令和3年3月9日～11日）」をダウンロードする"),
            "予算特別委員会（令和3年3月9日～11日）",
        )

    def test_drops_a_dangling_close_quote(self):
        """保存名が開き括弧を落としていることがある。"""
        self.assertEqual(
            strip_pdf_notes("令和5年12月13日：本山議員」をダウンロードする"),
            "令和5年12月13日：本山議員",
        )

    def test_keeps_a_matched_pair_inside(self):
        self.assertEqual(strip_download_note("第1回「特別」委員会をダウンロード"), "第1回「特別」委員会")

    def test_a_note_only_label_is_left_alone(self):
        """落とすと何も残らないなら、落とさない。"""
        self.assertEqual(strip_pdf_notes("をダウンロードする"), "をダウンロードする")

    def test_an_ordinary_title_is_untouched(self):
        self.assertEqual(strip_pdf_notes("令和3年第2回定例会会議録"), "令和3年第2回定例会会議録")
