#!/usr/bin/env python3
"""鮮度（持っている中でいちばん新しい文書の日付）が後戻りしないことを確かめる。

`plan_summary.date_max` は**その実行が計画した分**の最大日でしかない。
途中でエラーになって古い年しか計画しなかった実行があると、実際に持って
いる文書より古い日付へ置き換わる。仙台市は 2026-02-17 の会議録を検索
できるのに 1991-01-14 と表示されていた。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.append(str(Path(__file__).resolve().parent))

import freshness_metadata


class RecordedFreshnessTest(unittest.TestCase):
    def test_takes_the_newest_of_live_and_snapshot(self) -> None:
        items = {
            "gijiroku": {"04100-sendai-shi": {"freshness_date": "1991-01-14"}},
            "gijiroku_snapshot": {"04100-sendai-shi": {"freshness_date": "2026-02-17"}},
        }
        with mock.patch.object(
            freshness_metadata,
            "status_item",
            side_effect=lambda task, slug: items.get(task, {}).get(slug, {}),
        ):
            self.assertEqual(
                freshness_metadata.recorded_freshness_date("gijiroku", "04100-sendai-shi"),
                "2026-02-17",
            )

    def test_empty_slug_has_no_record(self) -> None:
        self.assertEqual(freshness_metadata.recorded_freshness_date("gijiroku", ""), "")


class GijirokuFreshnessTest(unittest.TestCase):
    def build_target(self, directory: str, date_max: str) -> dict:
        work_dir = Path(directory)
        (work_dir / "scrape_state.json").write_text(
            json.dumps({"plan_summary": {"date_max": date_max}}),
            encoding="utf-8",
        )
        return {
            "slug": "04100-sendai-shi",
            "work_dir": str(work_dir),
            "index_json_path": str(work_dir / "missing.json"),
        }

    def freshness(self, target: dict, recorded: str) -> dict[str, str]:
        with mock.patch.object(
            freshness_metadata, "recorded_freshness_date", return_value=recorded
        ):
            return freshness_metadata.gijiroku_target_freshness(target)

    def test_partial_run_does_not_move_the_date_backwards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = self.build_target(directory, "1991-01-14")
            info = self.freshness(target, "2026-02-17")
            self.assertEqual(info["freshness_date"], "2026-02-17")
            self.assertEqual(info["freshness_basis"], "latest_document")

    def test_newer_run_moves_the_date_forward(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = self.build_target(directory, "2026-08-20")
            info = self.freshness(target, "2026-02-17")
            self.assertEqual(info["freshness_date"], "2026-08-20")

    def test_no_record_and_no_plan_leaves_it_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = self.build_target(directory, "")
            info = self.freshness(target, "")
            self.assertEqual(info["freshness_date"], "")
            self.assertEqual(info["freshness_basis"], "")


if __name__ == "__main__":
    unittest.main()
