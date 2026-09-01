"""新しい Reiki-Base の本文。

`USER-SET-STYLE` も `danraku-normal` も無く、本文は `div#primary.joubun` に
ある。牛久市 1,001 件・福岡市 1,136 件は、題名と日付は読めるのに本文が空
だった。**件数では出てこない。**公開はされていて、中身だけが無い。
"""

import sys
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent / "scrapers"))

from d1_parser import fallback_content_container, reiki_base_content_container  # noqa: E402


class ReikiBaseContentTest(unittest.TestCase):
    def test_reads_the_primary_container(self):
        soup = BeautifulSoup(
            '<div id="primary" class="joubun"><div class="clause">第1条 この条例は…</div></div>',
            "html.parser",
        )
        node = reiki_base_content_container(soup)
        self.assertIsNotNone(node)
        self.assertIn("第1条", node.get_text())

    def test_an_empty_container_is_not_content(self):
        soup = BeautifulSoup('<div id="primary" class="joubun">  </div>', "html.parser")
        self.assertIsNone(reiki_base_content_container(soup))

    def test_a_page_without_it_returns_none(self):
        soup = BeautifulSoup("<div>本文</div>", "html.parser")
        self.assertIsNone(reiki_base_content_container(soup))

    def test_the_old_layout_still_uses_its_own_path(self):
        """`danraku-normal` の取得元は、これまでどおり拾える。"""
        soup = BeautifulSoup(
            '<div class="danraku-normal">第1条 この条例は…</div>', "html.parser"
        )
        node = fallback_content_container(soup)
        self.assertIsNotNone(node)
        self.assertIn("第1条", node.get_text())
