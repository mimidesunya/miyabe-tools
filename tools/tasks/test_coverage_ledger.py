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


class UnregisteredTest(unittest.TestCase):
    """取得元を登録できていない自治体を、分母から落とさない。

    台帳の対象は自治体マスタではなく取得先レジストリだった。URL が空の行は
    落ちるので、実測でマスタ 1,794 件に対し会議録は 282 件・例規は 47 件が
    分母の外にあった。**全国の 16% が「数えていないので健全」に見えていた。**
    codex の指摘。
    """

    def setUp(self):
        self.targets = [
            {"slug": "01100-sapporo-shi", "name": "札幌市", "system_type": "x", "crawl_status": "enabled"},
        ]

    def test_master_only_codes_become_missing(self):
        section = ledger.build_section(
            "minutes", self.targets, {"01100-sapporo-shi"}, lambda slugs: {},
            master_codes={"01100", "01101", "01102"},
        )
        self.assertEqual(section["targets"], 3)
        self.assertEqual(section["configured"], 1)
        self.assertEqual(section["missing"], 2)
        self.assertEqual(section["reasons"], {ledger.UNREGISTERED: 2})

    def test_without_master_codes_nothing_changes(self):
        section = ledger.build_section(
            "minutes", self.targets, {"01100-sapporo-shi"}, lambda slugs: {}
        )
        self.assertEqual(section["targets"], 1)
        self.assertEqual(section["missing"], 0)

    def test_a_configured_town_is_not_counted_twice(self):
        section = ledger.build_section(
            "minutes", self.targets, set(), lambda slugs: {"01100-sapporo-shi": 0},
            master_codes={"01100"},
        )
        self.assertEqual(section["missing"], 1)
        self.assertEqual(section["reasons"], {"no_saved_files": 1})


class DateMismatchTest(unittest.TestCase):
    """題名の月日と開催日が食い違う自治体を挙げる。

    件数だけを見ていると、空欄ではなく「もっともらしく誤る」形が見えない。
    若桜町は題名 `6月17日` に対し開催日が 6月16日（招集日）だった。公開
    件数は正しく、日付だけが 1 日ずれていた。
    """

    def test_finds_a_town_with_many_mismatches(self):
        found = ledger.date_mismatch_rows({"31325-wakasa-cho": (100, 40), "ok": (100, 2)})
        self.assertEqual([row["slug"] for row in found], ["31325-wakasa-cho"])
        self.assertEqual(found[0]["mismatched"], 40)

    def test_a_small_sample_is_not_judged(self):
        self.assertEqual(ledger.date_mismatch_rows({"a": (5, 5)}), [])

    def test_a_few_mismatches_are_tolerated(self):
        """会期をまたぐ会議など、正しくずれる文書もある。"""
        self.assertEqual(ledger.date_mismatch_rows({"a": (100, 5)}), [])

    def test_the_worst_ratio_comes_first(self):
        found = ledger.date_mismatch_rows({"a": (100, 20), "b": (100, 90)})
        self.assertEqual([row["slug"] for row in found], ["b", "a"])


class EmptyBodyTest(unittest.TestCase):
    """本文がほとんど空の自治体を挙げる。

    **件数では出てこない不具合。**公開はされていて、中身だけが無い。
    牛久市 1,001 件・福岡市 1,136 件は、題名と日付は読めるのに条文が 1 件も
    入っていなかった。
    """

    def test_finds_a_town_with_empty_bodies(self):
        found = ledger.empty_body_rows({"08219-ushiku-shi": 1001}, {"08219-ushiku-shi": 1001})
        self.assertEqual(found[0]["slug"], "08219-ushiku-shi")
        self.assertEqual(found[0]["ratio"], 1.0)

    def test_a_healthy_town_is_not_flagged(self):
        self.assertEqual(ledger.empty_body_rows({"a": 1000}, {"a": 80}), [])

    def test_a_tiny_town_is_not_judged(self):
        """短い例規しか無い村で誤検知しない。"""
        self.assertEqual(ledger.empty_body_rows({"a": 10}, {"a": 10}), [])

    def test_the_worst_ratio_comes_first(self):
        found = ledger.empty_body_rows({"a": 100, "b": 100}, {"a": 60, "b": 100})
        self.assertEqual([row["slug"] for row in found], ["b", "a"])


class StaleRowsTest(unittest.TestCase):
    """更新が止まっている自治体を、件数や本文とは別の軸で拾えること。"""

    def test_old_newest_is_reported(self):
        rows = ledger.stale_rows(
            {"04100-sendai-shi": "1991-06-19", "13104-shinjuku-ku": "2026-08-01"},
            today="2026-09-02",
        )
        self.assertEqual([row["slug"] for row in rows], ["04100-sendai-shi"])
        self.assertGreater(rows[0]["age_days"], 12000)

    def test_recent_is_not_reported(self):
        rows = ledger.stale_rows({"a": "2026-08-31"}, today="2026-09-02")
        self.assertEqual(rows, [])

    def test_unreadable_date_is_skipped_not_counted_as_stale(self):
        rows = ledger.stale_rows({"a": "", "b": "不明"}, today="2026-09-02")
        self.assertEqual(rows, [])

    def test_sorted_by_age(self):
        rows = ledger.stale_rows(
            {"a": "2000-01-01", "b": "1990-01-01"}, today="2026-09-02"
        )
        self.assertEqual([row["slug"] for row in rows], ["b", "a"])


