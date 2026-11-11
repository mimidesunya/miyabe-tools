"""取得元の「取りすぎ」の制限（HTTP 429）への備え。

2026-09〜11 の legal-square は、本文ビューアが「429 Too Many Requests」を返し、
本文が空のまま 1.5 秒あけて 1 度だけ開き直して失敗にしていた。20 自治体で
146 本の更新が取れず、7 自治体は「直近の取得に失敗」になった
（水戸市・狭山市・浦安市・石川県・上牧町ほか）。

前半は道具（host_rate_limit）、後半は取得元を真似た偽の画面で run() を通す
（test_legal_square_session の偽物に 429 を足したもの）。最後にバッチと
優先度計算が、途中で止めた走査を失敗にせず、取得元が求めた時刻まで待つことを見る。
"""

import datetime
import email.utils
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / "reiki" / "scrapers"), str(ROOT / "tools" / "reiki"), str(ROOT)]

import host_rate_limit  # noqa: E402
import legal_square  # noqa: E402
import scrape_all_reiki  # noqa: E402
from test_legal_square_session import (  # noqa: E402
    TOO_MANY_TEXT,
    FakeSource,
    fake_work_root,
    run_fake,
)
from tools.tasks import priority  # noqa: E402

LEGAL_SQUARE_URL = "https://krr029.legal-square.com/HAS-Shohin/page/SJSrbLogin.jsf"


class RetryAfterTest(unittest.TestCase):
    def test_seconds(self) -> None:
        self.assertEqual(host_rate_limit.parse_retry_after("120"), 120.0)
        self.assertEqual(host_rate_limit.parse_retry_after(" 1.5 "), 1.5)

    def test_http_date(self) -> None:
        now = 1_762_840_000.0
        later = email.utils.formatdate(now + 90, usegmt=True)
        self.assertAlmostEqual(host_rate_limit.parse_retry_after(later, now=now), 90.0, delta=1)
        earlier = email.utils.formatdate(now - 90, usegmt=True)
        self.assertEqual(host_rate_limit.parse_retry_after(earlier, now=now), 0.0)

    def test_missing_or_unreadable(self) -> None:
        self.assertIsNone(host_rate_limit.parse_retry_after(None))
        self.assertIsNone(host_rate_limit.parse_retry_after(""))
        self.assertIsNone(host_rate_limit.parse_retry_after("soon"))


class RateLimitedTextTest(unittest.TestCase):
    def test_the_page_seen_in_production(self) -> None:
        self.assertTrue(host_rate_limit.is_rate_limited_text(TOO_MANY_TEXT))
        self.assertTrue(host_rate_limit.is_rate_limited_text("too  many\nrequests"))

    def test_an_ordinance_is_not_a_rate_limit(self) -> None:
        self.assertFalse(host_rate_limit.is_rate_limited_text("水戸市消防吏員服制規則 第1条"))
        self.assertFalse(host_rate_limit.is_rate_limited_text(""))


class RateLimitHostTest(unittest.TestCase):
    def test_legal_square_tenants_share_one_backend(self) -> None:
        # 429 は取得元全体の制限。自治体ごとのホスト名で覚えると、次の自治体が
        # また全速で始める。バッチの同時実行の数え方と同じ単位にする。
        self.assertEqual(host_rate_limit.rate_limit_host(LEGAL_SQUARE_URL), "legal-square.com")
        self.assertEqual(
            host_rate_limit.rate_limit_host("https://kri101.legal-square.com/HAS-Shohin/page/SJSrbLogin.jsf"),
            "legal-square.com",
        )

    def test_other_hosts(self) -> None:
        self.assertEqual(host_rate_limit.rate_limit_host("https://www1.g-reiki.net/x/reiki_menu.html"), "www1.g-reiki.net")
        self.assertEqual(host_rate_limit.rate_limit_host(""), "")


