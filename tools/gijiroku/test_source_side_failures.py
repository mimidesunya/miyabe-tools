"""取得元の事情で取れない候補を、こちらの取得エラーと分けて数えることの確認。

2026-10-01 の本番で会議録の 116 自治体が「一部検索可（エラー停止）」だった。
内訳は、取得元のリンク切れ（404）、国立国会図書館 WARP に断られた外部リンク、
前の版の巡回が一覧に入れた会議録でない文書の分だけ「前回より大きく減った」
と判定され続けていたもの、が大半だった。どれも放っておいても治らない。
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from tools.gijiroku import gijiroku_storage
from tools.gijiroku.scrapers import gikai_pdf, kami_city_pdf


def _http_error(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"{status} Error", response=response)


class NormalizeHrefTest(unittest.TestCase):
    def test_stray_quote_after_pdf_is_removed(self) -> None:
        # 睦沢町: href="…0302T01m.pdf"" の閉じ損ね。
        self.assertEqual(
            kami_city_pdf.normalize_href('/wp-content/uploads/0302T01m.pdf"'),
            "/wp-content/uploads/0302T01m.pdf",
        )

    def test_javascript_anywhere_is_not_followed(self) -> None:
        # 榛東村: 先頭以外に javascript: が出る。
        self.assertEqual(kami_city_pdf.normalize_href("javascript:void(0)?idSubTop=4"), "")
        self.assertEqual(kami_city_pdf.normalize_href("./javascript:void(0)"), "")

    def test_mail_and_tel_are_not_followed(self) -> None:
        self.assertEqual(kami_city_pdf.normalize_href("mailto:gikai@example.lg.jp"), "")
        self.assertEqual(kami_city_pdf.normalize_href("tel:0123"), "")

    def test_ordinary_href_is_kept(self) -> None:
        self.assertEqual(kami_city_pdf.normalize_href("  kaigiroku/r7.html \n"), "kaigiroku/r7.html")


class LinkShapesFromExpansionSurveyTest(unittest.TestCase):
    """2026-10-01 の取得元再調査で、既存の収集器が取りこぼした書き方。"""

    def test_year_with_starting_month(self) -> None:
        # 吉富町: 年別一覧へのリンクが「令和8年1月～」。
        for text in ("令和8年1月～", "平成26年4月～", "令和8年1月～12月", "令和8年", "2026年度"):
            self.assertTrue(kami_city_pdf.YEAR_ONLY_ANCHOR_RE.match(text), text)
        for text in ("令和8年1月臨時会", "令和8年第1回定例会"):
            self.assertFalse(kami_city_pdf.YEAR_ONLY_ANCHOR_RE.match(text), text)

    def test_resource_php_is_a_download_endpoint(self) -> None:
        # 小国町（熊本）: /resource.php?e=… に「(PDF 329KB)」と添える。
        url = "https://www.town.kumamoto-oguni.lg.jp/resource.php?e=41c5b2a62a3cce93"
        self.assertTrue(kami_city_pdf.looks_like_attachment_pdf(url, "令和8年　第2回臨時会 (PDF 329KB)"))
        self.assertFalse(kami_city_pdf.looks_like_attachment_pdf(url, "町のあらまし"))

    def test_kaigi_no_kiroku_is_a_minutes_page(self) -> None:
        # 八丈町: 議会の「会議の記録」の下に会議録がある。
        self.assertTrue(
            kami_city_pdf.looks_like_generic_minutes_page(
                "会議の記録", "https://www.town.hachijo.tokyo.jp/chousei/chougikai/katsushin/kaigi-kiroku/"
            )
        )


class ClassifyFetchFailureTest(unittest.TestCase):
    PAGE = "https://www.city.example.lg.jp/gikai/kaigiroku.html"

    def test_not_found_is_source_missing(self) -> None:
        self.assertEqual(
            kami_city_pdf.classify_fetch_failure(_http_error(404), "https://www.city.example.lg.jp/a.pdf", self.PAGE),
            "source_missing",
        )
        self.assertEqual(
            kami_city_pdf.classify_fetch_failure(_http_error(410), "https://www.city.example.lg.jp/a.pdf", self.PAGE),
            "source_missing",
        )

    def test_external_archive_refusal_is_not_our_error(self) -> None:
        warp = "https://warp.ndl.go.jp/info:ndljp/pid/13343723/www.city.example.lg.jp/a.pdf"
        self.assertEqual(
            kami_city_pdf.classify_fetch_failure(_http_error(403), warp, self.PAGE),
            "external_unavailable",
        )
        html_instead = ValueError("PDF ではない応答のため取り込みを中止します: 'text/html' " + warp)
        self.assertEqual(
            kami_city_pdf.classify_fetch_failure(html_instead, warp, self.PAGE),
            "external_unavailable",
        )

    def test_same_site_refusal_stays_an_error(self) -> None:
        # 自治体のサイト自身が断るのは、こちらが直すべき問題かもしれない。
        self.assertEqual(
            kami_city_pdf.classify_fetch_failure(_http_error(403), "https://www.city.example.lg.jp/a.pdf", self.PAGE),
            "error",
        )

    def test_transient_failure_stays_an_error(self) -> None:
        self.assertEqual(
            kami_city_pdf.classify_fetch_failure(requests.ConnectionError("reset"), "https://x.example/a.pdf", self.PAGE),
            "error",
        )


class DisallowedArchiveTest(unittest.TestCase):
    def test_warp_is_not_fetched(self) -> None:
        item = kami_city_pdf.PdfMeetingItem(
            title="平成29年度 決算 提出資料",
            url="https://warp.ndl.go.jp/info:ndljp/pid/13343723/www.city.example.lg.jp/a.pdf",
            year_label="平成29年",
            source_year=2017,
            source_fino=None,
            page_url="https://www.city.example.lg.jp/gikai/kaigiroku.html",
            page_title="会議録",
            meeting_group=None,
        )

        class _NoNetwork:
            def get(self, *_args, **_kwargs):
                raise AssertionError("WARP へ取りに行ってはいけない")

        result = kami_city_pdf.process_pdf_meeting_plan(
            _NoNetwork(),
            {"item": item, "pdf_path": Path(tempfile.gettempdir()) / "never-written.pdf"},
            no_resume=False,
            timeout_ms=1000,
        )
        self.assertEqual(result["status"], "external_unavailable")


class LocalPdfReuseTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.pdf_path = Path(self._tmp.name) / "pdfs" / "不明" / "1_議案.pdf"
        self.pdf_path.parent.mkdir(parents=True)
        self.item = kami_city_pdf.PdfMeetingItem(
            title="議案第1号",
            url="https://www.town.example.lg.jp/a.pdf",
            year_label="不明",
            source_year=None,
            source_fino=None,
            page_url="https://www.town.example.lg.jp/gikai/kaigiroku.html",
            page_title="会議録",
            meeting_group=None,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    class _NoNetwork:
        def get(self, *_args, **_kwargs):
            raise AssertionError("手元の写しがあるのに取得元へ取りに行ってはいけない")

    def test_fresh_local_copy_is_reused(self) -> None:
        self.pdf_path.write_bytes(b"%PDF-1.4 scanned")
        with mock.patch.object(kami_city_pdf, "extract_pdf_text", return_value=""):
            result = kami_city_pdf.process_pdf_meeting_plan(
                self._NoNetwork(),
                {"item": self.item, "pdf_path": self.pdf_path},
                no_resume=False,
                timeout_ms=1000,
            )
        self.assertEqual(result["status"], "empty_pdf_text")
        self.assertFalse(result["downloaded"])

    def test_old_local_copy_is_fetched_again(self) -> None:
        self.pdf_path.write_bytes(b"%PDF-1.4 old")
        old = kami_city_pdf.time.time() - (kami_city_pdf.LOCAL_PDF_REUSE_DAYS + 1) * 86400
        import os

        os.utime(self.pdf_path, (old, old))
        self.assertFalse(kami_city_pdf.local_pdf_is_fresh(self.pdf_path))


class TransientRetryTest(unittest.TestCase):
    class _Response:
        def __init__(self, content: bytes, headers: dict, status: int = 200) -> None:
            self.content = content
            self.headers = headers
            self.status_code = status

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise _http_error(self.status_code)

    class _Session:
        def __init__(self, outcomes) -> None:
            self.outcomes = list(outcomes)
            self.calls = 0

        def get(self, _url, **_kwargs):
            self.calls += 1
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    def test_connection_reset_is_retried(self) -> None:
        pdf = self._Response(b"%PDF-1.7 body", {"Content-Type": "application/pdf"})
        session = self._Session([requests.ConnectionError("RemoteDisconnected"), pdf])
        with mock.patch.object(kami_city_pdf.time, "sleep"):
            body = kami_city_pdf.request_pdf_bytes(session, "https://example.lg.jp/a.pdf", 10_000)
        self.assertEqual(body, b"%PDF-1.7 body")
        self.assertEqual(session.calls, 2)

    def test_not_found_is_not_retried(self) -> None:
        session = self._Session([self._Response(b"", {}, status=404)])
        with mock.patch.object(kami_city_pdf.time, "sleep") as sleep:
            with self.assertRaises(requests.HTTPError):
                kami_city_pdf.request_text(session, "https://example.lg.jp/gone.html", 10_000)
        self.assertEqual(session.calls, 1)
        sleep.assert_not_called()

    def test_gives_up_after_retries(self) -> None:
        session = self._Session([requests.Timeout("t")] * 3)
        with mock.patch.object(kami_city_pdf.time, "sleep"):
            with self.assertRaises(requests.Timeout):
                kami_city_pdf.request_text(session, "https://example.lg.jp/slow.html", 10_000)
        self.assertEqual(session.calls, 1 + len(kami_city_pdf.FETCH_RETRY_WAITS_SECONDS))


class SourceSideSummaryTest(unittest.TestCase):
    def test_dead_links_do_not_block_completion(self) -> None:
        summary = gijiroku_storage.classified_scrape_summary(
            discovered_count=10,
            downloaded_count=8,
            status_counts={"saved_text": 8, "source_missing": 1, "external_unavailable": 1},
        )
        self.assertEqual(summary["failed_count"], 0)
        self.assertEqual(summary["progress_current"], summary["progress_total"])
        self.assertIn("取得元でリンクが切れている候補 1件", summary["warning_lines"])
        self.assertIn("外部の保存先に取得を断られた候補 1件", summary["warning_lines"])
        self.assertNotIn("会議録本体ではない候補を除外 2件", summary["warning_lines"])

    def test_all_dead_links_still_mean_the_entry_is_stale(self) -> None:
        # 1 件も取れずリンク切れだけなら、登録した入口が古い。0/0 の完了にしない。
        summary = gijiroku_storage.classified_scrape_summary(
            discovered_count=5,
            downloaded_count=0,
            status_counts={"source_missing": 5},
        )
        self.assertEqual(summary["failed_count"], 5)
        self.assertTrue(summary["all_failed"])
        self.assertLess(summary["progress_current"], summary["progress_total"])


class AcceptedShrinkTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "meetings_index.json"
        # 前の版の巡回が一覧に入れた 100 件。本文を取れたのはそのうち 20 件だけ。
        old = [{"url": f"https://example.lg.jp/{i}.pdf"} for i in range(100)]
        self.path.write_text(json.dumps(old), encoding="utf-8")
        self.accepted = {f"https://example.lg.jp/{i}.pdf" for i in range(20)}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_dropping_never_accepted_rows_is_not_a_shrink(self) -> None:
        current = [{"url": url} for url in sorted(self.accepted)] + [{"url": "https://example.lg.jp/new.pdf"}]
        self.assertFalse(
            gijiroku_storage.meetings_index_would_shrink(self.path, current, accepted_urls=self.accepted)
        )
        gijiroku_storage.save_meetings_index(self.path, current, accepted_urls=self.accepted)
        self.assertEqual(len(json.loads(self.path.read_text(encoding="utf-8"))), 21)

    def test_losing_accepted_minutes_is_still_a_shrink(self) -> None:
        current = [{"url": url} for url in sorted(self.accepted)[:10]]
        self.assertTrue(
            gijiroku_storage.meetings_index_would_shrink(self.path, current, accepted_urls=self.accepted)
        )
        gijiroku_storage.save_meetings_index(self.path, current, accepted_urls=self.accepted)
        self.assertEqual(len(json.loads(self.path.read_text(encoding="utf-8"))), 100)

    def test_without_accepted_the_count_rule_applies(self) -> None:
        current = [{"url": f"https://example.lg.jp/{i}.pdf"} for i in range(20)]
        self.assertTrue(gijiroku_storage.meetings_index_would_shrink(self.path, current))

    def test_accepted_urls_come_from_state(self) -> None:
        state = {
            "items": {
                "a": {"url": "https://example.lg.jp/a.pdf", "status": "saved_text"},
                "b": {"url": "https://example.lg.jp/b.pdf", "status": "skipped_existing"},
                "c": {"url": "https://example.lg.jp/c.pdf", "status": "skipped_not_minutes"},
                "d": {"url": "https://example.lg.jp/d.pdf", "status": "error"},
            }
        }
        self.assertEqual(
            gijiroku_storage.accepted_item_urls(state),
            {"https://example.lg.jp/a.pdf", "https://example.lg.jp/b.pdf"},
        )


class PreviousAcceptedUrlsTest(unittest.TestCase):
    """batch は実行の前に scrape_state.json を消すので、実行ごとに残る CSV から引く。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.work_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, stamp: str, rows: list[tuple[str, str]]) -> None:
        lines = ["title,year,url,status,output,pdf,error"]
        lines += [f"t,y,{url},{status},,," for url, status in rows]
        (self.work_dir / f"run_result_{stamp}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_without_state_the_runs_are_read(self) -> None:
        self._run("20260901_000000", [("https://a.pdf", "saved_text"), ("https://b.pdf", "saved_text")])
        self._run("20260915_000000", [("https://a.pdf", "skipped_existing")])
        # b は最近の実行に出てこないが、本文は取れていた。見失った側に数える材料になる。
        self.assertEqual(
            gijiroku_storage.previous_accepted_urls(self.work_dir, {"items": {}}),
            {"https://a.pdf", "https://b.pdf"},
        )

    def test_the_latest_settled_status_wins(self) -> None:
        self._run("20260901_000000", [("https://a.pdf", "saved_text"), ("https://c.pdf", "saved_text")])
        # 会議録でないと分かった・取得元から消えた回は、本文を持っていない側。
        self._run("20260910_000000", [("https://a.pdf", "skipped_not_minutes"), ("https://c.pdf", "source_missing")])
        self.assertEqual(gijiroku_storage.previous_accepted_urls(self.work_dir), set())

    def test_a_failed_fetch_does_not_hide_an_earlier_text(self) -> None:
        self._run("20260901_000000", [("https://a.pdf", "saved_text")])
        self._run("20260910_000000", [("https://a.pdf", "error")])
        self.assertEqual(gijiroku_storage.previous_accepted_urls(self.work_dir), {"https://a.pdf"})

    def test_state_still_counts(self) -> None:
        state = {"items": {"x": {"url": "https://s.pdf", "status": "saved_text"}}}
        self.assertEqual(gijiroku_storage.previous_accepted_urls(self.work_dir, state), {"https://s.pdf"})

    def test_a_shrunk_list_that_lost_nothing_is_saved(self) -> None:
        # 前回の一覧には会議録でない文書が混ざっていた（1,080 件）。本文を取れていた
        # 69 件は今回も全部見つかったので、置き換えてよい（松前町の形）。
        index = self.work_dir / "meetings_index.json"
        index.write_text(
            json.dumps([{"url": f"https://old/{i}.pdf"} for i in range(1080)]), encoding="utf-8"
        )
        found = [f"https://new/{i}.pdf" for i in range(69)]
        self._run("20260926_000000", [(url, "skipped_existing") for url in found])
        accepted = gijiroku_storage.previous_accepted_urls(self.work_dir, {"items": {}})
        current = [{"url": url} for url in found]
        self.assertFalse(gijiroku_storage.meetings_index_would_shrink(index, current, accepted_urls=accepted))


class AdaptivePageLimitTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.work_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _coverage(self, **fields) -> None:
        gijiroku_storage.save_source_coverage(self.work_dir, {"mode": "source_discovery_coverage", **fields})

    def test_default_without_record(self) -> None:
        self.assertEqual(gijiroku_storage.adaptive_page_limit(self.work_dir, 120), 120)

    def test_limit_doubles_after_hitting_it(self) -> None:
        # 栗山町: 毎回 120 ページで止まり「取得上限に達した部分データ」のままだった。
        self._coverage(state="partial_limit", limit_reached=True, visited_pages=120, page_limit=120)
        self.assertEqual(gijiroku_storage.adaptive_page_limit(self.work_dir, 120), 240)

    def test_learned_limit_is_kept_after_completion(self) -> None:
        # 広げた上限で歩き切れたら、次回も同じ上限で走る（元に戻すとまた当たる）。
        self._coverage(state="complete", limit_reached=False, visited_pages=180, page_limit=240)
        self.assertEqual(gijiroku_storage.adaptive_page_limit(self.work_dir, 120), 240)

    def test_hard_cap(self) -> None:
        self._coverage(state="partial_limit", limit_reached=True, visited_pages=2400, page_limit=2400)
        self.assertEqual(
            gijiroku_storage.adaptive_page_limit(self.work_dir, 600), gijiroku_storage.PAGE_LIMIT_HARD_CAP
        )

    def test_unlimited_stays_unlimited(self) -> None:
        self._coverage(state="partial_limit", limit_reached=True, visited_pages=50)
        self.assertEqual(gijiroku_storage.adaptive_page_limit(self.work_dir, 0), 0)


class CrawlDeadPagesTest(unittest.TestCase):
    START = "https://www.town.example.lg.jp/gikai/kaigiroku/index.html"

    def _crawl(self, pages: dict[str, object]) -> tuple[list, dict]:
        def fake_request_text(_session, url, _timeout_ms):
            outcome = pages.get(url, _http_error(404))
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        walk: dict = {}
        with mock.patch.object(gikai_pdf, "request_text", side_effect=fake_request_text):
            items = gikai_pdf.crawl_pdf_items(
                object(), self.START, timeout_ms=1000, max_pages=50, max_depth=3, walk=walk
            )
        return items, walk

    def test_dead_child_page_is_not_a_missed_page(self) -> None:
        start_html = (
            '<html><head><title>会議録</title></head><body>'
            '<a href="r7/kaigiroku.html">令和7年 会議録</a>'
            '<a href="r6/kaigiroku.html">令和6年 会議録</a>'
            '</body></html>'
        )
        r7 = '<html><head><title>令和7年 会議録</title></head><body><a href="r7-1.pdf">第1回定例会 会議録</a></body></html>'
        items, walk = self._crawl({
            self.START: start_html,
            "https://www.town.example.lg.jp/gikai/kaigiroku/r7/kaigiroku.html": r7,
        })
        self.assertEqual([item.url for item in items], ["https://www.town.example.lg.jp/gikai/kaigiroku/r7/r7-1.pdf"])
        self.assertEqual(walk["missed_pages"], 0)
        self.assertEqual(walk["dead_pages"], 1)

    def test_dead_entry_page_is_a_missed_page(self) -> None:
        _items, walk = self._crawl({})
        self.assertEqual(walk["missed_pages"], 1)
        self.assertEqual(walk["dead_pages"], 0)

    def test_base_href_is_used(self) -> None:
        start_html = (
            '<html><head><title>会議録</title><base href="https://www.town.example.lg.jp/files/"></head>'
            '<body><a href="r7-1.pdf">第1回定例会 会議録</a></body></html>'
        )
        items, _walk = self._crawl({self.START: start_html})
        self.assertEqual([item.url for item in items], ["https://www.town.example.lg.jp/files/r7-1.pdf"])

    def test_pdf_with_stray_quote_becomes_an_item(self) -> None:
        start_html = (
            '<html><head><title>会議録</title></head>'
            '<body><a href=\'r7-1.pdf"\'>第1回定例会 会議録</a></body></html>'
        )
        items, walk = self._crawl({self.START: start_html})
        self.assertEqual([item.url for item in items], ["https://www.town.example.lg.jp/gikai/kaigiroku/r7-1.pdf"])
        self.assertEqual(walk["missed_pages"], 0)


if __name__ == "__main__":
    unittest.main()
