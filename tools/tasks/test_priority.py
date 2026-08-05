from __future__ import annotations

import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
