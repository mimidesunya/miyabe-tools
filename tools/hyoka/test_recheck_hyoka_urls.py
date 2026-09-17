from __future__ import annotations

import sys
import unittest
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(WORKSPACE_ROOT / "tools" / "hyoka"))

import discover_hyoka_urls as discover  # noqa: E402
import recheck_hyoka_urls as recheck  # noqa: E402

BASE = "https://example.test/gyosei/hyoka/"


def page(title: str, body: str = "") -> str:
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>"


class FakeResponse:
    def __init__(self, url: str, text: str) -> None:
        self.url = url
        self.text = text
        self.encoding = "utf-8"
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    """URL ごとに決まった頁を返す。開いた順を残す。"""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.opened: list[str] = []

    def get(self, url: str, headers=None, timeout=None) -> FakeResponse:
        self.opened.append(url)
        if url not in self.pages:
            raise OSError("not found")
        return FakeResponse(url, self.pages[url])


class ChildLinkTest(unittest.TestCase):
    def test_yearly_review_links_come_first(self) -> None:
        body = (
            '<a href="/gyosei/hyoka/about.html">評価結果の見方</a>'
            '<a href="/gyosei/hyoka/r7.html">令和7年度事務事業評価</a>'
            '<a href="/kurashi/gomi.html">ごみの出し方</a>'
        )
        links = discover.child_evaluation_links(BASE, body)
        self.assertEqual([label for _url, label in links][0], "令和7年度事務事業評価")
        self.assertNotIn("ごみの出し方", [label for _url, label in links])

    def test_other_schemes_are_not_followed(self) -> None:
        body = (
            '<a href="/a.html">令和6年度電源立地地域対策交付金事業評価</a>'
            '<a href="/b.html">総合戦略評価結果</a>'
            '<a href="/c.html">指定管理施設評価結果</a>'
            '<a href="/d.html">教育委員会の点検・評価結果</a>'
            '<a href="/e.html">歴史的風致維持向上計画 進行管理・評価シート</a>'
        )
        self.assertEqual(discover.child_evaluation_links(BASE, body), [])

    def test_attachments_and_other_sites_are_not_pages_to_open(self) -> None:
        body = (
            '<a href="/gyosei/hyoka/r7.pdf">令和7年度事務事業評価表</a>'
            '<a href="https://other.test/hyoka.html">事務事業評価</a>'
        )
        self.assertEqual(discover.child_evaluation_links(BASE, body), [])

    def test_the_number_of_children_is_capped(self) -> None:
        body = "".join(f'<a href="/y{i}.html">令和{i}年度評価結果</a>' for i in range(1, 10))
        self.assertEqual(len(discover.child_evaluation_links(BASE, body, limit=4)), 4)


class EvaluationTableTest(unittest.TestCase):
    def test_a_table_of_reviewed_programs_is_detected(self) -> None:
        table = (
            "<table><tr><th>事業名</th><th>担当課</th><th>決算額</th><th>評価</th>"
            "<th>今後の方向</th></tr><tr><td>道路維持</td><td>建設課</td>"
            "<td>1,200</td><td>B</td><td>継続</td></tr></table>"
        )
        self.assertTrue(discover.has_evaluation_table(page("令和7年度事務事業評価", table)))

    def test_an_unrelated_table_is_ignored(self) -> None:
        table = "<table><tr><th>日時</th><th>場所</th></tr><tr><td>7月1日</td><td>市役所</td></tr></table>"
        self.assertFalse(discover.has_evaluation_table(page("お知らせ", table)))

    def test_a_managed_facility_table_is_ignored(self) -> None:
        table = (
            "<table><tr><th>指定管理施設</th><th>事業名</th><th>決算額</th><th>評価</th></tr></table>"
        )
        self.assertFalse(discover.has_evaluation_table(page("評価結果", table)))


class OffTopicChildTest(unittest.TestCase):
    def test_plan_progress_child_page_is_skipped(self) -> None:
        # リンク文字は「令和5年度評価結果」でも、開いた先の題名が計画の進行管理なら採らない。
        entry = '<a href="/gyosei/hyoka/r5.html">令和5年度評価結果</a>'
        child = page(
            "歴史的風致維持向上計画の進行管理",
            '<a href="/a.pdf">令和5年度 評価シート</a><a href="/b.pdf">令和4年度 評価シート</a>',
        )
        session = FakeSession({"https://example.test/gyosei/hyoka/r5.html": child})
        self.assertIsNone(recheck.descend(session, BASE, entry, 10, delay=0, sleep=lambda _s: None))

    def test_a_title_naming_program_review_is_kept(self) -> None:
        self.assertFalse(discover.child_page_is_off_topic("事務事業評価（総合計画の進行管理）"))


