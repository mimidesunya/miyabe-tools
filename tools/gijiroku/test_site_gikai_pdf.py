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


class SameSiteHtmlPageTest(unittest.TestCase):
    """一覧ページを辿る範囲。

    `/site/` を含む CMS だけを見ていたので、使わない自治体では一覧を 1 ページも
    辿れず、入口に並ぶぶんしか取れなかった（一宮町は 83 件の一覧に対して 5 件）。
    """

    def setUp(self):
        from kami_city_pdf import is_same_site_html_page

        self.follows = is_same_site_html_page
        self.start = "https://www.town.ichinomiya.chiba.jp/info/gikai/2/16.html"

    def test_the_site_cms_still_works(self):
        self.assertTrue(
            self.follows(
                "https://www.town.kuriyama.hokkaido.jp/site/gikai/7389.html",
                "https://www.town.kuriyama.hokkaido.jp/site/gikai/7390.html",
            )
        )

    def test_the_parent_listing_is_followed(self):
        self.assertTrue(self.follows(self.start, "https://www.town.ichinomiya.chiba.jp/info/gikai/2/"))
        self.assertTrue(self.follows(self.start, "https://www.town.ichinomiya.chiba.jp/info/gikai/"))

    def test_another_section_of_the_same_site_is_not_followed(self):
        """議会の階層から出ない。"""
        self.assertFalse(
            self.follows(self.start, "https://www.town.ichinomiya.chiba.jp/info/kurashi/1.html")
        )

    def test_another_site_is_not_followed(self):
        self.assertFalse(self.follows(self.start, "https://other.example.jp/info/gikai/2/"))

    def test_a_pdf_is_not_a_listing_page(self):
        self.assertFalse(
            self.follows(self.start, "https://www.town.ichinomiya.chiba.jp/info/gikai/2/x.pdf")
        )
