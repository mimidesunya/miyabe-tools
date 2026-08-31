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

    def test_a_japanese_header_before_the_mojibake_is_kept(self) -> None:
        # 保存ファイルはスクレイパが日本語で `出典:` と題名を足してから
        # PDF 本文を繋げる。全体をまとめて読み直すと、日本語が 1 字でもあれば
        # latin-1 へ戻せず、化けた本文がそのまま残る。行ごとに直す。
        original = "小海町議会定例会会議録" + chr(10) + "平成27年6月9日" + chr(10) + "開議 午前10時"
        mixed = "不明" + chr(10) + "pdf" + chr(10) + "出典: https://example.jp/a.pdf" + chr(10) + chr(10) + sjis_as_latin1(original)
        repaired = minutes_kind.repair_cp932_mojibake(mixed)
        self.assertIn("出典: https://example.jp/a.pdf", repaired)
        self.assertIn("小海町議会定例会会議録", repaired)
        self.assertIn("開議 午前10時", repaired)

    def test_glyph_name_bodies_are_not_minutes(self) -> None:
        # PDF の抽出に失敗するとグリフ名が並ぶ。垂水市の本文 190 万字は
        # `/g5140 /g5777 /g14814` で始まる。ラテン補助は 0 なので
        # 文字化けの判定には当たらないが、日本語としては読めない。
        glyphs = "/g5140 /g5777 /g14814/g14817 " * 80
        self.assertEqual(
            minutes_kind.non_minutes_reason("会議録", glyphs), "unreadable_glyph_names"
        )
        real = "垂水市議会会議録" + chr(10) + "開議 午前10時" + chr(10) + "出席議員 15名" + chr(10) + "議員の発言。" * 50
        self.assertIsNone(minutes_kind.non_minutes_reason("会議録", real))

    def test_empty_stays_empty(self) -> None:
        self.assertEqual(minutes_kind.repair_cp932_mojibake(""), "")


if __name__ == "__main__":
    unittest.main()