class DescendTest(unittest.TestCase):
    def _run(self, pages: dict[str, str], entry_html: str) -> tuple[dict | None, FakeSession]:
        session = FakeSession(pages)
        found = recheck.descend(session, BASE, entry_html, 10, delay=0, sleep=lambda _s: None)
        return found, session

    def test_review_sheets_one_level_down_are_found(self) -> None:
        # 南知多町の形: 入口は「事業評価」だけで、評価書は年度の子ページにある。
        entry = '<a href="/gyosei/hyoka/r8.html">令和8年度事業評価書（予算時）</a>'
        child = page(
            "令和8年度事業評価書（予算時）",
            '<a href="/files/r8_1.pdf">事務事業評価表（総務課）</a>'
            '<a href="/files/r8_2.pdf">事務事業評価表（建設課）</a>',
        )
        found, _session = self._run({"https://example.test/gyosei/hyoka/r8.html": child}, entry)
        self.assertIsNotNone(found)
        # 前回と同じ基準（確実、または有力で実ファイルあり）で取得対象になる。
        self.assertTrue(recheck.settled(found["confidence"], int(found["attachments"])))
        self.assertEqual(found["attachments"], "2")
        self.assertIn("子ページ", found["evidence"])

    def test_an_html_table_child_counts_as_a_published_sheet(self) -> None:
        entry = '<a href="/gyosei/hyoka/r7.html">令和7年度 評価結果</a>'
        child = page(
            "行政評価 令和7年度評価結果",
            "<p>事務事業評価の結果です。</p><table><tr><th>事業名</th><th>事業費</th>"
            "<th>成果</th><th>評価</th></tr></table>",
        )
        found, _session = self._run({"https://example.test/gyosei/hyoka/r7.html": child}, entry)
        self.assertIsNotNone(found)
        self.assertEqual(found["attachments"], "1")

    def test_an_other_scheme_child_is_not_taken(self) -> None:
        # 入口のリンク文字は「評価結果」でも、開いた先が電源立地の報告書なら採らない。
        entry = '<a href="/gyosei/hyoka/r6.html">令和6年度評価結果</a>'
        child = page(
            "電源立地地域対策交付金事業評価報告書",
            '<a href="/a.pdf">令和6年度 事業評価報告書</a><a href="/b.pdf">令和5年度 事業評価報告書</a>',
        )
        found, _session = self._run({"https://example.test/gyosei/hyoka/r6.html": child}, entry)
        self.assertIsNone(found)

    def test_a_child_without_sheets_is_not_enough(self) -> None:
        entry = '<a href="/gyosei/hyoka/about.html">行政評価について</a>'
        child = page("行政評価について", "<p>事務事業評価の仕組みを説明します。</p>")
        found, _session = self._run({"https://example.test/gyosei/hyoka/about.html": child}, entry)
        self.assertIsNone(found)

    def test_it_stops_at_the_first_settled_child(self) -> None:
        entry = (
            '<a href="/gyosei/hyoka/r7.html">令和7年度事務事業評価</a>'
            '<a href="/gyosei/hyoka/r6.html">令和6年度事務事業評価</a>'
        )
        child = page("令和7年度事務事業評価", '<a href="/x.pdf">事務事業評価表</a>')
        found, session = self._run(
            {
                "https://example.test/gyosei/hyoka/r7.html": child,
                "https://example.test/gyosei/hyoka/r6.html": child,
            },
            entry,
        )
        self.assertIsNotNone(found)
        self.assertEqual(len(session.opened), 1)


class RecheckOneTest(unittest.TestCase):
    def test_entry_without_sheets_is_settled_by_descending(self) -> None:
        entry_url = "https://example.test/gyosei/hyoka/"
        entry = page(
            "行政評価",
            "<p>事務事業評価を実施しています。</p>"
            '<a href="/gyosei/hyoka/r8.html">令和8年度事務事業評価</a>',
        )
        child = page("令和8年度事務事業評価", '<a href="/files/r8.pdf">事務事業評価表</a>')
        session = FakeSession({entry_url: entry, "https://example.test/gyosei/hyoka/r8.html": child})
        row = {"jis_code": "99999", "url": entry_url}
        without = recheck.recheck_one(session, row, 10)
        self.assertEqual(without["confidence"], "medium")
        self.assertEqual(without["attachments"], "0")
        found = recheck.recheck_one(session, row, 10, descend_children=True, child_delay=0)
        self.assertTrue(recheck.settled(found["confidence"], int(found["attachments"])))
        self.assertIn(entry_url, found["note"])

    def test_a_settled_entry_does_not_open_children(self) -> None:
        entry_url = "https://example.test/gyosei/hyoka/"
        entry = page(
            "事務事業評価",
            '<a href="/files/r8.pdf">事務事業評価表</a>'
            '<a href="/gyosei/hyoka/r8.html">令和8年度事務事業評価</a>',
        )
        session = FakeSession({entry_url: entry})
        recheck.recheck_one(session, {"jis_code": "99999", "url": entry_url}, 10,
                            descend_children=True, child_delay=0)
        self.assertEqual(session.opened, [entry_url])


if __name__ == "__main__":
    unittest.main()
