"""登録した入口が死んだ自治体を、取得元の探索に回す。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import discovered_sources
from tools.tasks import discover_sources


class DeadEntryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        # discover_sources は sys.path 経由で読んだ別の discovered_sources を使う。両方を差し替えないと、
        # 手元の本物の work/gijiroku/discovered_sources.json に書いてしまう。
        for module in {discovered_sources, discover_sources.discovered_sources}:
            patcher = mock.patch.object(module, "WORK_ROOT", Path(self.directory.name))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store = discover_sources.discovered_sources

    def test_dead_entry_is_due_until_it_has_been_searched_from_that_entry(self) -> None:
        dead = {"01234": "https://example.jp/gikai/old/"}
        self.assertEqual(discover_sources.due_dead_entries("gijiroku", dead, retry_days=14), ["01234"])

        self.store.record("gijiroku", "01234", confidence="none", replaces_url="https://example.jp/gikai/old/")
        self.assertEqual(discover_sources.due_dead_entries("gijiroku", dead, retry_days=14), [])

    def test_a_record_for_another_entry_does_not_count(self) -> None:
        # 前に空の行として探した記録は、いまの死んだ入口を探し直したことにならない。
        self.store.record("gijiroku", "01234", url="https://example.jp/gikai/old/",
                                  system_type="独自", confidence="medium")
        dead = {"01234": "https://example.jp/gikai/old/"}
        self.assertEqual(discover_sources.due_dead_entries("gijiroku", dead, retry_days=14), ["01234"])

    def test_run_records_the_replaced_entry(self) -> None:
        dead = {"01234": "https://example.jp/gikai/old/"}
        found = {"url": "https://example.jp/gikai/new/", "system_type": "独自", "confidence": "medium", "note": "link"}
        with mock.patch.object(discover_sources, "unresolved_codes", return_value=[]), mock.patch.object(
            discover_sources, "dead_entries", return_value=dead
        ), mock.patch.object(
            discover_sources, "load_names_and_homepages", return_value=({"01234": "例町"}, {"01234": "https://example.jp/"})
        ), mock.patch.dict(discover_sources.DISCOVERERS, {"gijiroku": lambda *_args: found}):
            result = discover_sources.run("gijiroku", limit=20)

        self.assertEqual(result["registered"], 1)
        entry = self.store.load("gijiroku")["01234"]
        self.assertEqual(entry["replaces_url"], "https://example.jp/gikai/old/")
        self.assertEqual(
            self.store.apply_to_row(entry, "https://example.jp/gikai/old/", "独自")[0],
            "https://example.jp/gikai/new/",
        )

    def test_finding_only_the_dead_entry_is_not_registered(self) -> None:
        dead = {"01234": "https://example.jp/gikai/old/"}
        found = {"url": "https://example.jp/gikai/old/", "system_type": "独自", "confidence": "high", "note": "link"}
        with mock.patch.object(discover_sources, "unresolved_codes", return_value=[]), mock.patch.object(
            discover_sources, "dead_entries", return_value=dead
        ), mock.patch.object(
            discover_sources, "load_names_and_homepages", return_value=({"01234": "例町"}, {"01234": "https://example.jp/"})
        ), mock.patch.dict(discover_sources.DISCOVERERS, {"gijiroku": lambda *_args: found}):
            result = discover_sources.run("gijiroku", limit=20)

        self.assertEqual(result["registered"], 0)
        self.assertEqual(discover_sources.due_dead_entries("gijiroku", dead, retry_days=14), [])


if __name__ == "__main__":
    unittest.main()
