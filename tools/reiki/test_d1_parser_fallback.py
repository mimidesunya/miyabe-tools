"""本文を包む `USER-SET-STYLE` が無い取得元でも、条文を取り出す。

d1-law の一部の自治体は、段落の div が `<body>` の下へそのまま並んでいる。
パーサは本文の入れ物を `div.USER-SET-STYLE` で探していたので、条文はあるのに
`law-content` が空のまま保存されていた。

本番で 4 自治体・約 4,900 件（石狩市 1,246 / 京都市 1,156 / 江別市 1,019 /
留寿都村 655）。**題名と日付は読めていたので、件数を数えても出てこない。**
本文が 120 字未満の例規を数えて初めて見えた。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scrapers"))

from bs4 import BeautifulSoup  # noqa: E402

import d1_parser  # noqa: E402


FLAT_BODY = """<html><body>
<table><tbody><tr><td><a href="x_m.html">条文目次</a></td></tr></tbody></table>
<div class="danraku-normal">○試験町職員の勤務時間に関する規則</div>
<div class="danraku-normal" style="text-align: right">平成11年３月31日規則第20号</div>
<div class="danraku-normal">（趣旨）</div>
<div class="danraku-normal">第１条 この規則は、勤務時間について必要な事項を定める。</div>
<div class="danraku-normal">第２条 勤務時間は、午前８時30分から午後５時15分までとする。</div>
</body></html>"""

WRAPPED_BODY = """<html><body>
<div class="USER-SET-STYLE">
<div>○試験町規則</div>
<div>第１条 本文がここにある。</div>
</div>
</body></html>"""


class FallbackContainerTest(unittest.TestCase):
    def test_flat_paragraphs_are_used_as_the_body(self) -> None:
        soup = BeautifulSoup(FLAT_BODY, "html.parser")
        self.assertIsNone(soup.find("div", class_="USER-SET-STYLE"))
        container = d1_parser.fallback_content_container(soup)
        self.assertIsNotNone(container)
        text = container.get_text("\n", strip=True)
        self.assertIn("第１条", text)
        self.assertIn("午前８時30分", text)

    def test_no_paragraphs_means_no_container(self) -> None:
        soup = BeautifulSoup("<html><body><p>ただの段落</p></body></html>", "html.parser")
        self.assertIsNone(d1_parser.fallback_content_container(soup))

    def test_the_wrapped_layout_is_untouched(self) -> None:
        # 入れ物がある取得元は今までどおり。フォールバックは呼ばれない。
        soup = BeautifulSoup(WRAPPED_BODY, "html.parser")
        self.assertIsNotNone(soup.find("div", class_="USER-SET-STYLE"))

    def test_parser_version_is_bumped_so_saved_sources_are_reparsed(self) -> None:
        # 原典は変わらないので、世代を上げないと既存の空本文は直らない。
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        import importlib

        d1_law = importlib.import_module("tools.reiki.scrapers.d1_law")
        self.assertGreaterEqual(d1_law.PARSER_VERSION, 2)


if __name__ == "__main__":
    unittest.main()
