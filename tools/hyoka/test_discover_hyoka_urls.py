from __future__ import annotations

import sys
import unittest
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(WORKSPACE_ROOT / "tools" / "hyoka"))

import discover_hyoka_urls as discover  # noqa: E402


def page(title: str, body: str = "") -> str:
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>"


class NegativeTest(unittest.TestCase):
    """事務事業評価ではないのに拾っていたもの。2026-09-17 に本番の登録簿から
    抜き取って確かめた実例をそのまま並べる。"""

    def test_web_accessibility_evaluation_is_not_a_program_review(self) -> None:
        # 白糠町: ウェブアクセシビリティ方針の取組確認・評価結果
        html = page("ウェブアクセシビリティ方針取組確認・評価結果", "団体全体としての取組確認・評価表")
        self.assertEqual(discover.score_page("https://example.test/a", html, "")[0], "none")

    def test_subsidy_project_report_is_not_a_program_review(self) -> None:
        # 礼文町・勝山市: 電源立地地域対策交付金を活用した事業の評価報告書
        html = page("電源立地地域対策交付金事業評価報告書について", "平成29年度 事業評価報告書")
        self.assertEqual(discover.score_page("https://example.test/a", html, "")[0], "none")

    def test_single_policy_project_evaluation_is_not_a_program_review(self) -> None:
        # 余市町: アイヌ施策推進事業評価
        html = page("余市町アイヌ施策推進事業評価", "令和6年度末評価")
        self.assertEqual(discover.score_page("https://example.test/a", html, "")[0], "none")

    def test_public_comment_page_is_not_a_program_review(self) -> None:
        # 糸満市: 行政経営プラン(案)のパブリックコメント
        html = page("「行政経営プラン(案)」のパブリックコメントについて", "R6取組評価シート")
        self.assertEqual(discover.score_page("https://example.test/a", html, "")[0], "none")

    def test_regional_strategy_external_review_is_not_a_program_review(self) -> None:
        # 普代村: まち・ひと・しごと創生総合戦略の外部評価
        html = page("第６次総合発展計画", "まち・ひと・しごと創生総合戦略事業評価シート(外部評価)")
        self.assertEqual(discover.score_page("https://example.test/a", html, "")[0], "none")

    def test_education_board_review_is_kept_apart(self) -> None:
        # 大石田町: 教育委員会の点検・評価は地方教育行政法 26 条の別制度。
        html = page("教育委員会事務事業の点検・評価について", "事務事業点検・評価報告書")
        confidence, _evidence = discover.score_page("https://example.test/a", html, "")
        self.assertEqual(confidence, "education")


class PositiveTest(unittest.TestCase):
    def test_strong_word_in_title_is_certain(self) -> None:
        html = page("事務事業評価の結果について", "評価シートを公開しています")
        self.assertEqual(discover.score_page("https://example.test/a", html, "")[0], "high")

    def test_yearly_evaluation_documents_are_certain(self) -> None:
        # 南知多町: 年度ごとの評価書が並ぶ。入口の言葉は「事業評価」だけ。
        body = (
            '<a href="/1005664.html">令和8年度事業評価書（予算時）</a>'
            '<a href="/1005155.html">令和7年度事業評価書（予算時）</a>'
            '<a href="/1004613.html">令和6年度事業評価書（決算時）</a>'
        )
        confidence, evidence = discover.score_page("https://example.test/a", page("事業評価", body), "")
        self.assertEqual(confidence, "high")
        self.assertIn("年度ごとの評価文書", evidence)

    def test_one_year_document_alone_is_not_enough(self) -> None:
        # 1 件だけの年度つき文書は、単発の報告であることが多い。
        body = '<a href="/a.pdf">令和6年度評価報告書</a>'
        self.assertNotEqual(
            discover.score_page("https://example.test/a", page("お知らせ", body), "")[0], "high"
        )

    def test_year_links_skip_excluded_documents(self) -> None:
        body = (
            '<a href="/a.pdf">平成29年度 電源立地地域対策交付金事業評価報告書</a>'
            '<a href="/b.pdf">令和3年度 アイヌ施策推進事業評価結果</a>'
        )
        self.assertEqual(discover.year_evaluation_links("https://example.test/a", body), [])

    def test_hub_with_sheet_word_stays_medium(self) -> None:
        html = page("行政評価", "事務事業の評価シートを掲載しています")
        self.assertIn(discover.score_page("https://example.test/a", html, "")[0], {"high", "medium"})



