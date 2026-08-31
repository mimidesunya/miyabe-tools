"""PDF から取り出した Shift_JIS を 1 バイト 1 文字として保存しない。

pypdf は、ToUnicode を持たない CID フォント（日本語 PDF の 90ms-RKSJ-H など）
から Shift_JIS のバイト列をそのまま返す。そのまま保存すると本文が文字化けし、
**日本語では二度と検索に当たらない。**題名と件数は正しいので、収録件数では
出てこない。

本番の小海町で、本文が `平 成 ２ ７ 年` ではなく
`\x95\xbd \x90\xac \x82Q \x82V \x94N` になっていた。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.gijiroku import minutes_kind  # noqa: E402


def sjis_as_latin1(text: str) -> str:
    """cp932 のバイト列を 1 バイト 1 文字として読んだ形を作る。"""
    return text.encode("cp932").decode("latin-1")


class Cp932MojibakeTest(unittest.TestCase):
    def test_a_mojibake_body_is_read_again(self) -> None:
        original = "小海町議会定例会会議録\n平成27年6月9日\n開議 午前10時"
        broken = sjis_as_latin1(original)
        self.assertNotEqual(broken, original)
        self.assertEqual(minutes_kind.repair_cp932_mojibake(broken), original)

    def test_a_healthy_body_is_left_alone(self) -> None:
        body = "小海町議会定例会会議録\n平成27年6月9日\n開議"
        self.assertEqual(minutes_kind.repair_cp932_mojibake(body), body)

    def test_an_english_body_is_left_alone(self) -> None:
        body = "Minutes of the council meeting, 2026-06-09."
        self.assertEqual(minutes_kind.repair_cp932_mojibake(body), body)

    def test_repair_is_refused_when_it_would_not_help(self) -> None:
        # ラテン補助が多くても、読み直して日本語が増えないなら直さない。
        body = "éèêüöä " * 40
        self.assertEqual(minutes_kind.repair_cp932_mojibake(body), body)

    def test_empty_stays_empty(self) -> None:
        self.assertEqual(minutes_kind.repair_cp932_mojibake(""), "")


if __name__ == "__main__":
    unittest.main()
