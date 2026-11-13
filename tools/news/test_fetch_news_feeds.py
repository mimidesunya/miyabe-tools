"""新着情報の取得と保存。取得元には当てず、応答を作って渡す。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.news import fetch_news_feeds as fetch  # noqa: E402
from tools.news import discover_news_feeds as discover  # noqa: E402

FEED_URL = "https://www.city.example.jp/rss.xml"


def rss(*items: tuple[str, str, str]) -> bytes:
    body = "".join(
        f"<item><title>{title}</title><link>{link}</link><pubDate>{date}</pubDate></item>"
        for title, link, date in items
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>例市</title>{body}</channel></rss>'.encode("utf-8")


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"", headers: dict | None = None, url: str = FEED_URL) -> None:
        self.status_code = status
        self.body = body
        self.headers = headers or {}
        self.url = url

    def iter_content(self, chunk_size: int = 0):
        yield self.body

    def close(self) -> None:
        pass


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url, headers=None, timeout=None, stream=False):
        self.calls.append({"url": url, "headers": dict(headers or {})})
        return self.responses.pop(0)


class FetchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.data_root = root / "data"
        self.work_root = root / "work"
        self.registry = root / "news_feed_urls.tsv"
        self.registry.write_text(
            "jis_code\turl\tfound_by\tfeed_format\tcrawl_status\texclusion_reason\texclusion_detail\n"
            f"99999\t{FEED_URL}\thead\trss2\tenabled\t\t\n"
            "99999\thttps://www.city.example.jp/old.xml\thead\trss2\texcluded\tstale\t\n",
            encoding="utf-8",
        )
        self.master = {"99999": {"name_romaji": "example-shi", "full_name": "例市"}}
        self.clock_value = datetime(2026, 11, 13, 12, 0, tzinfo=fetch.JST)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def fetcher(self, session: FakeSession, **kwargs) -> fetch.NewsFetcher:
        return fetch.NewsFetcher(
            data_root=self.data_root,
            work_root=self.work_root,
            session=session,
            pacer=fetch.HostPacer(interval=0),
            clock=lambda: self.clock_value,
            **kwargs,
        )

    def targets(self) -> list[fetch.FeedTarget]:
        grouped = fetch.load_feed_targets(self.registry, master=self.master)
        return grouped["99999"]

    def items(self) -> list[dict]:
        path = self.data_root / "99999-example-shi" / "items" / "2026.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_only_enabled_rows_are_targets(self) -> None:
        targets = self.targets()
        self.assertEqual([target.url for target in targets], [FEED_URL])
        self.assertEqual(targets[0].slug, "99999-example-shi")

    def test_new_items_are_stored_once_and_revisions_get_a_new_line(self) -> None:
        first = rss(("お知らせA", "https://www.city.example.jp/a.html", "Thu, 12 Nov 2026 10:00:00 +0900"),
                    ("お知らせB", "https://www.city.example.jp/b.html", "Wed, 11 Nov 2026 10:00:00 +0900"))
        session = FakeSession([FakeResponse(200, first, {"ETag": '"v1"'})])
        fetcher = self.fetcher(session)
        results = fetcher.fetch_municipality(self.targets())
        fetcher.save_state()
        self.assertEqual((results[0].status, results[0].new), ("fetched", 2))
        self.assertEqual([item["title"] for item in self.items()], ["お知らせA", "お知らせB"])
        self.assertEqual(self.items()[0]["jis_code"], "99999")
        self.assertEqual(self.items()[0]["date"], "2026-11-12")

        # 同じページを新しい日付で出し直した（人口のページなど）。
        second = rss(("お知らせA", "https://www.city.example.jp/a.html", "Fri, 13 Nov 2026 09:00:00 +0900"),
                     ("お知らせB", "https://www.city.example.jp/b.html", "Wed, 11 Nov 2026 10:00:00 +0900"))
        session = FakeSession([FakeResponse(200, second, {"ETag": '"v2"'})])
        fetcher = self.fetcher(session)
        results = fetcher.fetch_municipality(self.targets())
        self.assertEqual(session.calls[0]["headers"].get("If-None-Match"), '"v1"')
        self.assertEqual((results[0].new, results[0].revised), (0, 1))
        stored = self.items()
        self.assertEqual(len(stored), 3)
        self.assertEqual((stored[2]["id"], stored[2]["revision"]), (stored[0]["id"], 1))

    def test_two_items_sharing_a_url_do_not_add_lines_every_run(self) -> None:
        # 同じ URL に別のお知らせを付けるフィード（苫小牧市で見た）。
        body = rss(("講座A", "https://www.city.example.jp/x.html", "2026-11-09"),
                   ("講座B", "https://www.city.example.jp/x.html", "2026-11-11"))
        for expected_new, expected_revised in ((1, 1), (0, 0), (0, 0)):
            session = FakeSession([FakeResponse(200, body)])
            fetcher = self.fetcher(session)
            result = fetcher.fetch_municipality(self.targets())[0]
            self.assertEqual((result.new, result.revised), (expected_new, expected_revised))
        self.assertEqual([item["title"] for item in self.items()], ["講座A", "講座B"])

    def test_seen_can_be_rebuilt_from_the_items(self) -> None:
        body = rss(("講座A", "https://www.city.example.jp/x.html", "2026-11-09"),
                   ("講座B", "https://www.city.example.jp/x.html", "2026-11-11"))
        self.fetcher(FakeSession([FakeResponse(200, body)])).fetch_municipality(self.targets())
        seen_path = self.data_root / "99999-example-shi" / "seen.json"
        before = json.loads(seen_path.read_text(encoding="utf-8"))
        seen_path.write_text("{broken", encoding="utf-8")
        store = fetch.MunicipalityStore(self.data_root, "99999", "99999-example-shi")
        self.assertEqual(store.seen, before)

    def test_a_failed_save_makes_the_next_run_read_the_feed_again(self) -> None:
        body = rss(("お知らせA", "https://www.city.example.jp/a.html", "2026-11-12"))
        session = FakeSession([FakeResponse(200, body, {"ETag": '"v1"'})])
        fetcher = self.fetcher(session)
        original = fetch.MunicipalityStore.save

        def broken(store, seen_at):
            raise OSError("disk full")

        fetch.MunicipalityStore.save = broken
        try:
            result = fetcher.fetch_municipality(self.targets())[0]
        finally:
            fetch.MunicipalityStore.save = original
        self.assertEqual(result.status, "failed")
        self.assertNotIn("etag", fetcher.state[FEED_URL])

    def test_not_modified_keeps_items_and_clears_failures(self) -> None:
        session = FakeSession([FakeResponse(304)])
        fetcher = self.fetcher(session)
        fetcher.state[FEED_URL] = {"etag": '"v1"', "failures": 3, "checked_at": "2026-11-13 09:00:00"}
        result = fetcher.fetch_municipality(self.targets())[0]
        self.assertEqual(result.status, "not_modified")
        self.assertEqual(fetcher.state[FEED_URL]["failures"], 0)
        self.assertFalse((self.data_root / "99999-example-shi" / "items").exists())

    def test_html_instead_of_a_feed_counts_as_a_failure(self) -> None:
        session = FakeSession([FakeResponse(200, b"<!DOCTYPE html><html>moved</html>")])
        fetcher = self.fetcher(session)
        result = fetcher.fetch_municipality(self.targets())[0]
        self.assertEqual(result.status, "failed")
        self.assertEqual(fetcher.state[FEED_URL]["failures"], 1)
        self.assertIn("FeedError", fetcher.state[FEED_URL]["last_error"])

    def test_failing_feeds_back_off(self) -> None:
        now = self.clock_value
        recent = (now - timedelta(hours=3)).strftime(fetch.TIME_FORMAT)
        old = (now - timedelta(days=2)).strftime(fetch.TIME_FORMAT)
        self.assertTrue(fetch.backoff_due({"failures": 7, "checked_at": recent}, now))
        self.assertFalse(fetch.backoff_due({"failures": 8, "checked_at": recent}, now))
        self.assertTrue(fetch.backoff_due({"failures": 8, "checked_at": old}, now))
        self.assertFalse(fetch.backoff_due({"failures": 40, "checked_at": old}, now))
        session = FakeSession([])
        fetcher = self.fetcher(session)
        fetcher.state[FEED_URL] = {"failures": 8, "checked_at": recent}
        self.assertEqual(fetcher.fetch_municipality(self.targets())[0].status, "skipped")
        self.assertEqual(session.calls, [])

    def test_run_writes_a_summary(self) -> None:
        body = rss(("お知らせA", "https://www.city.example.jp/a.html", "2026-11-12"))
        session = FakeSession([FakeResponse(200, body)])
        summary = fetch.run(registry=self.registry, data_root=self.data_root, work_root=self.work_root,
                            session=session, quiet=True, workers=1)
        self.assertEqual((summary["feeds"], summary["fetched"], summary["new_items"]), (1, 1, 1))
        self.assertTrue((self.work_root / "last_run.json").exists())
        # トップページの「各地のお知らせ」の一覧も書く。
        self.assertIn("latest_candidates", summary)
        self.assertTrue((self.work_root / "latest" / "all.json").exists())
        self.assertEqual(len((self.work_root / "runs.jsonl").read_text(encoding="utf-8").splitlines()), 1)


class RegistryTest(unittest.TestCase):
    def test_rows_from_discovery_keep_operator_decisions(self) -> None:
        records = [{
            "jis_code": "99999",
            "feeds": [
                {"url": "https://a.example.jp/new.xml", "source": "head", "ok": True, "items": 20, "format": "rss1", "newest": "2026-11-12"},
                {"url": "https://a.example.jp/old.xml", "source": "anchor", "ok": True, "items": 5, "format": "rss2", "newest": "2024-01-01"},
                {"url": "https://a.example.jp/empty.xml", "source": "anchor", "ok": True, "items": 0},
                {"url": "https://a.example.jp/ad.xml", "source": "anchor", "ok": True, "items": 3, "format": "rss2", "newest": "2026-11-01"},
                {"url": "https://a.example.jp/page.html", "source": "anchor", "ok": False, "why": "html"},
            ],
        }]
        previous = [
            {"jis_code": "99999", "url": "https://a.example.jp/ad.xml", "crawl_status": "excluded",
             "exclusion_reason": "not_news", "exclusion_detail": "広告の配信"},
            {"jis_code": "99999", "url": "https://a.example.jp/gone.xml", "crawl_status": "enabled"},
        ]
        rows = discover.registry_rows(records, as_of=datetime(2026, 11, 13).date(), previous=previous)
        by_url = {row["url"].rsplit("/", 1)[-1]: row for row in rows}
        self.assertEqual(sorted(by_url), ["ad.xml", "gone.xml", "new.xml", "old.xml"])
        self.assertEqual(by_url["new.xml"]["crawl_status"], "enabled")
        self.assertEqual((by_url["old.xml"]["crawl_status"], by_url["old.xml"]["exclusion_reason"]), ("excluded", "stale"))
        self.assertEqual(by_url["ad.xml"]["exclusion_reason"], "not_news")
        self.assertEqual(by_url["gone.xml"]["crawl_status"], "enabled")


if __name__ == "__main__":
    unittest.main()
