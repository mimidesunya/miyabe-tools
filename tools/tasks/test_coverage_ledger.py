"""取りこぼし台帳。

公開に 1 件も出ていない自治体を、原因まで分けて数える。原因ごとに打つ手が
違うので、まとめて「未取得」にはしない。
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.tasks import coverage_ledger as ledger  # noqa: E402
from tools.tasks import status as batch_status  # noqa: E402


class ClassifyTest(unittest.TestCase):
    def test_published_has_no_reason(self):
        self.assertEqual(ledger.classify("a", published=True, saved_files=0, excluded=False), "")

    def test_saved_but_not_indexed(self):
        """鳥栖市。取得は成功、索引は走らないまま。"""
        self.assertEqual(
            ledger.classify("a", published=False, saved_files=588, excluded=False),
            "not_indexed",
        )

    def test_nothing_saved(self):
        """能代市。取得元の作りが変わって 0 件だった。"""
        self.assertEqual(
            ledger.classify("a", published=False, saved_files=0, excluded=False),
            "no_saved_files",
        )

    def test_excluded_is_not_a_gap(self):
        """録画しか公開していない議会など、台帳で対象外にしたもの。"""
        self.assertEqual(
            ledger.classify("a", published=False, saved_files=0, excluded=True), "excluded"
        )

    def test_excluded_wins_over_saved_files(self):
        self.assertEqual(
            ledger.classify("a", published=False, saved_files=10, excluded=True), "excluded"
        )


class BuildSectionTest(unittest.TestCase):
    def setUp(self):
        self.targets = [
            {"slug": "a", "name": "A市", "system_type": "d1-law", "crawl_status": "enabled"},
            {"slug": "b", "name": "B市", "system_type": "taikei", "crawl_status": "enabled"},
            {"slug": "c", "name": "C町", "system_type": "taikei", "crawl_status": "enabled"},
            {"slug": "d", "name": "D村", "system_type": "独自", "crawl_status": "video_only"},
        ]

    def test_counts_by_reason(self):
        section = ledger.build_section(
            "reiki", self.targets, {"a"}, lambda slugs: {"b": 588, "c": 0, "d": 0}
        )
        self.assertEqual(section["targets"], 4)
        self.assertEqual(section["published"], 1)
        self.assertEqual(section["missing"], 3)
        self.assertEqual(
            section["reasons"], {"not_indexed": 1, "no_saved_files": 1, "excluded": 1}
        )

    def test_only_counts_files_for_unpublished(self):
        """全国を毎回歩くと 100 万件超のファイルを触る。"""
        asked = {}

        def count(slugs):
            asked["slugs"] = set(slugs)
            return {}

        ledger.build_section("reiki", self.targets, {"a"}, count)
        self.assertEqual(asked["slugs"], {"b", "c", "d"})

    def test_the_biggest_saved_comes_first_within_a_reason(self):
        targets = [
            {"slug": "x", "name": "X", "system_type": "", "crawl_status": "enabled"},
            {"slug": "y", "name": "Y", "system_type": "", "crawl_status": "enabled"},
        ]
        section = ledger.build_section("reiki", targets, set(), lambda slugs: {"x": 5, "y": 500})
        self.assertEqual([row["slug"] for row in section["missing_rows"]], ["y", "x"])


class WriteReadTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        original = batch_status.status_root
        batch_status.status_root = lambda: Path(self.temporary.name)
        self.addCleanup(lambda: setattr(batch_status, "status_root", original))

    def test_round_trip(self):
        ledger.write_ledger([{"doc_type": "reiki", "missing": 2}])
        loaded = ledger.read_ledger()
        self.assertEqual(loaded["version"], ledger.LEDGER_VERSION)
        self.assertEqual(loaded["sections"][0]["missing"], 2)

    def test_a_broken_file_reads_as_empty(self):
        ledger.ledger_path().write_text("{ broken", encoding="utf-8")
        self.assertEqual(ledger.read_ledger(), {})
