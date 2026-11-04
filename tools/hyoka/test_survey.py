"""事務事業評価の実地調査の束の試験。Codex は呼ばない。"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "survey"))

import run_survey


def census_row(**values: str) -> dict[str, str]:
    row = {"mic_introduction_r7": "導入済み", "mic_publication_r7": "全て公表", "ledger_status": "unresolved"}
    row.update(values)
    return row


def good_result(code: str = "14130") -> dict:
    return {
        "jis_code": code, "name": "神奈川県川崎市", "conducts": "yes", "publishes": "target_all", "verdict": "project_sheets",
        "entry_url": "https://www.city.kawasaki.jp/170/page/0000188786.html",
        "year_pages": [{"fiscal_year": "令和7年度", "url": "https://www.city.kawasaki.jp/170/page/0000188786.html", "documents": 23, "note": ""}],
        "forms": [{"years": "令和7年度", "type": "project_sheets", "fields": {}}],
        "other_sources": [],
        "samples": [{"url": "https://www.city.kawasaki.jp/a.pdf", "fiscal_year": "令和7年度", "business_name": "事業", "what_i_saw": "事業費の欄"}],
        "confidence": "high",
    }


class PriorityTest(unittest.TestCase):
    def test_published_first(self) -> None:
        self.assertEqual(run_survey.priority(census_row()), 0)
        self.assertEqual(run_survey.priority(census_row(mic_publication_r7="一部公表")), 0)

    def test_collected_before_unpublished(self) -> None:
        collected = census_row(mic_introduction_r7="廃止", mic_publication_r7="", ledger_status="enabled")
        unpublished = census_row(mic_publication_r7="公表していない")
        self.assertLess(run_survey.priority(collected), run_survey.priority(unpublished))

    def test_no_plan_last(self) -> None:
        self.assertEqual(run_survey.priority(census_row(mic_introduction_r7="導入予定なし", mic_publication_r7="")), 5)


class TargetsTest(unittest.TestCase):
    def test_all_real_municipalities_and_no_northern_villages(self) -> None:
        targets = run_survey.build_targets()
        codes = [row["jis_code"] for row in targets]
        self.assertEqual(len(codes), 1788)
        self.assertEqual(len(set(codes)), 1788)
        self.assertNotIn("01695", codes)
        self.assertIn("13101", codes)
        self.assertIn("41206", codes)
        self.assertTrue(all(row["homepage"] for row in targets[:50]))


class PlanTest(unittest.TestCase):
    def test_continues_after_planned_batches(self) -> None:
        def target(code: str) -> dict[str, str]:
            return {column: code for column in run_survey.TARGET_COLUMNS}

        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            first = work / "batches" / "0001"
            first.mkdir(parents=True)
            run_survey.write_tsv(first / "all_targets.tsv", [target("01000"), target("02000")])
            with mock.patch.object(run_survey, "build_targets", lambda: [target(code) for code in ("01000", "02000", "03000", "04000")]):
                run_survey.cmd_plan(argparse.Namespace(work=temporary, batch=1, codes="", previous=""))
            self.assertEqual([folder.name for folder in run_survey.batch_dirs(work)], ["0001", "0002", "0003"])
            self.assertEqual(run_survey.read_tsv(work / "batches" / "0003" / "all_targets.tsv")[0]["jis_code"], "04000")

    def test_recheck_takes_chosen_codes_with_previous_result(self) -> None:
        def target(code: str) -> dict[str, str]:
            return {column: code for column in run_survey.TARGET_COLUMNS}

        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            (work / "codes.txt").write_text("02000\n", encoding="utf-8")
            (work / "survey.jsonl").write_text(json.dumps({"jis_code": "02000", "verdict": "not_found"}) + "\n", encoding="utf-8")
            with mock.patch.object(run_survey, "build_targets", lambda: [target("01000"), target("02000")]):
                run_survey.cmd_plan(argparse.Namespace(work=temporary, batch=1, codes=str(work / "codes.txt"), previous=str(work / "survey.jsonl")))
            folders = run_survey.batch_dirs(work)
            self.assertEqual([row["jis_code"] for row in run_survey.read_tsv(folders[0] / "all_targets.tsv")], ["02000"])
            self.assertEqual(len(folders), 1)
            self.assertIn("not_found", (folders[0] / "previous.jsonl").read_text(encoding="utf-8"))


class ProblemsTest(unittest.TestCase):
    def test_good(self) -> None:
        self.assertEqual(run_survey.problems(good_result(), {"14130"}), [])

    def test_missing_field(self) -> None:
        row = good_result()
        del row["verdict"]
        self.assertEqual(run_survey.problems(row, {"14130"}), ["verdict がない"])

    def test_other_municipality(self) -> None:
        self.assertIn("対象でない団体", run_survey.problems(good_result("11100"), {"14130"}))

    def test_bad_value(self) -> None:
        row = good_result()
        row["verdict"] = "yes"
        self.assertEqual(run_survey.problems(row, {"14130"}), ["verdict: yes"])

    def test_sheets_need_samples(self) -> None:
        row = good_result()
        row["samples"] = []
        self.assertEqual(run_survey.problems(row, {"14130"}), ["事業の情報ありなのに samples が空"])

    def test_table_needs_year_pages(self) -> None:
        row = good_result()
        row.update({"verdict": "project_table", "year_pages": []})
        self.assertEqual(run_survey.problems(row, {"14130"}), ["事業の情報ありなのに year_pages が空"])

    def test_not_found_needs_no_samples(self) -> None:
        row = good_result()
        row.update({"verdict": "not_found", "samples": [], "entry_url": "", "year_pages": [], "forms": []})
        self.assertEqual(run_survey.problems(row, {"14130"}), [])


class ResultsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder_holder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder_holder.cleanup)
        self.folder = Path(self.folder_holder.name)
        rows = [dict.fromkeys(run_survey.TARGET_COLUMNS, "") for _ in range(2)]
        rows[0]["jis_code"] = "14130"
        rows[1]["jis_code"] = "11100"
        run_survey.write_tsv(self.folder / "all_targets.tsv", rows)

    def test_nothing_yet(self) -> None:
        self.assertEqual(len(run_survey.remaining(self.folder)), 2)

    def test_bad_lines_stay_remaining(self) -> None:
        bad = good_result("11100")
        bad["verdict"] = "yes"
        lines = [json.dumps(good_result(), ensure_ascii=False), json.dumps(bad, ensure_ascii=False), "{壊れた行"]
        (self.folder / "results.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        good, problems = run_survey.read_results(self.folder)
        self.assertEqual(list(good), ["14130"])
        self.assertEqual(len(problems), 2)
        self.assertEqual([row["jis_code"] for row in run_survey.remaining(self.folder)], ["11100"])

    def test_tokens(self) -> None:
        (self.folder / "codex.log").write_text("x\ntokens used\n12,345\n\ntokens used\n5\n", encoding="utf-8")
        self.assertEqual(run_survey.tokens_used(self.folder), 12350)


class RunTest(unittest.TestCase):
    def run_with(self, codes: list[int], quota: float = 10.0) -> list[str]:
        """束ごとの終了コードを順に返す偽の Codex で流し、始めた束の名前を返す。"""
        started: list[str] = []
        results = iter(codes)

        def fake_batch(folder: Path, codex: str, effort: str, model: str, preface: str) -> int:
            started.append(folder.name)
            return next(results)

        with tempfile.TemporaryDirectory() as temporary:
            for number in range(1, len(codes) + 1):
                folder = Path(temporary) / "batches" / f"{number:04d}"
                folder.mkdir(parents=True)
                run_survey.write_tsv(folder / "all_targets.tsv", [{column: str(number) for column in run_survey.TARGET_COLUMNS}])
            args = argparse.Namespace(
                work=temporary, codex="codex", limit=0, parallel=1, max_quota=90.0, max_failures=3, effort="low", model="", preface=""
            )
            limits = {"ordinaryUsageAllowed": True, "rateLimits": {"primary": {"usedPercent": quota}}}
            with mock.patch.object(run_survey, "run_batch", fake_batch), mock.patch.object(run_survey, "account_limits", lambda codex: limits):
                try:
                    run_survey.cmd_run(args)
                except SystemExit:
                    pass
        return started

    def test_single_failure_does_not_stop(self) -> None:
        self.assertEqual(len(self.run_with([1, 0, 1, 0, 0])), 5)

    def test_repeated_failures_stop(self) -> None:
        self.assertEqual(self.run_with([1, 1, 1, 0, 0]), ["0001", "0002", "0003"])

    def test_quota_stops_before_starting(self) -> None:
        self.assertEqual(self.run_with([0, 0], quota=95.0), [])


class UrlTest(unittest.TestCase):
    def test_japanese_path_is_encoded(self) -> None:
        self.assertEqual(run_survey.ascii_url("https://a.jp/R2事後/01.pdf?x=1"), "https://a.jp/R2%E4%BA%8B%E5%BE%8C/01.pdf?x=1")

    def test_encoded_url_is_unchanged(self) -> None:
        url = "https://a.jp/%E4%BA%8B.pdf#page=2"
        self.assertEqual(run_survey.ascii_url(url), url)


class VerifyTest(unittest.TestCase):
    def test_urls_are_grouped_by_host(self) -> None:
        row = good_result()
        with mock.patch.object(run_survey, "batch_dirs", return_value=[Path("a")]):
            with mock.patch.object(run_survey, "read_results", return_value=({row["jis_code"]: row}, [])):
                hosts = run_survey.urls_by_host(Path("work"))
        self.assertEqual(list(hosts), ["www.city.kawasaki.jp"])
        self.assertEqual([item[1] for item in hosts["www.city.kawasaki.jp"]], ["entry", "year", "sample"])

    def test_host_rows_keep_status(self) -> None:
        with mock.patch.object(run_survey, "fetch_status", return_value="404"):
            rows = run_survey.check_host([("14130", "entry", "https://a.jp/")], 0)
        self.assertEqual(rows, [["14130", "entry", "404", "https://a.jp/"]])


class QuotaTest(unittest.TestCase):
    @staticmethod
    def limits(used: float, allowed: bool | None = True) -> dict:
        return {"ordinaryUsageAllowed": allowed, "rateLimits": {"primary": {"usedPercent": used}}}

    def test_percent(self) -> None:
        self.assertEqual(run_survey.quota_percent(self.limits(42)), 42.0)

    def test_blocked_counts_as_full(self) -> None:
        self.assertEqual(run_survey.quota_percent(self.limits(12, allowed=False)), 100.0)

    def test_unknown_permission_uses_percent(self) -> None:
        self.assertEqual(run_survey.quota_percent(self.limits(12, allowed=None)), 12.0)

    def test_no_record(self) -> None:
        self.assertIsNone(run_survey.quota_percent(None))
        self.assertIsNone(run_survey.quota_percent({"rateLimits": {}}))

    def test_reply_skips_other_messages(self) -> None:
        lines = ["壊れた行", json.dumps({"method": "note"}), json.dumps({"id": 0, "result": {}}), json.dumps({"id": 1, "result": {"a": 1}})]
        self.assertEqual(run_survey.read_reply(iter(lines), 1), {"a": 1})

    def test_reply_missing(self) -> None:
        self.assertIsNone(run_survey.read_reply(iter(["{}"]), 1))


if __name__ == "__main__":
    unittest.main()
