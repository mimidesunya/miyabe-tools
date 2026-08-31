"""自治体サイトに直接置かれた会議録 PDF。

`#main_body .detail_free` と `/uploaded/attachment/` を決め打ちしていた。
その形でないサイトでは PDF が 95 件並んでいても候補 0 件になり、しかも
**成功として終わっていた**（南種子町・御宿町・一宮町）。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scrapers"))

from kami_city_pdf import SITE_ATTACHMENT_DIRS, is_site_attachment_pdf  # noqa: E402


class SiteAttachmentTest(unittest.TestCase):
    def test_the_original_layout_still_passes(self):
        self.assertTrue(
            is_site_attachment_pdf("https://example.jp/uploaded/attachment/12345.pdf")
        )

    def test_assets_files_passes(self):
        """南種子町 `assets/files/pdf/gikai/R8dai2kai….pdf`。"""
        self.assertTrue(
            is_site_attachment_pdf(
                "http://www.town.minamitane.kagoshima.jp/assets/files/pdf/gikai/R8dai2kai.pdf"
            )
        )

    def test_content_files_passes(self):
        """御宿町 `content/files/gikaijimukyoku/…`。"""
        self.assertTrue(
            is_site_attachment_pdf(
                "https://www.town.onjuku.chiba.jp/content/files/gikaijimukyoku/x.pdf"
            )
        )

    def test_a_non_pdf_is_rejected(self):
        self.assertFalse(is_site_attachment_pdf("https://example.jp/assets/files/x.html"))

    def test_a_pdf_outside_the_known_places_is_rejected(self):
        """どこに置かれた PDF でも通すわけではない。"""
        self.assertFalse(is_site_attachment_pdf("https://example.jp/kouhou/2026-03.pdf"))

    def test_every_known_place_is_a_directory(self):
        for directory in SITE_ATTACHMENT_DIRS:
            self.assertTrue(directory.startswith("/"), directory)
            self.assertTrue(directory.endswith("/"), directory)