class EntryPortalTest(unittest.TestCase):
    """入口選択だけのホームページ（高岡市）から先へ進めるか。"""

    def test_resident_entry_is_followed_from_the_homepage(self) -> None:
        self.assertGreater(discover.link_priority("市民の方は こちらから", "https://example.test/citizen/", depth=0), 0)

    def test_tourism_entry_is_not_followed(self) -> None:
        self.assertEqual(discover.link_priority("観光の方は こちらから", "https://example.test/kanko/", depth=0), 0)

    def test_resident_entry_is_not_followed_from_deeper_pages(self) -> None:
        # 下の階層まで住民向けの入口を辿ると、サイト全体を歩くことになる。
        self.assertEqual(discover.link_priority("くらし", "https://example.test/kurashi/", depth=2), 0)


if __name__ == "__main__":
    unittest.main()


class FacilityEvaluationTest(unittest.TestCase):
    def test_designated_manager_facility_review_is_not_a_program_review(self) -> None:
        # いわき市: 指定管理施設経営状況評価結果。施設の評価で、事務事業評価ではない。
        html = page("指定管理施設経営状況評価結果（令和５年度）", "指定管理施設経営状況評価結果一覧")
        self.assertEqual(discover.score_page("https://example.test/a", html, "")[0], "none")


class AttachmentSchemeTest(unittest.TestCase):
    """置いてある評価文書そのものを見て、別制度かどうかを分ける。"""

    def test_only_strategy_sheets_are_rejected(self) -> None:
        # 二宮町: 政策評価委員会の頁だが、添付は総合戦略評価シートだけ。
        body = (
            '<a href="/a.pdf">資料1 総合戦略評価シート（平成30年度実績）</a>'
            '<a href="/b.pdf">参考資料7 総合戦略評価シート（29年度実績）</a>'
        )
        self.assertEqual(discover.score_page("https://example.test/a", page("政策評価委員会", body), "")[0], "none")

    def test_a_single_strategy_sheet_among_others_is_kept(self) -> None:
        # 岩倉市: 10 件のうち総合戦略は 1 件で、残りは行政評価結果報告書。
        body = (
            '<a href="/a.pdf">まち・ひと・しごと創生総合戦略評価結果報告書</a>'
            '<a href="/b.pdf">行政評価委員会行政評価結果報告書</a>'
            '<a href="/c.pdf">基本目標評価シート</a>'
        )
        self.assertNotEqual(
            discover.score_page("https://example.test/a", page("行政評価委員会について", body), "")[0], "none"
        )

    def test_a_page_without_attachments_is_unaffected(self) -> None:
        html = page("行政評価", "事務事業評価を実施しています")
        self.assertNotEqual(discover.score_page("https://example.test/a", html, "")[0], "none")


class PostEvaluationWordTest(unittest.TestCase):
    """「事後評価」は行政評価でも普通に使う。公共事業と一緒のときだけ落とす。"""

    def test_program_review_post_evaluation_sheet_is_kept(self) -> None:
        # 三条市・大月市・由布市: 行政評価の事後評価シート。
        body = (
            '<a href="/a.pdf">令和7年度行政評価事後評価シート</a>'
            '<a href="/b.pdf">令和6年度行政評価事後評価シート</a>'
        )
        self.assertEqual(discover.score_page("https://example.test/a", page("行政評価制度", body), "")[0], "high")

    def test_public_works_post_evaluation_is_rejected(self) -> None:
        html = page("公共事業の事後評価について", "公共事業再評価委員会")
        self.assertEqual(discover.score_page("https://example.test/a", html, "")[0], "none")


class PlanProgressTest(unittest.TestCase):
    def test_plan_progress_sheets_are_not_a_program_review(self) -> None:
        # 国見町: 歴史的風致維持向上計画の進行管理・評価シート。題名が
        # 評価の入口を名乗っていないので、年度ごとに並んでいても採らない。
        body = (
            '<a href="/a.pdf">令和5年度 進行管理・評価シート</a>'
            '<a href="/b.pdf">令和4年度 進行管理・評価シート</a>'
        )
        html = page("歴史的風致維持向上計画について", body)
        self.assertNotEqual(discover.score_page("https://example.test/a", html, "")[0], "high")

    def test_review_page_with_year_documents_is_still_certain(self) -> None:
        body = (
            '<a href="/a.pdf">令和7年度行政評価結果</a>'
            '<a href="/b.pdf">令和6年度行政評価結果</a>'
        )
        self.assertEqual(discover.score_page("https://example.test/a", page("行政評価", body), "")[0], "high")