class HostMemoryTest(unittest.TestCase):
    """taikei.php の host_rate_limits.json と同じ置き場・同じ形で覚える。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = host_rate_limit.host_rate_limit_path(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_same_file_name_as_taikei(self) -> None:
        self.assertEqual(self.path.name, "host_rate_limits.json")
        source = (ROOT / "tools" / "reiki" / "scrapers" / "taikei.php").read_text(encoding="utf-8")
        self.assertIn("'host_rate_limits.json'", source)
        self.assertIn(f"TAIKEI_RATE_LIMIT_MEMORY_SECONDS = {host_rate_limit.RATE_LIMIT_MEMORY_SECONDS}", source)

    def test_remember_and_forget(self) -> None:
        now = 1_762_840_000
        self.assertFalse(host_rate_limit.host_rate_limited_recently("legal-square.com", self.path, now=now))
        host_rate_limit.remember_host_rate_limit("legal-square.com", self.path, now=now)
        self.assertTrue(host_rate_limit.host_rate_limited_recently("legal-square.com", self.path, now=now + 3600))
        self.assertFalse(host_rate_limit.host_rate_limited_recently("www1.g-reiki.net", self.path, now=now))
        # 1 日たてば忘れる。取得元が制限を緩めることもある。
        later = now + host_rate_limit.RATE_LIMIT_MEMORY_SECONDS
        self.assertFalse(host_rate_limit.host_rate_limited_recently("legal-square.com", self.path, now=later))

    def test_keeps_what_taikei_wrote(self) -> None:
        now = 1_762_840_000
        self.path.write_text(json.dumps({"www1.g-reiki.net": now - 60, "old.example": now - 90000}), encoding="utf-8")
        host_rate_limit.remember_host_rate_limit("legal-square.com", self.path, now=now)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        # PHP と同じく整数の epoch 秒で書き、期限切れは落とす。
        self.assertEqual(payload, {"www1.g-reiki.net": now - 60, "legal-square.com": now})
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_broken_file_is_not_fatal(self) -> None:
        self.path.write_text("{", encoding="utf-8")
        self.assertFalse(host_rate_limit.host_rate_limited_recently("legal-square.com", self.path))
        host_rate_limit.remember_host_rate_limit("legal-square.com", self.path)
        self.assertTrue(host_rate_limit.host_rate_limited_recently("legal-square.com", self.path))


class PacerTest(unittest.TestCase):
    def make(self, *, remembered: bool = False):
        clock = [100.0]
        slept: list[float] = []

        def sleep(seconds: float) -> None:
            slept.append(seconds)
            clock[0] += seconds

        pacer = host_rate_limit.Pacer(
            normal_interval=1.0,
            slow_interval=4.0,
            max_interval=15.0,
            remembered=remembered,
            clock=lambda: clock[0],
            sleep=sleep,
        )
        return pacer, clock, slept

    def test_keeps_the_interval(self) -> None:
        pacer, clock, slept = self.make()
        self.assertEqual(pacer.wait_turn(), 0.0)
        clock[0] += 0.25
        self.assertAlmostEqual(pacer.wait_turn(), 0.75)
        # 本文の読み込みに間隔より長くかかったなら待たない。
        clock[0] += 3.0
        self.assertEqual(pacer.wait_turn(), 0.0)
        self.assertEqual(slept, [0.75])

    def test_slows_down_after_a_rate_limit(self) -> None:
        pacer, _clock, _slept = self.make()
        self.assertEqual(pacer.slow_down(), 4.0)
        self.assertEqual(pacer.slow_down(), 8.0)
        self.assertEqual(pacer.slow_down(), 15.0)
        self.assertEqual(pacer.slow_down(), 15.0)

    def test_starts_slow_when_remembered(self) -> None:
        pacer, _clock, _slept = self.make(remembered=True)
        self.assertEqual(pacer.interval, 4.0)


class BackoffTest(unittest.TestCase):
    def make(self, budget: float = 3600) -> host_rate_limit.Backoff:
        return host_rate_limit.Backoff(base=30, max_wait=600, max_attempts=5, budget=budget)

    def test_doubles(self) -> None:
        backoff = self.make()
        self.assertEqual([backoff.next_wait(n, None) for n in range(1, 6)], [30, 60, 120, 240, 480])
        self.assertEqual(backoff.total_waited, 930)
        self.assertEqual(backoff.hits, 5)

    def test_retry_after_wins_when_longer(self) -> None:
        backoff = self.make()
        self.assertEqual(backoff.next_wait(1, 90), 90)
        self.assertEqual(backoff.next_wait(2, 5), 60)

    def test_too_long_a_retry_after_is_deferred(self) -> None:
        with self.assertRaises(host_rate_limit.RateLimitDeferred) as caught:
            self.make().next_wait(1, 7200)
        self.assertEqual(caught.exception.retry_after, 7200)

    def test_gives_up_after_the_attempts(self) -> None:
        backoff = self.make()
        for n in range(1, 6):
            backoff.next_wait(n, None)
        with self.assertRaises(host_rate_limit.RateLimitDeferred):
            backoff.next_wait(6, None)

    def test_budget_for_the_whole_walk(self) -> None:
        backoff = self.make(budget=100)
        backoff.next_wait(1, None)
        backoff.next_wait(2, None)
        with self.assertRaises(host_rate_limit.RateLimitDeferred):
            backoff.next_wait(1, None)


class ManifestMergeTest(unittest.TestCase):
    def test_keeps_rows_not_walked_this_time(self) -> None:
        previous = [{"source_file": "a.html", "title": "旧A"}, {"source_file": "b.html", "title": "B"}]
        current = [{"source_file": "a.html", "title": "新A"}, {"source_file": "c.html", "title": "C"}]
        merged = legal_square.merge_previous_manifest(previous, current)
        self.assertEqual([row["title"] for row in merged], ["新A", "C", "B"])

    def test_retry_time(self) -> None:
        now = 1_000_000.0
        self.assertEqual(
            legal_square.rate_limit_retry_at(None, now=now), int(now + legal_square.RATE_LIMIT_DEFER_MIN_SECONDS)
        )
        self.assertEqual(legal_square.rate_limit_retry_at(5 * 3600, now=now), int(now + 5 * 3600))


class RateLimitRunTest(unittest.TestCase):
    """偽の取得元で run() を通す。待ちは time.sleep を差し替えて飛ばす。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name)
        self.root = fake_work_root(self.work)
        self.memory = host_rate_limit.host_rate_limit_path(self.root.parent)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def log(self) -> str:
        return (self.work / "log.txt").read_text(encoding="utf-8")

    def test_a_body_is_fetched_after_waiting(self) -> None:
        # 本番の熱海市・豊橋市と同じ形。本文ビューアが 429 を返す。以前は 1.5 秒で
        # 1 度だけ開き直して失敗にしていた。
        source = FakeSource(25, too_many={"テスト条例12": 2})
        result = run_fake(source, self.work)
        coverage = result["coverage"]
        self.assertTrue(coverage["complete"])
        self.assertEqual(coverage["failed"], 0)
        self.assertEqual(len(result["manifest"]), 25)
        rate = coverage["rate_limit"]
        self.assertFalse(rate["deferred"])
        self.assertEqual(rate["hits"], 2)
        self.assertEqual(rate["waited_seconds"], 30 + 60)
        self.assertEqual(rate["host"], "legal-square.com")
        # 待って開き直した分は、空の本文の開き直し（BODY_FETCH_ATTEMPTS）に数えない。
        self.assertEqual(source.opened.count("テスト条例12"), 3)
        # 429 を受けたら本文を開く間隔を広げ（4 秒、続けば 8 秒）、取得元全体で 1 日覚える。
        self.assertEqual(rate["body_interval_seconds"], 2 * legal_square.BODY_INTERVAL_AFTER_RATE_LIMIT_SECONDS)
        self.assertTrue(host_rate_limit.host_rate_limited_recently("legal-square.com", self.memory))
        self.assertIn("取りすぎの制限（HTTP 429）を返したので、30秒待って", self.log())
        # 入り直してはいない（入り直すと要求がかえって増える）。
        self.assertEqual(coverage["session_renewals"], 0)

    def test_retry_after_is_obeyed(self) -> None:
        source = FakeSource(25, too_many={"テスト条例5": 1}, retry_after=90)
        result = run_fake(source, self.work)
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["rate_limit"]["waited_seconds"], 90)

    def test_the_next_walk_starts_slow(self) -> None:
        # 次の自治体は別のプロセス。覚えていなければ全速で始めて 429 を受け直す。
        host_rate_limit.remember_host_rate_limit("legal-square.com", self.memory)
        result = run_fake(FakeSource(5), self.work)
        rate = result["coverage"]["rate_limit"]
        self.assertTrue(rate["remembered_at_start"])
        self.assertEqual(rate["body_interval_seconds"], legal_square.BODY_INTERVAL_AFTER_RATE_LIMIT_SECONDS)
        self.assertIn("本文を 4 秒おきに開きます", self.log())

    def test_a_search_answered_with_429_is_searched_again(self) -> None:
        # 期間で割る途中の検索に 429。0 件や前の結果を読んで区間を落とさない。
        source = FakeSource(150, spread_dates=True, too_many_searches={3})
        result = run_fake(source, self.work)
        coverage = result["coverage"]
        self.assertTrue(coverage["complete"])
        self.assertEqual(coverage["unresolved"], [])
        self.assertEqual(coverage["session_renewals"], 0)
        self.assertEqual(coverage["rate_limit"]["hits"], 1)
        self.assertEqual(len(result["manifest"]), 150)

    def test_unending_limit_defers_instead_of_failing(self) -> None:
        # 前回は全部取れている。毎日の巡回（--check-updates）の 11 件目から先が
        # ずっと 429。待ちきれないので止めて、残りは後で取り直す。
        first = run_fake(FakeSource(25), self.work)
        self.assertEqual(len(first["manifest"]), 25)
        source = FakeSource(25, too_many_from=11)
        before = time.time()
        result = run_fake(source, self.work, check_updates=True)
        coverage = result["coverage"]
        rate = coverage["rate_limit"]
        self.assertTrue(rate["deferred"])
        self.assertFalse(coverage["complete"])
        self.assertTrue(coverage["stopped_early"])
        # 失敗にも、取り切れなかった区間にも数えない。
        self.assertEqual(coverage["failed"], 0)
        self.assertEqual(coverage["unresolved"], [])
        # 前回の一覧は縮めない。歩けなかった分は前回の行のまま残す。
        self.assertEqual(len(result["manifest"]), 25)
        self.assertFalse(coverage["manifest_shrunk"])
        # 11 件目は最初の 1 回と、待って開き直した 5 回。そこで止める。
        self.assertEqual(source.opened.count("テスト条例11"), 1 + legal_square.RATE_LIMIT_MAX_ATTEMPTS)
        self.assertEqual(len(source.opened), 10 + 1 + legal_square.RATE_LIMIT_MAX_ATTEMPTS)
        self.assertGreaterEqual(rate["retry_at"], int(before + legal_square.RATE_LIMIT_DEFER_MIN_SECONDS))
        self.assertTrue(rate["retry_at_text"].endswith("+09:00"))
        # バッチの進捗は n/n にしない（取れた記録を残さない）。
        state = json.loads((self.root / "scrape_state.json").read_text(encoding="utf-8"))
        self.assertLess(state["progress_current"], state["progress_total"])
        self.assertIn("残りは", self.log())

    def test_a_long_retry_after_is_not_waited_for(self) -> None:
        source = FakeSource(25, too_many_from=11, retry_after=4 * 3600)
        before = time.time()
        result = run_fake(source, self.work)
        rate = result["coverage"]["rate_limit"]
        self.assertTrue(rate["deferred"])
        self.assertEqual(rate["waited_seconds"], 0)
        self.assertEqual(source.opened.count("テスト条例11"), 1)
        self.assertGreaterEqual(rate["retry_at"], int(before + 4 * 3600))
        # 初めての自治体でも、取れた 10 件は一覧に載る。
        self.assertEqual(len(result["manifest"]), 10)

    def test_the_entrance_answered_with_429(self) -> None:
        source = FakeSource(5, too_many_logins={1})
        result = run_fake(source, self.work)
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["rate_limit"]["hits"], 1)
        self.assertEqual(source.logins, 2)

    def test_the_entrance_never_opens(self) -> None:
        # 初めての自治体で入口から先へ進めない。失敗にも空の一覧にもしない。
        source = FakeSource(5, too_many_logins={0})
        result = run_fake(source, self.work)
        coverage = result["coverage"]
        self.assertTrue(coverage["rate_limit"]["deferred"])
        self.assertEqual(coverage["collected"], 0)
        self.assertEqual(result["manifest"], [])
        self.assertFalse(coverage["manifest_shrunk"])
        self.assertEqual(source.logins, 1 + legal_square.RATE_LIMIT_MAX_ATTEMPTS)

    def test_a_walk_without_429_is_unchanged(self) -> None:
        source = FakeSource(25)
        result = run_fake(source, self.work)
        rate = result["coverage"]["rate_limit"]
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(rate["hits"], 0)
        self.assertFalse(rate["deferred"])
        self.assertEqual(rate["body_interval_seconds"], legal_square.BODY_INTERVAL_SECONDS)
        self.assertFalse(self.memory.exists())


