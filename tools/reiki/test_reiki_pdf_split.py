"""1 本の PDF で公開された例規集の切り出し（海士町・知夫村）。

2026-09-19 に取り込みを始めたときに出た誤りを固定する。
- 廃止編には書き出しの同じ題名が並ぶ。題名の先頭 12 字だけで照らしていたので、
  「固定資産税の課税免除に関する条例を廃止する条例」の位置に
  「固定資産税の課税免除に関する条例施行規則を廃止する規則」を拾っていた
- 空行をまたいで行をつないでいたので、公布日の括弧が本文とつながり、
  公布日が 1 割近く取れていなかった
- 本文中の「…の全部を改正する。」の「改正する。」を沿革の注記と見て段落を切っていた
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "scrapers"))

import reiki_pdf  # noqa: E402


class TitleLineTest(unittest.TestCase):
    def test_a_title_sharing_its_opening_is_not_taken(self):
        lines = [
            "○固定資産税の課税免除に関する条例施行規則を廃止",
            "する規則 ",
            " ",
            "○固定資産税の課税免除に関する条例を廃止する条例 ",
        ]
        self.assertEqual(reiki_pdf._title_line_index(lines, "○固定資産税の課税免除に関する条例を廃止する条例"), 3)
        self.assertEqual(reiki_pdf._title_line_index(lines, "○固定資産税の課税免除に関する条例施行規則を廃止する規則"), 0)

    def test_a_bookmark_that_lost_characters_still_finds_the_line(self):
        lines = ["○知夫村提携創業保証「創業応援」制度要綱 "]
        self.assertEqual(reiki_pdf._title_line_index(lines, "○知夫村提携創業保証「 」制度要綱"), 0)


class ParagraphTest(unittest.TestCase):
    def test_a_blank_line_ends_the_paragraph(self):
        lines = [
            "○知夫村役場の位置を変更する条例 ",
            " ",
            "（昭和39年10月23日知夫村条例第19号） ",
            " ",
            "知夫村役場の位置を次のとおり変更するものとする。 ",
        ]
        self.assertEqual(
            reiki_pdf._paragraphs(lines),
            ["○知夫村役場の位置を変更する条例", "（昭和39年10月23日知夫村条例第19号）", "知夫村役場の位置を次のとおり変更するものとする。"],
        )

    def test_a_wrapped_line_joins_its_paragraph(self):
        lines = [
            "（令和５年６月19日知夫村要綱第13号）",
            " ",
            "知夫村国民健康保険人間ドック助成事業補助金交付要綱（平成17年知夫村要綱第２号）の全部を",
            "改正する。",
            "   改正（平８条例第７号） ",
        ]
        self.assertEqual(
            reiki_pdf._paragraphs(lines)[1:],
            [
                "知夫村国民健康保険人間ドック助成事業補助金交付要綱（平成17年知夫村要綱第２号）の全部を改正する。",
                "改正（平８条例第７号）",
            ],
        )


class ParseTest(unittest.TestCase):
    def test_the_promulgation_line_gives_date_and_number(self):
        raw = "<p>○海士町公告式条例</p>\n<p>（昭和27年４月２日海士町条例第103号）</p>\n<p>（趣旨）</p>"
        parsed = reiki_pdf.parse_article(raw, "")
        self.assertEqual(parsed.title, "海士町公告式条例")
        self.assertEqual(parsed.date_text, "昭和27年４月２日")
        self.assertEqual(parsed.number, "海士町条例第103号")
        self.assertEqual(parsed.content_html, "<p>（趣旨）</p>")


if __name__ == "__main__":
    unittest.main()
