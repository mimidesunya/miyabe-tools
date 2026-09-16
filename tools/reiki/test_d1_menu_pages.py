"""入口ページが指している目次を辿る。

新しい Reiki-Base は `reiki_kana/kana_default.html` のような下位ディレクトリに
目次を置く。古い版の `mokuji_index_index.html` を決め打ちしていたので、
牛久市・福岡市のように入口ページが 200 でも目録が 1 件も開けなかった。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scrapers"))

from d1_law import (  # noqa: E402
    PLAIN_HONBUN_LINK_RE,
    REIKI_HONBUN_LINK_RE,
    branch_pages,
    menu_pages_from_entry,
)


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


class BranchPagesTest(unittest.TestCase):
    """目次の枝を辿る。

    古い静的版は `href=bunya_0010000.html` と引用符を付けない（錦江町・猿払村）。
    引用符付きしか読めていなかったので、目次は開けるのに例規 ID が 1 件も
    集まらず、毎周回 `No regulations were collected` で失敗していた。
    """

    def test_unquoted_branch_links(self):
        html = (
            '<li TYPE=square><a href=bunya_0010000.html target=FRAME_MOKUJI_RIGHT '
            'onclick="javascript:ViewMokujiList(\'0010000\',\'IMGBOOK1\')">第１編 総規</a></li>'
            '<li TYPE=square><a href=bunya_00100000010000.html target=FRAME_MOKUJI_RIGHT>第１章</a></li>'
        )
        self.assertEqual(branch_pages(html), ["bunya_0010000.html", "bunya_00100000010000.html"])

    def test_quoted_branch_links_still_work(self):
        html = '<a href="bunya_0020000.html">第２編</a><a href=\'index_002.html\'>あ</a>'
        self.assertEqual(branch_pages(html), ["bunya_0020000.html", "index_002.html"])

    def test_body_pages_are_not_branches(self):
        """本文ページは目次ではない。目録として開くと無駄に取りに行く。"""
        html = '<a href=H417901010001/H417901010001_j.html>錦江町役場の位置を定める条例</a>'
        self.assertEqual(branch_pages(html), [])

    def test_other_sites_are_not_followed(self):
        html = '<a href=http://example.jp/other/bunya_0010000.html>x</a>'
        self.assertEqual(branch_pages(html), [])

    def test_no_duplicates(self):
        html = '<a href=bunya_0010000.html>a</a><a href="bunya_0010000.html">b</a>'
        self.assertEqual(branch_pages(html), ["bunya_0010000.html"])


class PlainHonbunLinkTest(unittest.TestCase):
    """例規を JavaScript ではなく普通のリンクで並べる取得元（京都市・留寿都村）。"""

    def test_unquoted_body_link(self):
        html = '<a href=H417901010001/H417901010001_j.html target=_blank>条例</a>'
        self.assertEqual([hno for hno, _ in PLAIN_HONBUN_LINK_RE.findall(html)], ["H417901010001"])

    def test_quoted_body_link(self):
        html = '<a href="H417901010002/H417901010002_j.html">条例</a>'
        self.assertEqual([hno for hno, _ in PLAIN_HONBUN_LINK_RE.findall(html)], ["H417901010002"])

    def test_mismatched_pair_is_not_a_body_link(self):
        html = '<a href="H417901010002/H999_j.html">条例</a>'
        self.assertEqual(PLAIN_HONBUN_LINK_RE.findall(html), [])


class ReikiHonbunLinkTest(unittest.TestCase):
    """新しい Reiki-Base は本文を `../reiki_honbun/x000RG….html` に置く。"""

    def test_unquoted_and_quoted(self):
        self.assertEqual(
            REIKI_HONBUN_LINK_RE.findall('<a href=../reiki_honbun/z500RG00000122.html>x</a>'),
            ["../reiki_honbun/z500RG00000122.html"],
        )
        self.assertEqual(
            REIKI_HONBUN_LINK_RE.findall('<a href="reiki_honbun/c534RG00000016.html">x</a>'),
            ["reiki_honbun/c534RG00000016.html"],
        )
