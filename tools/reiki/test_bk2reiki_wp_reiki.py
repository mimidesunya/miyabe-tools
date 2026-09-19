"""bk2reiki（西宮市）と WordPress 例規（日吉津村）の本文解析。

どちらも 2026-09-19 に登録簿の除外（login_required / unsupported_system）を
外して取り込み始めた取得元。取得元の HTML の形を固定する。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "scrapers"))

import bk2reiki  # noqa: E402
import wp_reiki  # noqa: E402


BK2_LIST = (
    '<div id="ditem50000003" class="bk2_ditemKenmei bk2_KenmeiSea"><p class="bk2_SearchTitle ">'
    '<a class="bk2_KenmeiLink" target="honbun" href="../doc/docopen.php?typ=50&file=50000003&date=20120329&key=">'
    '<img src="./image/icon_reiki.gif" width="16" height="16" alt="資料50" border="0"/>西宮市民憲章</a>'
    '（昭和４５年１１月３日西宮市告示甲第９４号）</p><p class="bk2_SearchMokuji">'
    '<span class="bk2_SearchMokujiTitle">体系</span>　第１編　総　規 > 市　制</p></div>'
)

BK2_BODY = (
    '<html><body><div id="honbun" style="font-size:100%;"><h1>西宮市民憲章</h1>'
    '<div class="seitei">（昭和４５年１１月３日）<br>（西宮市告示甲第９４号）</div>'
    '<div class="n0">美しい風光と豊かな伝統のまち</div></div></body></html>'
)

WP_PAGE = (
    '<h1 class="reiki-main-title">日吉津村の事務所位置条例</h1><div class="reiki-body-content">'
    '<!-- REIKI_CAT: 第1編総規 第1章村制 --><div id="primaryInner2">'
    '<p class="title-irregular"><span>○日吉津村の事務所位置条例</span></p>'
    '<p class="date"><span>昭和27年9月26日</span></p><p class="number"><span>条例第41号</span></p>'
    '<p class="p">本文</p></div></div>'
)


class Bk2ReikiTest(unittest.TestCase):
    def test_the_search_list_gives_the_document_and_taxonomy(self):
        found = bk2reiki.ITEM_RE.findall(BK2_LIST)
        self.assertEqual(len(found), 1)
        query, title, _promulgation, taxonomy = found[0]
        self.assertIn("file=50000003", query)
        self.assertEqual(bk2reiki._plain(title), "西宮市民憲章")
        self.assertEqual(bk2reiki._plain(taxonomy).removeprefix("体系").strip(), "第１編 総 規 > 市 制")

    def test_the_body_gives_date_and_number(self):
        parsed = bk2reiki.parse_article(BK2_BODY, "u")
        self.assertEqual(parsed.title, "西宮市民憲章")
        self.assertEqual(parsed.date_text, "昭和４５年１１月３日")
        self.assertEqual(parsed.number, "西宮市告示甲第９４号")


class WpReikiTest(unittest.TestCase):
    def test_the_post_gives_title_date_number_and_taxonomy(self):
        parsed = wp_reiki.parse_article(WP_PAGE, "u")
        self.assertEqual(parsed.title, "日吉津村の事務所位置条例")
        self.assertEqual(parsed.date_text, "昭和27年9月26日")
        self.assertEqual(parsed.number, "条例第41号")
        self.assertEqual(parsed.taxonomy_path, "第1編総規 > 第1章村制")


if __name__ == "__main__":
    unittest.main()
