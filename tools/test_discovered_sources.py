#!/usr/bin/env python3
"""探索で見つけた取得元の記録と適用を確かめる。

登録簿に URL が無い自治体は巡回のキューに載らない。載らない限り放置しても
状態は変わらないので、探索の結果を実行時に重ねて取得の対象にする。
人が書いた登録簿の値は常に優先する。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

import discovered_sources


class UsabilityTest(unittest.TestCase):
    def test_only_high_and_medium_are_used(self) -> None:
        for confidence, expected in (("high", True), ("medium", True), ("low", False), ("none", False), ("", False)):
            entry = {"url": "https://example.test/gikai/", "system_type": "独自", "confidence": confidence}
            self.assertEqual(discovered_sources.is_usable(entry), expected, confidence)

    def test_a_result_without_a_system_type_is_unusable(self) -> None:
        # 系統が決まらないとスクレイパを選べない。
        entry = {"url": "https://example.test/gikai/", "system_type": "", "confidence": "high"}
        self.assertFalse(discovered_sources.is_usable(entry))

    def test_a_result_without_a_url_is_unusable(self) -> None:
        entry = {"url": "", "system_type": "独自", "confidence": "high"}
        self.assertFalse(discovered_sources.is_usable(entry))


class ApplyTest(unittest.TestCase):
    entry = {"url": "https://example.test/gikai/", "system_type": "独自", "confidence": "high"}

    def test_fills_an_empty_registry_row(self) -> None:
        url, system_type, replaced = discovered_sources.apply_to_row(self.entry, "", "")
        self.assertEqual(url, "https://example.test/gikai/")
        self.assertEqual(system_type, "独自")
        self.assertTrue(replaced)

    def test_never_overrides_a_registered_url(self) -> None:
        # 人が書いた値が優先。TSV が埋まれば探索結果は使われなくなる。
        url, system_type, replaced = discovered_sources.apply_to_row(
            self.entry, "https://written.example/gikai/", "dbsr"
        )
        self.assertEqual(url, "https://written.example/gikai/")
        self.assertEqual(system_type, "dbsr")
        self.assertFalse(replaced)

    def test_unusable_results_change_nothing(self) -> None:
        weak = {"url": "https://example.test/x", "system_type": "", "confidence": "low"}
        self.assertEqual(discovered_sources.apply_to_row(weak, "", ""), ("", "", False))


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.original = discovered_sources.WORK_ROOT
        discovered_sources.WORK_ROOT = Path(self.directory.name)

    def tearDown(self) -> None:
        discovered_sources.WORK_ROOT = self.original
        self.directory.cleanup()

    def test_records_round_trip(self) -> None:
        discovered_sources.record("gijiroku", "01234", url="https://example.test/",
                                  system_type="独自", confidence="high", note="link")
        entries = discovered_sources.load("gijiroku")
        self.assertEqual(entries["01234"]["system_type"], "独自")
        self.assertTrue(entries["01234"]["observed_at"])

    def test_broken_store_is_ignored(self) -> None:
        path = discovered_sources.store_path("gijiroku")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ broken", encoding="utf-8")
        self.assertEqual(discovered_sources.load("gijiroku"), {})

    def test_due_codes_skips_recent_attempts_and_solved_ones(self) -> None:
        discovered_sources.record("gijiroku", "solved", url="https://example.test/",
                                  system_type="独自", confidence="high")
        discovered_sources.record("gijiroku", "recent", confidence="none")
        due = discovered_sources.due_codes(
            "gijiroku", ["solved", "recent", "fresh"], retry_days=14
        )
        # 解決済みは再探索しない。直近で試した分も飛ばす。未実施だけ残る。
        self.assertEqual(due, ["fresh"])

    def test_due_codes_returns_the_oldest_attempts_again(self) -> None:
        entries = {
            "old": {"confidence": "none", "observed_at": "2026-01-01 00:00:00"},
            "newer": {"confidence": "none", "observed_at": "2026-02-01 00:00:00"},
        }
        discovered_sources.save("gijiroku", entries)
        due = discovered_sources.due_codes(
            "gijiroku", ["newer", "old"], retry_days=14, now="2026-09-01 00:00:00"
        )
        self.assertEqual(due, ["old", "newer"])

    def test_limit_caps_the_batch(self) -> None:
        due = discovered_sources.due_codes("gijiroku", ["a", "b", "c"], limit=2)
        self.assertEqual(len(due), 2)


if __name__ == "__main__":
    unittest.main()
