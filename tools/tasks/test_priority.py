from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

# priority.py は呼び出し元が tools/ を sys.path に載せる前提で
# freshness_metadata をトップレベル import している。
sys.path.append(str(Path(__file__).resolve().parents[1]))

from tools.tasks import priority


class ResidualFailureTest(unittest.TestCase):
    def test_missing_one_of_many_is_residual(self) -> None:
        item = {"progress_current": 1029, "progress_total": 1030}
        self.assertTrue(priority.residual_failure_only(item))

    def test_small_absolute_gap_is_residual(self) -> None:
        item = {"progress_current": 2438, "progress_total": 2440}
        self.assertTrue(priority.residual_failure_only(item))

    def test_ratio_allowance_scales_with_total(self) -> None:
        # 1% 許容なので 10000 件なら 100 件までは残り扱いにする。
        self.assertTrue(priority.residual_failure_only({"progress_current": 9900, "progress_total": 10000}))
        self.assertFalse(priority.residual_failure_only({"progress_current": 9800, "progress_total": 10000}))

    def test_large_gap_is_not_residual(self) -> None:
        item = {"progress_current": 494, "progress_total": 581}
        self.assertFalse(priority.residual_failure_only(item))

    def test_nothing_acquired_is_not_residual(self) -> None:
        self.assertFalse(priority.residual_failure_only({"progress_current": 0, "progress_total": 2528}))

    def test_unknown_total_is_not_residual(self) -> None:
        self.assertFalse(priority.residual_failure_only({"progress_current": 0, "progress_total": 0}))

    def test_complete_is_not_residual(self) -> None:
        self.assertFalse(priority.residual_failure_only({"progress_current": 10, "progress_total": 10}))


class PreviousItemFailedTest(unittest.TestCase):
    def _with_item(self, item: dict[str, object]):
        return mock.patch.object(priority, "task_item", return_value=item)

    def test_residual_failure_is_not_hard_failure(self) -> None:
        item = {
            "status": "failed",
            "returncode": -1,
            "message": "取得失敗: 1件",
            "progress_current": 1029,
            "progress_total": 1030,
        }
        with self._with_item(item):
            self.assertFalse(priority.previous_item_failed_with_error("gijiroku", "12217-kashiwa-shi"))

    def test_large_gap_stays_hard_failure(self) -> None:
        item = {
            "status": "failed",
            "returncode": -1,
            "message": "取得未完了: 494/581",
            "progress_current": 494,
            "progress_total": 581,
        }
        with self._with_item(item):
            self.assertTrue(priority.previous_item_failed_with_error("gijiroku", "24214-inabe-shi"))

    def test_index_failure_stays_hard_failure(self) -> None:
        # 検索投入の失敗は取得漏れの量に関係なく実エラーとして扱う。
        item = {
            "status": "failed",
            "index_status": "failed",
            "progress_current": 1029,
            "progress_total": 1030,
        }
        with self._with_item(item):
            self.assertTrue(priority.previous_item_failed_with_error("gijiroku", "12217-kashiwa-shi"))

    def test_stopped_item_is_not_failure(self) -> None:
        item = {"status": "failed", "message": "停止しました", "progress_current": 1, "progress_total": 10}
        with self._with_item(item):
            self.assertFalse(priority.previous_item_failed_with_error("gijiroku", "x"))

class FailureIsRetryableTest(unittest.TestCase):
    def test_recent_failure_waits_for_manual_handling(self) -> None:
        recent = (
            priority.freshness_metadata.now_tokyo() - timedelta(days=1)
        ).strftime("%Y-%m-%d %H:%M:%S")
        self.assertFalse(priority.failure_is_retryable(recent))

    def test_old_failure_is_retried_automatically(self) -> None:
        # 一度の失敗で永久に巡回対象から外れないようにする。
        old = (
            priority.freshness_metadata.now_tokyo()
            - timedelta(days=priority.FAILED_RETRY_DAYS + 1)
        ).strftime("%Y-%m-%d %H:%M:%S")
        self.assertTrue(priority.failure_is_retryable(old))

    def test_unknown_failure_time_does_not_become_permanent_manual_work(self) -> None:
        self.assertTrue(priority.failure_is_retryable(""))

    def test_quick_failure_is_retried_after_a_day(self) -> None:
        # 2 秒で落ちた失敗（入口ページの一時的な応答なし等）は 7 日も待たない。
        finished = priority.freshness_metadata.now_tokyo() - timedelta(days=2)
        started = finished - timedelta(seconds=2)
        fmt = "%Y-%m-%d %H:%M:%S"
        self.assertTrue(priority.failure_is_retryable(finished.strftime(fmt), started.strftime(fmt)))

    def test_long_failure_still_waits_a_week(self) -> None:
        finished = priority.freshness_metadata.now_tokyo() - timedelta(days=2)
        started = finished - timedelta(minutes=30)
        fmt = "%Y-%m-%d %H:%M:%S"
        self.assertFalse(priority.failure_is_retryable(finished.strftime(fmt), started.strftime(fmt)))


class FailureReferenceTimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        priority.batch_status.configure_status_root(self.tmp.name)
        priority._TASK_STATUS_CACHE.clear()

    def tearDown(self) -> None:
        priority._TASK_STATUS_CACHE.clear()
        priority.batch_status.configure_status_root(None)
        self.tmp.cleanup()

    def _write_failed(self, item: dict[str, object], *, task_name: str = "gijiroku") -> Path:
        path = priority.batch_status.status_path(task_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"items": {"x": item}}), encoding="utf-8")
        priority._TASK_STATUS_CACHE.clear()
        return path

    def test_updated_at_is_used_when_finished_at_is_invalid(self) -> None:
        old = (priority.freshness_metadata.now_tokyo() - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S")
        item = {"status": "failed", "finished_at": "broken", "updated_at": old}
        self._write_failed(item)
        loaded = priority.task_item("gijiroku", "x")
        self.assertEqual(old, priority.failure_reference_time("gijiroku", "x", loaded))
        self.assertTrue(priority.failure_is_retryable(old))

    def test_last_checked_at_is_the_next_fallback(self) -> None:
        old = (priority.freshness_metadata.now_tokyo() - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S")
        item = {"status": "failed", "finished_at": "", "updated_at": "bad", "last_checked_at": old}
        self._write_failed(item)
        loaded = priority.task_item("gijiroku", "x")
        self.assertEqual(old, priority.failure_reference_time("gijiroku", "x", loaded))

    def test_state_mtime_is_fixed_before_later_writes_move_it(self) -> None:
        item = {"status": "failed", "finished_at": "", "updated_at": "", "last_checked_at": ""}
        path = self._write_failed(item)
        old_timestamp = (priority.freshness_metadata.now_tokyo() - timedelta(days=8)).timestamp()
        os.utime(path, (old_timestamp, old_timestamp))
        loaded = priority.task_item("gijiroku", "x")
        observed = priority.failure_reference_time("gijiroku", "x", loaded)
        self.assertTrue(priority.failure_is_retryable(observed))
        persisted = json.loads(path.read_text(encoding="utf-8"))["items"]["x"]
        self.assertEqual(observed, persisted["failure_observed_at"])
        self.assertEqual("state_mtime", persisted["failure_observed_basis"])

        # 別itemの更新でstate自体のmtimeが動いても、最初の観測値は動かさない。
        os.utime(path, None)
        priority._TASK_STATUS_CACHE.clear()
        loaded_again = priority.task_item("gijiroku", "x")
        self.assertEqual(observed, priority.failure_reference_time("gijiroku", "x", loaded_again))

    def test_future_values_fall_back_to_a_bounded_observation(self) -> None:
        future = (priority.freshness_metadata.now_tokyo() + timedelta(days=3650)).strftime("%Y-%m-%d %H:%M:%S")
        item = {"status": "failed", "finished_at": future, "updated_at": future, "last_checked_at": future}
        self._write_failed(item)
        loaded = priority.task_item("gijiroku", "x")
        observed = priority.failure_reference_time("gijiroku", "x", loaded)
        parsed = priority.freshness_metadata.parse_datetime_text(observed)
        self.assertIsNotNone(parsed)
        self.assertLessEqual(parsed, priority.freshness_metadata.now_tokyo())


class InputFingerprintPriorityTest(unittest.TestCase):
    def setUp(self) -> None:
        priority._TASK_STATUS_CACHE.clear()

    def tearDown(self) -> None:
        priority._TASK_STATUS_CACHE.clear()

    def test_recent_success_with_old_url_is_not_recent_complete(self) -> None:
        old_target = {"source_url": "https://old.example/", "system_type": "dbsr"}
        current_target = {"source_url": "https://new.example/", "system_type": "dbsr"}
        item = {
            **old_target,
            "status": "snapshot",
            "returncode": 0,
            "progress_current": 10,
            "progress_total": 10,
            "finished_at": priority.batch_status.now_text(),
            "input_fingerprint": priority.input_generation.input_fingerprint("minutes", old_target),
            "index_status": "ok",
        }
        with mock.patch.object(priority, "task_item", return_value=item):
            recent, _ = priority.recently_completed_successfully(
                "gijiroku", "x", 10, 10, current_target
            )
        self.assertFalse(recent)

    def test_recent_success_with_current_fingerprint_is_skipped(self) -> None:
        target = {"source_url": "https://example.test/", "system_type": "d1-law"}
        item = {
            **target,
            "status": "snapshot",
            "returncode": 0,
            "progress_current": 3,
            "progress_total": 3,
            "finished_at": priority.batch_status.now_text(),
            "input_fingerprint": priority.input_generation.input_fingerprint("reiki", target),
            "index_status": "ok",
        }
        with mock.patch.object(priority, "task_item", return_value=item):
            recent, _ = priority.recently_completed_successfully("reiki", "x", 3, 3, target)
        self.assertTrue(recent)


class IncompleteWaitTest(unittest.TestCase):
    """取り切れていない自治体を、進まないまま 10 分おきにやり直さない。

    白浜町（legal-square）は上限に張り付いた葉が 2 つ残り、20 分かかる取得を
    30 分おきに繰り返していた。上富田町は前回より減った一覧を毎回書けずに
    同じだった。進んだなら続ける。進まなかったなら 1 日置く。
    """

    def _now_text(self, delta: timedelta) -> str:
        return (priority.freshness_metadata.now_tokyo() + delta).strftime("%Y-%m-%d %H:%M:%S")

    def test_same_count_shortly_after_waits(self) -> None:
        item = {
            "status": "failed",
            "returncode": -1,
            "message": "取り切れなかった区間が 2 件",
            "finished_at": self._now_text(timedelta(minutes=-30)),
            "progress_current": 499,
            "progress_total": 500,
        }
        with mock.patch.object(priority, "task_item", return_value=item):
            self.assertTrue(priority.incomplete_wait_reason("reiki", "30401-shirahama-cho", 499))

    def test_progress_continues_immediately(self) -> None:
        # 仙台市の 1,933 ページは 1 回では歩き切れない。前回より進んだなら続ける。
        item = {
            "status": "failed",
            "returncode": -1,
            "message": "partial_time",
            "finished_at": self._now_text(timedelta(minutes=-30)),
            "progress_current": 176,
            "progress_total": 177,
        }
        with mock.patch.object(priority, "task_item", return_value=item):
            self.assertEqual(priority.incomplete_wait_reason("gijiroku", "04100-sendai-shi", 1169), "")

    def test_wait_ends_after_a_day(self) -> None:
        item = {
            "status": "failed",
            "returncode": -1,
            "finished_at": self._now_text(timedelta(hours=-25)),
            "progress_current": 499,
            "progress_total": 500,
        }
        with mock.patch.object(priority, "task_item", return_value=item):
            self.assertEqual(priority.incomplete_wait_reason("reiki", "30401-shirahama-cho", 499), "")

    def test_interrupted_run_is_not_an_attempt(self) -> None:
        item = {
            "status": "failed",
            "returncode": -15,
            "message": "停止要求により終了",
            "finished_at": self._now_text(timedelta(minutes=-5)),
            "progress_current": 499,
            "progress_total": 500,
        }
        with mock.patch.object(priority, "task_item", return_value=item):
            self.assertEqual(priority.incomplete_wait_reason("reiki", "30401-shirahama-cho", 499), "")

    def test_no_previous_run_does_not_wait(self) -> None:
        with mock.patch.object(priority, "task_item", return_value={}):
            self.assertEqual(priority.incomplete_wait_reason("reiki", "new-town", 0), "")


if __name__ == "__main__":
    unittest.main()
