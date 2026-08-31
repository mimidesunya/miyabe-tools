"""gijiroku.com の会議リンク。

会議へのリンクは取得元によって `ACT=100`（一覧の枝）と `ACT=200`（本文）に
分かれる。`ACT=100` だけを見ていたので、富士市は年度を 144 件辿りながら
1 件も拾えず、旧経路の直近 14 件に落ちていた。台帳は 0 件でないので健全に
見えていた。
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scrapers"))


def looks_like_meeting_link(href: str) -> bool:
    """スクレイパの取り込み条件と同じ判定。"""
    return "voiweb.exe?ACT=" in href and "FINO=" in href


class MeetingLinkTest(unittest.TestCase):
    def test_act_200_is_a_meeting(self):
        """富士市の会議本文。"""
        self.assertTrue(
            looks_like_meeting_link("voiweb.exe?ACT=200&KGNO=619&FINO=1636&UNID=k_R06112710011")
        )

    def test_act_100_is_still_a_meeting(self):
        """つくば市などの一覧の枝。"""
        self.assertTrue(looks_like_meeting_link("voiweb.exe?ACT=100&KENSAKU=1&FINO=1636&PAGE=1"))

    def test_a_link_without_a_meeting_id_is_not(self):
        self.assertFalse(looks_like_meeting_link("voiweb.exe?ACT=100&KENSAKU=1&PAGE=2"))

    def test_an_unrelated_link_is_not(self):
        self.assertFalse(looks_like_meeting_link("../index.asp"))
        self.assertFalse(looks_like_meeting_link("g08v_views.asp?Sflg=31&FYY=2024&TYY=2024"))


class ScraperUsesTheSameRuleTest(unittest.TestCase):
    """試験と実装がずれないよう、実装側の条件を読んで突き合わせる。"""

    def test_the_scraper_accepts_any_act(self):
        source = (Path(__file__).resolve().parent / "scrapers" / "gijiroku_com.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"voiweb.exe?ACT=" in href and "FINO=" in href', source)
        self.assertNotIn('"voiweb.exe?ACT=100" in href and "FINO=" in href', source)
