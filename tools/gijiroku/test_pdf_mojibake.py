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

    def test_heisei_file_names_carry_a_date_too(self) -> None:
        # 元号の頭文字はファイル名でも URL でも使われる。令和だけを見ていたので
        # 平成の `fileName=H160310A` が読めなかった（kensakusystem で 10,546 件）。
        cases = {
            "https://x/?fileName=R070220A": ("令和6年", "2025-02-20"),
            "https://x/?fileName=H160310A": ("平成16年", "2004-03-10"),
            "https://x/?fileName=H280614B": ("平成28年", "2016-06-14"),
        }
        for url, (label, expected) in cases.items():
            with self.subTest(url=url):
                self.assertEqual(
                    minutes_kind.extract_plausible_held_on(
                        "本文", title="x", year_label=label, filename="downloads/x.txt.gz " + url
                    ),
                    expected,
                )

    def test_title_and_file_name_agreeing_beats_a_body_date(self) -> None:
        # 本文の先頭には招集告示の日など別の日付が載ることがある。曜日まで
        # 揃っているとそちらが勝ってしまう。出雲市は `fileName=H250610A` と
        # 題名「第5号 6月10日」が一致しているのに、本文の 5月27日 が採られていた。
        body = (
            "出雲市議会定例会" + chr(10)
            + "招集告示 平成25年5月27日（月曜日）" + chr(10) + "開議"
        )
        self.assertEqual(
            minutes_kind.extract_plausible_held_on(
                body,
                title="度第2回定例会（第5号 6月10日）",
                year_label="平成25年",
                filename="x.txt.gz https://x/?fileName=H250610A",
            ),
            "2013-06-10",
        )

    def test_a_body_date_still_wins_without_agreement(self) -> None:
        # 題名にもファイル名にも日付が無いなら、本文の日付をそのまま使う。
        body = "出雲市議会定例会" + chr(10) + "平成25年5月27日（月曜日）" + chr(10) + "開議"
        self.assertEqual(
            minutes_kind.extract_plausible_held_on(
                body, title="本文", year_label="平成25年", filename="x.txt.gz"
            ),
            "2013-05-27",
        )

    def test_western_file_names_agree_with_the_title_too(self) -> None:
        # 一致ボーナスは元号のファイル名（`R060610A`）でしか乗っていなかった。
        # `20240610.pdf` `2024-06-10.pdf` `2024.06.10.pdf` でも同じ日を指すので、
        # 題名と一致するなら本文の招集告示より優先する。
        body = "本文" + chr(10) + "招集告示 令和6年5月27日（月曜日）" + chr(10) + "開議"
        title = "第2回定例会（6月10日）"
        for filename in (
            "x.txt.gz https://a/?fileName=R060610A",
            "x.txt.gz https://a/m/20240610.pdf",
            "x.txt.gz https://a/2024.06.10.pdf",
            "x.txt.gz https://a/2024-06-10.pdf",
        ):
            with self.subTest(filename=filename):
                self.assertEqual(
                    minutes_kind.extract_plausible_held_on(
                        body, title=title, year_label="令和6年", filename=filename
                    ),
                    "2024-06-10",
                )

    def test_without_a_file_date_the_body_still_wins(self) -> None:
        body = "本文" + chr(10) + "招集告示 令和6年5月27日（月曜日）" + chr(10) + "開議"
        self.assertEqual(
            minutes_kind.extract_plausible_held_on(
                body,
                title="第2回定例会（6月10日）",
                year_label="令和6年",
                filename="x.txt.gz https://a/abc.pdf",
            ),
            "2024-05-27",
        )

    def test_empty_stays_empty(self) -> None:
        self.assertEqual(minutes_kind.repair_cp932_mojibake(""), "")


if __name__ == "__main__":
    unittest.main()
