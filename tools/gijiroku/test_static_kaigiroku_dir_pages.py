#!/usr/bin/env python3
"""静的ディレクトリ型の会議録で、一覧と本文を取り違えないことを確かめる。

ときがわ町は `/gijiroku/` の下に年別一覧（`h30.html`）と日ごとの本文
（`r08/01230101.htm`）を並べる。年別一覧にも会議名と日付が並ぶので、
語だけで見ると本文と区別が付かない。一覧を本文として取り込むと、
会議録の中身が入らないまま件数だけ増える。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

SCRAPER_DIR = Path(__file__).resolve().parent / "scrapers"
sys.path.insert(0, str(SCRAPER_DIR))

import static_kaigiroku_dir as static_dir  # noqa: E402
import kami_city_pdf  # noqa: E402


INDEX_HTML = """
<html><body>
<a href="r08/01230100.htm">目次</a>
<a href="r08/01230101.htm">１月２３日（開会、議案上程、説明、質疑、討議、討論、採決、閉会）</a>
<a href="r08/02270100.htm">目次</a>
<a href="r08/02270101.htm">２月２７日（開会、議案上程、説明、質疑、討議、討論、採決、閉会）</a>
<a href="r08/03040000.htm">目次</a>
<a href="r08/03040001.htm">３月４日（開会、議案上程、説明、質疑、討議、討論、採決）</a>
</body></html>
"""

BODY_TEXT = (
    "ときがわ町告示第４号\n"
    "令和８年第１回ときがわ町議会臨時会を下記のとおり招集する。\n"
    "議事日程（第１号）\n"
    "出席議員（１２名）\n"
    "欠席議員（なし）\n"
    "会議録署名議員の指名について\n"
    "議長　　開議を宣します。\n"
    "○議案第１号について、質疑を許します。\n"
    "討論を省略し、採決いたします。\n"
) * 40


class IndexPageTest(unittest.TestCase):
    def test_link_heavy_short_page_is_not_a_document(self) -> None:
        soup = BeautifulSoup(INDEX_HTML, "html.parser")
        ratio = static_dir.link_text_ratio(soup)
        text = static_dir.text_from_html(BeautifulSoup(INDEX_HTML, "html.parser"))
        self.assertGreater(ratio, static_dir.INDEX_PAGE_LINK_TEXT_RATIO)
        self.assertFalse(
            static_dir.looks_like_html_minutes_document(
                "", "https://example.lg.jp/gijiroku/h30.html", text * 3, ratio
            )
        )

    def test_long_body_is_a_document_even_with_links(self) -> None:
        # 長い文書は割合に関係なく通す。本文の中に索引が混ざる取得元がある。
        self.assertTrue(
            static_dir.looks_like_html_minutes_document(
                "", "https://example.lg.jp/gijiroku/r08/01230101.htm", BODY_TEXT, 0.9
            )
        )

    def test_body_without_links_is_a_document(self) -> None:
        self.assertTrue(
            static_dir.looks_like_html_minutes_document(
                "", "https://example.lg.jp/gijiroku/r08/01230101.htm", BODY_TEXT, 0.0
            )
        )

    def test_short_page_is_still_refused(self) -> None:
        self.assertFalse(
            static_dir.looks_like_html_minutes_document(
                "", "https://example.lg.jp/gijiroku/r08/01230101.htm", "短い", 0.0
            )
        )


class EraDirectoryYearTest(unittest.TestCase):
    """題名が空でも、元号の略記ディレクトリから年を読む。"""

    def test_reads_year_from_the_directory(self) -> None:
        self.assertEqual(
            kami_city_pdf.extract_year_info(
                "", "https://www.town.tokigawa.lg.jp/div/203010/htm/gijiroku/r08/01230101.htm"
            ),
            ("令和8年", 2026),
        )
        self.assertEqual(
            kami_city_pdf.extract_year_info(
                "", "https://example.lg.jp/gijiroku/h30/0301.htm"
            ),
            ("平成30年", 2018),
        )

    def test_title_wins_over_the_directory(self) -> None:
        # 題名から読めるならそちらを使う。ディレクトリは最後の手段。
        self.assertEqual(
            kami_city_pdf.extract_year_info(
                "令和7年第2回定例会", "https://example.lg.jp/gijiroku/r08/0301.htm"
            ),
            ("令和7年", 2025),
        )

    def test_unrelated_paths_stay_unknown(self) -> None:
        for url in (
            "https://example.lg.jp/gijiroku/index.html",
            "https://example.lg.jp/gijiroku/r8/0301.htm",
            "https://example.lg.jp/gijiroku/x08/0301.htm",
        ):
            self.assertEqual(kami_city_pdf.extract_year_info("", url), ("不明", None), url)


if __name__ == "__main__":
    unittest.main()
