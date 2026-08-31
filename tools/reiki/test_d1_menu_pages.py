"""入口ページが指している目次を辿る。

新しい Reiki-Base は `reiki_kana/kana_default.html` のような下位ディレクトリに
目次を置く。古い版の `mokuji_index_index.html` を決め打ちしていたので、
牛久市・福岡市のように入口ページが 200 でも目録が 1 件も開けなかった。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scrapers"))

from d1_law import menu_pages_from_entry  # noqa: E402


class MenuPagesFromEntryTest(unittest.TestCase):
    def test_reads_the_three_menus_in_order(self):
        html = (
            '<li><a href="reiki_taikei/taikei_default.html">体系目次</a></li>'
            '<li><a href="reiki_kana/kana_default.html">五十音順目次</a></li>'
            '<li><a href="reiki_miseko/miseko_default.html">未施行の例規</a></li>'
        )
        self.assertEqual(
            menu_pages_from_entry(html),
            [
                "reiki_taikei/taikei_default.html",
                "reiki_kana/kana_default.html",
                "reiki_miseko/miseko_default.html",
            ],
        )

    def test_ignores_stylesheets_and_scripts(self):
        html = '<link href="css/base.css"><script src="js/jquery.js"></script>'
        self.assertEqual(menu_pages_from_entry(html), [])

    def test_ignores_absolute_links(self):
        """別サイトへ出ていかない。"""
        html = '<a href="https://example.jp/reiki_kana/kana_default.html">x</a>'
        self.assertEqual(menu_pages_from_entry(html), [])

    def test_keeps_the_old_layout(self):
        html = '<a href="mokuji_index_index.html">目次</a>'
        self.assertEqual(menu_pages_from_entry(html), ["mokuji_index_index.html"])

    def test_no_duplicates(self):
        html = '<a href="reiki_kana/kana_default.html">a</a><a href="reiki_kana/kana_default.html">b</a>'
        self.assertEqual(menu_pages_from_entry(html), ["reiki_kana/kana_default.html"])


class GuessedMenuTest(unittest.TestCase):
    """目次を推測で辿ったかどうかを残す。

    取得元が目次の名前を変えたとき、次に壊れるのは決め打ちで拾えている
    自治体である。牛久市・福岡市はそれで例規 0 件になった。壊れてから
    探すのではなく、推測に頼っている自治体を一覧で見えるようにする。
    """

    def test_a_declared_menu_is_not_a_guess(self):
        html = '<li><a href="reiki_kana/kana_default.html">五十音順目次</a></li>'
        self.assertEqual(menu_pages_from_entry(html), ["reiki_kana/kana_default.html"])

    def test_no_menu_link_means_we_are_guessing(self):
        self.assertEqual(menu_pages_from_entry('<link href="css/base.css">'), [])