class EmptyDateRowsTest(unittest.TestCase):
    """日付がほとんど読めていない自治体を、件数とは別の軸で拾えること。"""

    def test_mostly_undated_is_reported(self):
        rows = ledger.empty_date_rows({"a": 503}, {"a": 1})
        self.assertEqual(rows[0]["slug"], "a")
        self.assertEqual(rows[0]["no_date"], 502)

    def test_dated_is_not_reported(self):
        self.assertEqual(ledger.empty_date_rows({"a": 503}, {"a": 500}), [])

    def test_small_municipality_is_skipped(self):
        self.assertEqual(ledger.empty_date_rows({"a": 5}, {"a": 0}), [])


class StaleIgnoresUndatedTest(unittest.TestCase):
    """日付が読めていない自治体を、古さの軸に出さないこと。

    板柳町は 503 件中 502 件に公布日が無く、残る 1 件の 1961 年が
    最新として出ていた。取得が古いのではなく、日付が無いのである。
    """

    def test_undated_municipality_is_not_stale(self):
        rows = ledger.stale_rows(
            {"a": "1961-01-05"},
            today="2026-09-02",
            dated_by_slug={"a": 1},
            totals_by_slug={"a": 503},
        )
        self.assertEqual(rows, [])

    def test_dated_municipality_is_still_stale(self):
        rows = ledger.stale_rows(
            {"a": "1991-06-19"},
            today="2026-09-02",
            dated_by_slug={"a": 88},
            totals_by_slug={"a": 88},
        )
        self.assertEqual([row["slug"] for row in rows], ["a"])


class ShortBodyRowsTest(unittest.TestCase):
    """本文が仲間より極端に短い自治体を、空でも件数でもない軸で拾えること。"""

    def setUp(self):
        self.systems = {s: "gijiroku.com" for s in ("a", "b", "c", "d")}
        self.counts = {s: 1000 for s in ("a", "b", "c", "d")}

    def test_short_median_is_reported(self):
        rows = ledger.short_body_rows(
            {"a": 2855, "b": 23325, "c": 25306, "d": 21306}, self.systems, self.counts
        )
        self.assertEqual([row["slug"] for row in rows], ["a"])
        self.assertLess(rows[0]["ratio"], 0.2)

    def test_normal_median_is_not_reported(self):
        rows = ledger.short_body_rows(
            {"a": 20000, "b": 23325, "c": 25306, "d": 21306}, self.systems, self.counts
        )
        self.assertEqual(rows, [])

    def test_small_municipality_is_skipped(self):
        counts = {**self.counts, "a": 5}
        rows = ledger.short_body_rows(
            {"a": 100, "b": 23325, "c": 25306, "d": 21306}, self.systems, counts
        )
        self.assertEqual(rows, [])

    def test_system_with_too_few_peers_is_skipped(self):
        rows = ledger.short_body_rows(
            {"a": 100, "b": 23325}, {"a": "x", "b": "x"}, {"a": 1000, "b": 1000}
        )
        self.assertEqual(rows, [])


class SevereShortBodyTest(unittest.TestCase):
    """短さが分割で説明できるものと、できないものを分けること。

    松田町の「議案第58号」1,774 字は、議長の発言から始まる議事そのもので
    ある。議案ごとに分かれているだけで、欠けてはいない。宗像市の 443 字は
    「フレーム表示ができるブラウザが必要です」だけだった。
    """

    def test_finely_split_source_is_not_severe(self):
        rows = ledger.short_body_rows(
            {"a": 1774, "b": 25838, "c": 30000, "d": 22000},
            {s: "独自" for s in "abcd"},
            {s: 1000 for s in "abcd"},
        )
        self.assertEqual([row["slug"] for row in rows], ["a"])
        self.assertEqual(ledger.severe_short_body_rows(rows), [])

    def test_frame_only_source_is_severe(self):
        rows = ledger.short_body_rows(
            {"a": 443, "b": 37977, "c": 30000, "d": 32000},
            {s: "dbsr" for s in "abcd"},
            {s: 1000 for s in "abcd"},
        )
        self.assertEqual([row["slug"] for row in ledger.severe_short_body_rows(rows)], ["a"])


class IndexGapRowsTest(unittest.TestCase):
    """取得済みなのに公開へ出ていない自治体を、自分の保存データと比べて拾えること。

    各務原市は 3,220 件を保存して 5 件しか公開していなかった。0 件では
    ないので `sweep_never_indexed` からは見えなかった。
    """

    def test_partially_indexed_is_reported(self):
        rows = ledger.index_gap_rows({"a": 3220}, {"a": 5})
        self.assertEqual(rows[0]["gap"], 3215)

    def test_fully_indexed_is_not_reported(self):
        self.assertEqual(ledger.index_gap_rows({"a": 3220}, {"a": 3220}), [])

    def test_small_difference_is_not_reported(self):
        self.assertEqual(ledger.index_gap_rows({"a": 100}, {"a": 90}), [])

    def test_small_municipality_is_skipped(self):
        self.assertEqual(ledger.index_gap_rows({"a": 10}, {"a": 0}), [])

    def test_sorted_by_gap(self):
        rows = ledger.index_gap_rows({"a": 100, "b": 3220}, {"a": 0, "b": 5})
        self.assertEqual([row["slug"] for row in rows], ["b", "a"])
