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


class ThinSlugsTest(unittest.TestCase):
    """0 件でなくても取りこぼしは起きる。

    富士市は 1,666 件あるところを 14 件しか公開していなかった。台帳は
    「0 件かどうか」しか見ていないので健全に見えていた。
    """

    def test_finds_a_town_far_below_its_peers(self):
        counts = {f"peer{i}": 800 for i in range(10)}
        counts["22210-fuji-shi"] = 14
        systems = {slug: "gijiroku.com" for slug in counts}
        found = ledger.thin_slugs(counts, systems)
        self.assertEqual([row["slug"] for row in found], ["22210-fuji-shi"])
        self.assertEqual(found[0]["peer_median"], 800)

    def test_a_small_system_is_not_judged(self):
        """仲間が少ないと中央値が当てにならない。"""
        counts = {"a": 1000, "b": 5}
        systems = {"a": "rare", "b": "rare"}
        self.assertEqual(ledger.thin_slugs(counts, systems), [])

    def test_a_town_within_range_is_not_flagged(self):
        counts = {f"peer{i}": 800 for i in range(10)}
        counts["ok"] = 400
        systems = {slug: "taikei" for slug in counts}
        self.assertEqual(ledger.thin_slugs(counts, systems), [])

    def test_zero_is_left_to_the_missing_rows(self):
        """0 件は原因まで分けて別に数えている。ここでは重ねない。"""
        counts = {f"peer{i}": 800 for i in range(10)}
        counts["zero"] = 0
        systems = {slug: "taikei" for slug in counts}
        self.assertEqual(ledger.thin_slugs(counts, systems), [])


class MeasurementStatusTest(unittest.TestCase):
    """数え切れなかったことを、異常 0 件と読ませない。

    台帳は照会に失敗した区分を黙って飛ばし、それでも `ok: True` を返して
    いた。**今日直した不具合と同じ形が、監視の側へ移っていた。**
    codex と grok の両方が最優先の指摘として挙げた。
    """

    def test_both_sections_measured(self):
        sections = [{"doc_type": "minutes"}, {"doc_type": "reiki"}]
        self.assertEqual(ledger.measurement_status(sections), ledger.MEASUREMENT_COMPLETE)

    def test_one_section_missing_is_partial(self):
        self.assertEqual(
            ledger.measurement_status([{"doc_type": "reiki"}]), ledger.MEASUREMENT_PARTIAL
        )

    def test_nothing_measured_is_failed(self):
        self.assertEqual(ledger.measurement_status([]), ledger.MEASUREMENT_FAILED)

    def test_an_error_inside_a_section_is_partial(self):
        sections = [
            {"doc_type": "minutes", "errors": ["件数の偏りを見られませんでした"]},
            {"doc_type": "reiki"},
        ]
        self.assertEqual(ledger.measurement_status(sections), ledger.MEASUREMENT_PARTIAL)

    def test_the_status_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = batch_status.status_root
            batch_status.status_root = lambda: Path(tmp)
            try:
                ledger.write_ledger([{"doc_type": "reiki"}])
                loaded = ledger.read_ledger()
            finally:
                batch_status.status_root = original
        self.assertEqual(loaded["measurement_status"], ledger.MEASUREMENT_PARTIAL)
        self.assertEqual(loaded["measured_doc_types"], ["reiki"])


class DeclaredShortfallTest(unittest.TestCase):
    """取得元の申告母数と比べる。**これが本来の指標。**

    富士市は一覧が 1,761 件と申告しているのに 14 件しか公開していなかった。
    仲間の中央値ではなく、取得元が出している数と比べれば一発で分かる。
    codex と grok が揃って指摘した。
    """

    def test_finds_a_town_far_below_its_own_source(self):
        found = ledger.declared_shortfall({"22210-fuji-shi": 1761}, {"22210-fuji-shi": 14})
        self.assertEqual(found[0]["slug"], "22210-fuji-shi")
        self.assertEqual(found[0]["declared"], 1761)
        self.assertEqual(found[0]["published"], 14)

    def test_a_town_within_range_is_not_flagged(self):
        """索引は会議録でないものを落とすので、申告どおりにはならない。"""
        self.assertEqual(ledger.declared_shortfall({"a": 1000}, {"a": 800}), [])

    def test_no_declared_total_is_not_judged(self):
        """母数を出さない取得元は、ここでは判定しない。"""
        self.assertEqual(ledger.declared_shortfall({"a": 0}, {"a": 5}), [])

    def test_the_worst_ratio_comes_first(self):
        found = ledger.declared_shortfall(
            {"a": 1000, "b": 1000}, {"a": 400, "b": 10}
        )
        self.assertEqual([row["slug"] for row in found], ["b", "a"])

    def test_a_town_with_nothing_published_is_included(self):
        found = ledger.declared_shortfall({"a": 500}, {})
        self.assertEqual(found[0]["published"], 0)
