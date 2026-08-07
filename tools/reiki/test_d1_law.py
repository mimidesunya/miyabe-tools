#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRAPER_DIR = Path(__file__).resolve().parent / "scrapers"
sys.path.insert(0, str(SCRAPER_DIR))

import d1_law  # noqa: E402


class D1LawBaseUrlTest(unittest.TestCase):
    def test_discovers_static_reiki_root_from_landing_page(self) -> None:
        source_url = "https://en3-jg.d1-law.com/kagawa-ken/index.htm"
        source_html = '<frame src="d1w_reiki/mokuji_bunya.html">'

        self.assertEqual(
            d1_law.discover_d1_law_base_url(source_url, source_html),
            "https://en3-jg.d1-law.com/kagawa-ken/d1w_reiki/",
        )

    def test_keeps_direct_static_reiki_url(self) -> None:
        source_url = "https://example.d1-law.com/city/d1w_reiki/reiki.html"

        self.assertEqual(
            d1_law.discover_d1_law_base_url(source_url, "<html></html>"),
            "https://example.d1-law.com/city/d1w_reiki/",
        )


if __name__ == "__main__":
    unittest.main()
