from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import requests

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(WORKSPACE_ROOT / "tools" / "reiki"))
sys.path.append(str(WORKSPACE_ROOT / "tools" / "reiki" / "scrapers"))

import reiki_targets  # noqa: E402
from scrapers import d1_law  # noqa: E402


class FakeResponse:
    status_code = 200
    content = b"ok"
    headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    """呼ばれた回数を数え、指定の回数だけ接続を切る取得元。"""

    def __init__(self, failures: int, error: Exception | None = None) -> None:
        self.failures = failures
        self.calls = 0
        self.error = error or requests.exceptions.ConnectionError("切られた")

    def get(self, url, headers=None, timeout=None):  # noqa: ANN001
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return FakeResponse()


class RetryTest(unittest.TestCase):
    def test_a_dropped_connection_is_retried(self) -> None:
        session = FakeSession(failures=2)
        waits: list[float] = []
        response = d1_law.fetch_with_retry(session, "https://example.test/a.html", {}, "example.test", sleep=waits.append)
        self.assertIsInstance(response, FakeResponse)
        self.assertEqual(session.calls, 3)
        # 待ち時間は倍にしていく。壁を叩き続けない。
        self.assertEqual(waits, [2.0, 4.0])

    def test_it_gives_up_after_the_limit(self) -> None:
        session = FakeSession(failures=99)
        with self.assertRaises(requests.exceptions.ConnectionError):
            d1_law.fetch_with_retry(session, "https://example.test/a.html", {}, "example.test", sleep=lambda _s: None)
        self.assertEqual(session.calls, d1_law.CONNECTION_RETRY_ATTEMPTS)

    def test_an_http_error_is_not_retried(self) -> None:
        # 404 は相手が答えている。作り直しても同じ答えしか返らない。
        session = FakeSession(failures=1, error=requests.exceptions.HTTPError("404"))
        with self.assertRaises(requests.exceptions.HTTPError):
            d1_law.fetch_with_retry(session, "https://example.test/a.html", {}, "example.test", sleep=lambda _s: None)
        self.assertEqual(session.calls, 1)


class HostBlockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.original = reiki_targets.WORK_ROOT
        reiki_targets.WORK_ROOT = Path(self.directory.name)
        self.addCleanup(lambda: setattr(reiki_targets, "WORK_ROOT", self.original))
        d1_law._CONNECTION_FAILURES.clear()
        d1_law.SKIPPED_BY_HOST_BLOCK.clear()

    def test_a_host_is_avoided_after_repeated_drops(self) -> None:
        host = "en3-jg.example.test"
        for _ in range(d1_law.HOST_BLOCK_THRESHOLD - 1):
            self.assertFalse(d1_law.note_connection_failure(host))
        self.assertFalse(d1_law.host_is_blocked(host))
        # しきい値に届いた時点で休止を記録する。
        self.assertTrue(d1_law.note_connection_failure(host))
        self.assertTrue(d1_law.host_is_blocked(host))

    def test_the_pause_expires(self) -> None:
        host = "en3-jg.example.test"
        d1_law.remember_host_block(host, seconds=60, now=1000.0)
        self.assertTrue(d1_law.host_is_blocked(host, now=1030.0))
        self.assertFalse(d1_law.host_is_blocked(host, now=1100.0))

    def test_a_success_forgets_the_count(self) -> None:
        host = "en3-jg.example.test"
        d1_law.note_connection_failure(host)
        d1_law.note_connection_success(host)
        self.assertEqual(d1_law._CONNECTION_FAILURES.get(host), None)

    def test_a_blocked_host_is_not_fetched(self) -> None:
        host = "en3-jg.example.test"
        d1_law.remember_host_block(host, seconds=3600)
        session = FakeSession(failures=0)
        destination = Path(self.directory.name) / "out" / "a.html"
        downloaded, _path, digest, metadata = d1_law.download_file(
            f"https://{host}/a.html", destination, session=session
        )
        self.assertFalse(downloaded)
        self.assertEqual(digest, "")
        self.assertTrue(metadata.get("host_blocked"))
        # 取りに行っていないので、取りこぼしの列にも入れない。
        self.assertEqual(session.calls, 0)
        self.assertEqual(d1_law.DOWNLOAD_FAILURES, [])
        self.assertEqual(len(d1_law.SKIPPED_BY_HOST_BLOCK), 1)


if __name__ == "__main__":
    unittest.main()
