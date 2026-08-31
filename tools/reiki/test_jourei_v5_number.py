"""jourei-v5 の制定番号。枝番が付いた形。

えびの市「宮崎県市町村総合事務組合規約」の制定表記は
`(平成元年7月1日宮崎県指令第217号の328)` である。`号` の直後に閉じ括弧を
求めていたので、この括弧ごと落ちて公布日が空になっていた。
えびの 22 件・産山村 17 件がこの形。
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scrapers"))

from jourei_v5 import DATE_LINE_RE  # noqa: E402


class JoureiV5NumberTest(unittest.TestCase):
    def test_a_branch_number_still_matches(self):
        found = DATE_LINE_RE.search("(平成元年7月1日宮崎県指令第217号の328)")
        self.assertIsNotNone(found)
        self.assertEqual(found.group(1), "平成元年7月1日宮崎県指令第217号の328")

    def test_a_plain_number_still_matches(self):
        found = DATE_LINE_RE.search("(令和6年11月1日教委告示第2号)")
        self.assertIsNotNone(found)
        self.assertEqual(found.group(1), "令和6年11月1日教委告示第2号")

    def test_a_nested_branch_number(self):
        found = DATE_LINE_RE.search("(平成7年3月1日告示第1号の2の3)")
        self.assertIsNotNone(found)
        self.assertEqual(found.group(1), "平成7年3月1日告示第1号の2の3")

    def test_a_line_without_a_number_is_not_taken(self):
        self.assertIsNone(DATE_LINE_RE.search("(令和6年11月1日教委告示)"))
