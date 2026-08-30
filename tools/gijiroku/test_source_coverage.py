"""走査記録の読み書きが、実際に繋がっていることを確かめる。

ここが無いまま直したせいで、例規の優先度と公開画面が work_dir という
存在しないキーを読み、判定が丸ごと空振りしていた。判定そのものより、
**判定が呼ばれているか**が壊れやすい。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "tools" / "gijiroku"),
    str(ROOT / "tools" / "reiki"),
    str(ROOT / "tools" / "tasks"),
    str(ROOT / "tools"),
]

import gijiroku_planning  # noqa: E402
import gijiroku_storage  # noqa: E402
import reiki_io  # noqa: E402


class WalkStateTest(unittest.TestCase):
    def test_missing_rule_version_is_not_trusted(self):
        # 古い規則で書かれた complete は、いまの complete と意味が違う。
        self.assertEqual(
            gijiroku_storage.effective_walk_state({"state": "complete"}), "stale_rule"
        )

    def test_rewalking_beats_complete(self):
        self.assertEqual(
            gijiroku_storage.effective_walk_state(
                {
                    "rule_version": 2,
                    "state": "complete",
                    "walk_started_at": "20260830_120000",
                    "updated_at": "20260830_110000",
                }
            ),
            "rewalking",
        )

    def test_finished_walk_is_complete(self):
        self.assertEqual(
            gijiroku_storage.effective_walk_state(
                {
                    "rule_version": 2,
                    "state": "complete",
                    "walk_started_at": "20260830_110000",
                    "updated_at": "20260830_120000",
                }
            ),
            "complete",
        )

    def test_empty_is_unknown_not_complete(self):
        for value in (None, {}, "complete", []):
            self.assertEqual(gijiroku_storage.effective_walk_state(value), "unknown")

    def test_non_numeric_version_does_not_raise(self):
        # 壊れた記録で例外を投げると、優先度計算ごと落ちる。
        for broken in ("v2", None, [], {}, "..."):
            self.assertEqual(
                gijiroku_storage.effective_walk_state(
                    {"rule_version": broken, "state": "complete"}
                ),
                "stale_rule",
            )


class DurableRecordTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_save_state_mirrors_the_walk_record(self):
        # scrape_state.json は実行の頭で消される。写しが残らないと、
        # 殺された実行が「全部歩けた」という記録ごと消してしまう。
        gijiroku_storage.save_state(
            self.dir / "scrape_state.json", {"source_coverage": {"state": "complete"}}
        )
        (self.dir / "scrape_state.json").unlink()
        self.assertEqual(
            gijiroku_storage.load_source_coverage(self.dir).get("state"), "complete"
        )

    def test_version_is_stamped_on_save(self):
        gijiroku_storage.save_source_coverage(self.dir, {"state": "complete"})
        payload = json.loads(
            (self.dir / "source_coverage.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            payload["rule_version"], gijiroku_storage.COVERAGE_RULE_VERSION
        )

    def test_old_version_in_payload_does_not_win(self):
        gijiroku_storage.save_source_coverage(
            self.dir, {"state": "complete", "rule_version": 1}
        )
        payload = json.loads(
            (self.dir / "source_coverage.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            payload["rule_version"], gijiroku_storage.COVERAGE_RULE_VERSION
        )

    def test_empty_never_overwrites(self):
        gijiroku_storage.save_source_coverage(self.dir, {"state": "complete"})
        gijiroku_storage.save_source_coverage(self.dir, {})
        self.assertEqual(
            gijiroku_storage.load_source_coverage(self.dir).get("state"), "complete"
        )

    def test_newer_state_wins_over_stale_copy(self):
        gijiroku_storage.save_source_coverage(
            self.dir, {"state": "complete", "updated_at": "1"}
        )
        merged = gijiroku_storage.load_source_coverage(
            self.dir, {"source_coverage": {"state": "partial_error", "updated_at": "9"}}
        )
        self.assertEqual(merged.get("state"), "partial_error")

    def test_mark_walk_started_keeps_the_previous_record(self):
        previous = {"state": "complete", "updated_at": "1", "discovered_count": 12}
        gijiroku_storage.save_source_coverage(self.dir, previous)
        gijiroku_storage.mark_walk_started(self.dir, previous, "20260830_120000")
        payload = gijiroku_storage.load_source_coverage(self.dir)
        self.assertEqual(payload.get("discovered_count"), 12)
        self.assertEqual(gijiroku_storage.effective_walk_state(payload), "rewalking")


class MeetingsIndexTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "meetings_index.json"

    def test_empty_discovery_does_not_erase_the_plan(self):
        # 野洲市は 1202 ファイルを取得済みなのに計画が [] になっていた。
        gijiroku_storage.save_meetings_index(self.path, [{"a": 1}, {"b": 2}])
        gijiroku_storage.save_meetings_index(self.path, [])
        self.assertEqual(len(json.loads(self.path.read_text(encoding="utf-8"))), 2)

    def test_non_empty_discovery_replaces_the_plan(self):
        gijiroku_storage.save_meetings_index(self.path, [{"a": 1}])
        gijiroku_storage.save_meetings_index(self.path, [{"c": 3}])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), [{"c": 3}])


class AllFailedTest(unittest.TestCase):
    def test_every_candidate_failing_is_reported(self):
        summary = gijiroku_storage.classified_scrape_summary(
            discovered_count=95, downloaded_count=0, status_counts={"error": 95}
        )
        self.assertTrue(summary["all_failed"])

    def test_partial_failure_is_not_all_failed(self):
        summary = gijiroku_storage.classified_scrape_summary(
            discovered_count=95, downloaded_count=90, status_counts={"error": 5}
        )
        self.assertFalse(summary["all_failed"])

    def test_no_candidates_is_not_all_failed(self):
        summary = gijiroku_storage.classified_scrape_summary(
            discovered_count=0, downloaded_count=0, status_counts={}
        )
        self.assertFalse(summary["all_failed"])


class YearDirTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_existing_full_width_dir_is_reused(self):
        # 保存先を正規化した名前へ変えると、全角の年しか持たない
        # 890 自治体・99,549 ファイルが一度に孤児になる。
        (self.dir / "令和５年").mkdir()
        self.assertEqual(
            gijiroku_planning.existing_year_dir(self.dir, "令和5年"), "令和５年"
        )

    def test_exact_match_wins(self):
        (self.dir / "令和５年").mkdir()
        (self.dir / "令和5年").mkdir()
        self.assertEqual(
            gijiroku_planning.existing_year_dir(self.dir, "令和5年"), "令和5年"
        )

    def test_unknown_year_is_left_alone(self):
        self.assertEqual(
            gijiroku_planning.existing_year_dir(self.dir, "令和6年"), "令和6年"
        )


class ReikiCoverageTest(unittest.TestCase):
    """例規側は work_root。work_dir を読むと判定が丸ごと空振りする。"""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        import priority

        self.progress = priority.reiki_coverage_progress

    def test_reads_work_root_not_work_dir(self):
        reiki_io.save_source_coverage(
            self.dir,
            {"version": 2, "declares": True, "complete": True, "collected": 40},
        )
        self.assertEqual(self.progress({"work_root": str(self.dir)}), (40, 40))

    def test_incomplete_stays_below_total(self):
        reiki_io.save_source_coverage(
            self.dir,
            {"version": 2, "declares": True, "complete": False, "collected": 40},
        )
        current, total = self.progress({"work_root": str(self.dir)})
        self.assertLess(current, total)

    def test_old_version_is_not_complete(self):
        reiki_io.save_source_coverage(
            self.dir,
            {"version": 1, "declares": True, "complete": True, "collected": 40},
        )
        current, total = self.progress({"work_root": str(self.dir)})
        self.assertLess(current, total)

    def test_catalog_record_counts_failures(self):
        # 目録型は目録に並んだ件数が申告。失敗した分は取れていない。
        reiki_io.save_source_coverage(
            self.dir,
            {
                "version": 2,
                "kind": "catalog",
                "declares": True,
                "declared_total": 10,
                "collected": 9,
                "failed": 1,
                "complete": False,
            },
        )
        current, total = self.progress({"work_root": str(self.dir)})
        self.assertLess(current, total)

    def test_catalog_record_without_failures_is_complete(self):
        reiki_io.save_source_coverage(
            self.dir,
            {
                "version": 2,
                "kind": "catalog",
                "declares": True,
                "declared_total": 10,
                "collected": 10,
                "failed": 0,
                "complete": True,
            },
        )
        self.assertEqual(self.progress({"work_root": str(self.dir)}), (10, 10))

    def test_catalog_sources_are_left_to_other_evidence(self):
        reiki_io.save_source_coverage(self.dir, {"version": 2, "declares": False})
        self.assertEqual(self.progress({"work_root": str(self.dir)}), (0, 0))

    def test_helper_rejects_old_version_and_rewalk(self):
        self.assertFalse(
            reiki_io.effective_coverage_complete({"version": 1, "complete": True})
        )
        self.assertFalse(
            reiki_io.effective_coverage_complete(
                {
                    "version": 2,
                    "complete": True,
                    "walk_started_at": "2",
                    "observed_at": "1",
                }
            )
        )
        self.assertTrue(
            reiki_io.effective_coverage_complete(
                {
                    "version": 2,
                    "complete": True,
                    "walk_started_at": "1",
                    "observed_at": "2",
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
