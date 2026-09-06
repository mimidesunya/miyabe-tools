#!/usr/bin/env python3
"""会議録の一覧へ降りるリンクの見分け方を確かめる。

取得元によって書き方が違うだけで 1 件も取れなくなる。ここに並ぶのは
実際に 0 件で止まっていた自治体の形である。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRAPER_DIR = Path(__file__).resolve().parent / "scrapers"
sys.path.insert(0, str(SCRAPER_DIR))

import kami_city_pdf  # noqa: E402


class MinutesPageKeywordTest(unittest.TestCase):
    def test_kaigi_kiroku_is_a_minutes_page(self) -> None:
        # 浦幌町は「会議記録」と書く。「会議録」を含まないので、
        # 語を足すまで年別一覧へ降りられなかった。
        self.assertTrue(
            kami_city_pdf.looks_like_generic_minutes_page(
                "令和8年浦幌町議会会議記録",
                "https://www.urahoro.jp/council/?content=3059",
            )
        )

    def test_notice_pages_are_still_refused(self) -> None:
        for text in ("議会だより", "本会議の録画中継", "議員名簿", "政務活動費"):
            self.assertFalse(
                kami_city_pdf.looks_like_generic_minutes_page(
                    text, "https://example.lg.jp/gikai/x.html"
                ),
                text,
            )


class YearAnchorTest(unittest.TestCase):
    def test_year_labels_from_real_sources(self) -> None:
        cases = {
            "2026年": True,      # 岐南町
            "R8年度": True,       # 東峰村
            "H30年度": True,      # 東峰村
            "令和7年": True,      # 小竹町
            "平成31年度": True,
            "2026年度予算": False,
            "議会だより": False,
        }
        for text, expected in cases.items():
            self.assertEqual(
                bool(kami_city_pdf.YEAR_ONLY_ANCHOR_RE.match(text)), expected, text
            )

    def test_year_anchor_only_counts_from_a_minutes_page(self) -> None:
        # 年だけのリンクは、会議録のページから辿るときだけ通す。
        # どこでも通すと、年で分かれているだけの無関係なページへ広がる。
        url = "https://www.town.ginan.lg.jp/5800.htm"
        self.assertFalse(kami_city_pdf.looks_like_generic_minutes_page("2026年", url))
        self.assertTrue(kami_city_pdf.looks_like_generic_minutes_page("2026年", url, True))


if __name__ == "__main__":
    unittest.main()
