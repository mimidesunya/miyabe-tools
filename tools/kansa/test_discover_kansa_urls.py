"""包括外部監査の見分け方を固定する。

同じ「監査」でも、定例監査・決算審査・住民監査請求・内部統制は包括外部監査
ではない。これらを公開ありと数えると、義務付けの 129 団体以外にも「ある」と
言ってしまう。個別外部監査は制度としては近いが別物なので、区別して残す。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(WORKSPACE_ROOT))
sys.path.append(str(Path(__file__).resolve().parent))

from tools.kansa import discover_kansa_urls as kansa  # noqa: E402
from tools.kansa.core_cities import (  # noqa: E402
    CORE_CITY_CODES,
    DESIGNATED_CITY_CODES,
    mandatory_codes,
    mandatory_kind,
)


def page(title: str, body: str = "") -> str:
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>"


class ScorePageTest(unittest.TestCase):
    def test_link_label_naming_it_is_enough(self) -> None:
        confidence, evidence, individual = kansa.score_page(
            "https://example.lg.jp/kansa/", page("監査委員"), "包括外部監査結果報告書"
        )
        self.assertEqual(confidence, "high")
        self.assertIn("包括外部監査", evidence)
        self.assertFalse(individual)

    def test_title_naming_it_is_enough(self) -> None:
        confidence, _evidence, _individual = kansa.score_page(
            "https://example.lg.jp/x/", page("包括外部監査の結果について"), "監査"
        )
        self.assertEqual(confidence, "high")

    def test_body_mention_is_medium(self) -> None:
        html = page("監査委員事務局", "令和7年度の包括外部監査結果報告書を公表します。")
        confidence, _evidence, _individual = kansa.score_page(
            "https://example.lg.jp/kansa/", html, "監査結果"
        )
        self.assertEqual(confidence, "medium")

    def test_routine_audit_is_not_comprehensive(self) -> None:
        """定例監査・決算審査だけのページを公開ありと数えない。"""
        html = page("定例監査の結果", "定例監査及び決算審査の意見を公表します。")
        confidence, _evidence, _individual = kansa.score_page(
            "https://example.lg.jp/kansa/teirei/", html, "定例監査"
        )
        self.assertEqual(confidence, "none")

    def test_resident_audit_request_is_not_comprehensive(self) -> None:
        html = page("住民監査請求", "住民監査請求の手続きについて")
        confidence, _evidence, _individual = kansa.score_page(
            "https://example.lg.jp/kansa/seikyu/", html, "住民監査請求"
        )
        self.assertEqual(confidence, "none")

    def test_internal_control_is_not_comprehensive(self) -> None:
        html = page("内部統制評価報告書", "内部統制の評価結果を公表します。")
        confidence, _evidence, _individual = kansa.score_page(
            "https://example.lg.jp/naibu/", html, "内部統制"
        )
        self.assertEqual(confidence, "none")

    def test_individual_external_audit_is_marked_not_counted(self) -> None:
        """個別外部監査は別制度。包括があるとは限らないので印を残す。"""
        html = page("外部監査", "個別外部監査の請求について説明します。")
        confidence, _evidence, individual = kansa.score_page(
            "https://example.lg.jp/gaibu/", html, "外部監査"
        )
        self.assertEqual(confidence, "low")
        self.assertTrue(individual)

    def test_audit_hub_without_the_word_stays_low(self) -> None:
        html = page("監査委員", "監査委員の紹介と監査結果の公表について")
        confidence, _evidence, individual = kansa.score_page(
            "https://example.lg.jp/kansa/", html, "監査委員"
        )
        self.assertEqual(confidence, "low")
        self.assertFalse(individual)

    def test_a_page_listing_both_is_not_dropped(self) -> None:
        """監査委員の頁が定例監査と包括外部監査を並べていても落とさない。"""
        html = page("監査結果の公表", "定例監査、決算審査、包括外部監査の結果を掲載しています。")
        confidence, _evidence, _individual = kansa.score_page(
            "https://example.lg.jp/kansa/", html, "監査結果"
        )
        self.assertEqual(confidence, "medium")


class LinkPriorityTest(unittest.TestCase):
    def test_comprehensive_label_is_first(self) -> None:
        self.assertEqual(kansa.link_priority("包括外部監査", "https://example.lg.jp/a/"), 100)

    def test_routine_audit_label_is_not_followed(self) -> None:
        self.assertEqual(kansa.link_priority("定例監査の結果", "https://example.lg.jp/a/"), 0)

    def test_audit_hub_is_followed(self) -> None:
        self.assertGreater(kansa.link_priority("監査委員", "https://example.lg.jp/kansa/"), 0)

    def test_unrelated_label_is_not_followed(self) -> None:
        self.assertEqual(kansa.link_priority("イベント情報", "https://example.lg.jp/event/"), 0)

    def test_bare_audit_label_is_followed(self) -> None:
        """札幌市は監査の入口のリンク文字が「監査」だけ。ここで止まると届かない。"""
        self.assertGreater(
            kansa.link_priority("監査", "https://www.city.sapporo.jp/kansa/index.html"), 0
        )

    def test_periodic_audit_is_still_dropped(self) -> None:
        """「定期監査」と書く自治体がある。定例監査だけでは落とせない。"""
        self.assertEqual(
            kansa.link_priority("定期監査（行政監査）の結果", "https://x.lg.jp/kansa/keka.html"), 0
        )

    def test_city_administration_section_is_followed_by_url(self) -> None:
        """盛岡市はトップに市政のリンクが無い（メニューが JavaScript）。"""
        self.assertGreater(
            kansa.link_priority("お知らせ", "https://www.city.morioka.iwate.jp/shisei/index.html"), 0
        )

    def test_romaji_url_is_followed(self) -> None:
        self.assertGreater(
            kansa.link_priority("結果の公表", "https://example.lg.jp/gaibu-kansa/houkatsu.html"), 0
        )


class SameOrganizationTest(unittest.TestCase):
    def test_audit_office_on_its_own_subdomain(self) -> None:
        """東京都は監査事務局が kansa.metro.tokyo.lg.jp に居る。"""
        self.assertTrue(
            kansa.same_organization("www.metro.tokyo.lg.jp", "kansa.metro.tokyo.lg.jp")
        )

    def test_another_municipality_is_not_the_same(self) -> None:
        self.assertFalse(
            kansa.same_organization("www.city.sapporo.jp", "www.city.sendai.jp")
        )

    def test_www_prefix_does_not_matter(self) -> None:
        self.assertTrue(kansa.same_organization("www.pref.nara.lg.jp", "pref.nara.lg.jp"))


class MandatoryTargetsTest(unittest.TestCase):
    def test_counts_match_the_law(self) -> None:
        """都道府県 47・政令市 20・中核市 62 = 129 団体。"""
        self.assertEqual(len(DESIGNATED_CITY_CODES), 20)
        self.assertEqual(len(CORE_CITY_CODES), 62)
        master = kansa.load_master()
        codes = mandatory_codes(master)
        self.assertEqual(len(codes), 129)

    def test_every_mandatory_code_is_in_the_master(self) -> None:
        master = kansa.load_master()
        missing = [code for code in DESIGNATED_CITY_CODES + CORE_CITY_CODES if code not in master]
        self.assertEqual(missing, [])

    def test_kind_is_reported(self) -> None:
        master = kansa.load_master()
        self.assertEqual(mandatory_kind("13000", master), "都道府県")
        self.assertEqual(mandatory_kind("14100", master), "政令市")
        self.assertEqual(mandatory_kind("01202", master), "中核市")
        self.assertEqual(mandatory_kind("01100", master), "政令市")
        # 義務付けのない町村は空。
        self.assertEqual(mandatory_kind("01303", master), "")


if __name__ == "__main__":
    unittest.main()