class DeferredBatchTest(unittest.TestCase):
    """途中で止めた走査を、バッチは失敗にせず、優先度計算は取得元が求めた時刻まで待たせる。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "reiki" / "99999-test"
        self.root.mkdir(parents=True)
        self.target = {"slug": "99999-test", "work_root": str(self.root)}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, *, deferred: bool, retry_at: int, **extra) -> None:
        payload = {
            "version": 2,
            "declares": True,
            "observed_at": "20261111_120000",
            "complete": False,
            "unresolved": [],
            "failed": 0,
            "collected": 10,
            "rate_limit": {
                "deferred": deferred,
                "retry_at": retry_at,
                "retry_at_text": "2026-11-11 15:00:00+09:00",
            },
            **extra,
        }
        (self.root / "source_coverage.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_deferred_walk_is_not_a_failure(self) -> None:
        self.write(deferred=True, retry_at=int(time.time()) + 3600)
        self.assertEqual(scrape_all_reiki.scrape_completion_error(self.target, None), "")

    def test_an_incomplete_walk_is_still_a_failure(self) -> None:
        self.write(deferred=False, retry_at=0, failed=3)
        self.assertIn("3 件", scrape_all_reiki.scrape_completion_error(self.target, None))

    def test_waits_until_the_retry_time(self) -> None:
        now = time.time()
        self.write(deferred=True, retry_at=int(now) + 3600)
        deferred, reason = priority.reiki_rate_limit_deferral(self.target, now=now)
        self.assertTrue(deferred)
        self.assertIn("2026-11-11 15:00:00+09:00 まで待機", reason)
        deferred, reason = priority.reiki_rate_limit_deferral(self.target, now=now + 3601)
        self.assertTrue(deferred)
        self.assertEqual(reason, "")

    def test_a_new_walk_no_longer_waits(self) -> None:
        self.write(deferred=True, retry_at=int(time.time()) + 3600, walk_started_at="20261111_130000")
        self.assertEqual(priority.reiki_rate_limit_deferral(self.target), (False, ""))
        self.assertFalse(scrape_all_reiki.rate_limit_deferred(self.target))

    def test_queue_label(self) -> None:
        self.write(deferred=True, retry_at=int(time.time()) + 3600)
        with mock.patch.object(priority, "task_item", lambda task, slug: {}):
            info = scrape_all_reiki.reiki_priority._compute_priority_info("99999-test", self.target)
        self.assertEqual(info["priority_label"], "rate_limited_wait")
        self.assertEqual(info["priority_score"], 0)
        self.assertIn("取りすぎの制限", info["wait_reason"])

    def test_after_the_retry_time_it_runs_without_the_daily_wait(self) -> None:
        # 止めた走査は件数が前回より減って見える。24 時間の待ち
        # （incomplete_wait_reason）にかかると、取り直しが 1 日遅れる。
        self.write(deferred=True, retry_at=int(time.time()) - 60)
        finished = (datetime.datetime.now() - datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        item = {"status": "ok", "returncode": 0, "finished_at": finished, "progress_current": 10, "progress_total": 11}
        with (
            mock.patch.object(priority, "task_item", lambda task, slug: dict(item) if task == "reiki" else {}),
            mock.patch.object(priority, "incomplete_wait_reason", side_effect=AssertionError("24 時間の待ちを見た")),
        ):
            info = scrape_all_reiki.reiki_priority._compute_priority_info("99999-test", self.target)
        self.assertEqual(info["priority_label"], "incomplete")
        self.assertGreater(info["priority_score"], 0)


if __name__ == "__main__":
    unittest.main()
