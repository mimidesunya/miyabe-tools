from __future__ import annotations

import unittest
from pathlib import Path
import time
import tempfile
from types import SimpleNamespace

from tools.tasks import batch


class FakePriority:
    def __init__(self, info_by_slug: dict[str, dict[str, object]]) -> None:
        self.info_by_slug = info_by_slug

    def target_priority_info(self, target: dict) -> dict[str, object]:
        return self.info_by_slug[str(target["slug"])]

    def sort_targets_by_priority(self, targets: list[dict]) -> list[dict]:
        return sorted(targets, key=lambda target: str(target["slug"]))


class SelectRunnableTargetsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.targets = [
            {"slug": "failed"},
            {"slug": "recent"},
            {"slug": "runnable"},
        ]
        self.spec = SimpleNamespace(
            priority=FakePriority(
                {
                    "failed": {"priority_score": 0, "priority_label": "previous_failed"},
                    "recent": {"priority_score": 0, "priority_label": "recent_complete"},
                    "runnable": {"priority_score": 1, "priority_label": "unknown_total"},
                }
            )
        )

    def test_previous_failures_are_excluded_by_default(self) -> None:
        selected = batch.select_runnable_targets(self.spec, self.targets)
        self.assertEqual(["runnable"], [target["slug"] for target in selected])

    def test_retry_failed_includes_only_previous_failures_among_zero_scores(self) -> None:
        selected = batch.select_runnable_targets(self.spec, self.targets, retry_failed=True)
        self.assertEqual(["failed", "runnable"], [target["slug"] for target in selected])

class StalledWorkersTest(unittest.TestCase):
    def _worker(self, tmpdir, text: str) -> dict:
        path = Path(tmpdir) / "out.log"
        path.write_text(text, encoding="utf-8")
        return {
            "target": {"slug": "x"},
            "stdout_path": str(path),
            "stderr_path": "",
            "output_size": 0,
            "output_seen_at": time.monotonic() - 10_000,
        }

    def test_worker_that_keeps_writing_is_not_stalled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = self._worker(tmpdir, "進んでいる")
            # 前回の記録と大きさが違う=進んでいるので、打ち切らない。
            self.assertEqual(batch.stalled_workers([worker], 1800), [])

    def test_silent_worker_is_stalled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = self._worker(tmpdir, "止まっている")
            worker["output_size"] = batch.worker_output_size(worker)
            self.assertEqual(batch.stalled_workers([worker], 1800), [worker])

    def test_zero_disables_the_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = self._worker(tmpdir, "止まっている")
            worker["output_size"] = batch.worker_output_size(worker)
            self.assertEqual(batch.stalled_workers([worker], 0), [])


if __name__ == "__main__":
    unittest.main()
