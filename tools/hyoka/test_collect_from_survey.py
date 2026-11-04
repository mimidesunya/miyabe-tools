"""調査の台帳からの収集の試験。取得元には接続しない。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import collect_from_survey as c  # noqa: E402
import scrape_hyoka_docs as scrape  # noqa: E402


class FakeFetcher:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.opened: list[str] = []

    def html(self, url: str) -> tuple[str, str] | None:
        self.opened.append(url)
        return (url, self.pages[url]) if url in self.pages else None


def target(**values) -> c.SurveyTarget:
    fields = {
        "code": "14130", "slug": "14130-kawasaki-shi", "name": "川崎市", "url": "https://www.city.a.jp/top.html", "crawl_status": "survey",
        "year_pages": [{"fiscal_year": "令和7年度", "url": "https://www.city.a.jp/r7.html", "documents": 2}],
    }
    fields.update(values)
    return c.SurveyTarget(**fields)


def collect(pages: dict[str, str], item: c.SurveyTarget, max_depth: int = 2) -> tuple[list[scrape.Candidate], scrape.RunResult, FakeFetcher]:
    fetcher = FakeFetcher(pages)
    result = scrape.RunResult(slug=item.slug)
    found = c.collect_from_survey(fetcher, item, max_pages=50, max_depth=max_depth, result=result)
    return found, result, fetcher


class CollectTest(unittest.TestCase):
    def test_takes_every_file_without_choosing_by_words(self) -> None:
        pages = {
            "https://www.city.a.jp/top.html": "<title>行政評価</title>",
            "https://www.city.a.jp/r7.html": '<h1>令和7年度</h1><a href="a.pdf">森林環境税</a><a href="b.pdf">事業シート</a>',
        }
        found, _result, _fetcher = collect(pages, target())
        self.assertEqual({item.url.rsplit("/", 1)[1] for item in found}, {"a.pdf", "b.pdf"})
        self.assertEqual({item.origin for item in found}, {"listed"})
        self.assertEqual({item.path_year for item in found}, {2025})

    def test_year_page_that_is_a_file_is_taken_directly(self) -> None:
        item = target(year_pages=[{"fiscal_year": "平成30年度", "url": "https://www.city.a.jp/h30.pdf", "documents": 1}])
        found, _result, fetcher = collect({"https://www.city.a.jp/top.html": "<title>x</title>"}, item)
        self.assertEqual([entry.url for entry in found], ["https://www.city.a.jp/h30.pdf"])
        self.assertEqual(found[0].path_year, 2018)
        self.assertEqual(fetcher.opened, ["https://www.city.a.jp/top.html"])

    def test_follows_department_pages_within_depth(self) -> None:
        pages = {
            "https://www.city.a.jp/top.html": "<title>x</title>",
            "https://www.city.a.jp/r7.html": '<h1>令和7年度</h1><h2>事業シート</h2><a href="soumu.html">事務事業評価シート（総務局）</a>',
            "https://www.city.a.jp/soumu.html": '<h1>総務局</h1><a href="c.pdf">シート</a><a href="more.html">事務事業評価シート（続き）</a>',
            "https://www.city.a.jp/more.html": '<h1>続き</h1><a href="d.pdf">シート</a>',
        }
        found, _result, fetcher = collect(pages, target(), max_depth=1)
        self.assertEqual([item.url.rsplit("/", 1)[1] for item in found], ["c.pdf"])
        self.assertEqual(found[0].origin, "followed")
        self.assertNotIn("https://www.city.a.jp/more.html", fetcher.opened)

    def test_general_page_does_not_lead_to_department_pages(self) -> None:
        pages = {
            "https://www.city.a.jp/top.html": "<title>x</title>",
            "https://www.city.a.jp/r7.html": '<h1>決算</h1><a href="soumu.html">総務課</a><a href="a.pdf">主要施策成果報告書</a>',
            "https://www.city.a.jp/soumu.html": '<h1>総務課</h1><a href="aed.pdf">AED設置個所</a>',
        }
        found, _result, fetcher = collect(pages, target())
        self.assertEqual([item.url.rsplit("/", 1)[1] for item in found], ["a.pdf"])
        self.assertNotIn("https://www.city.a.jp/soumu.html", fetcher.opened)

    def test_other_site_files_are_counted_not_taken(self) -> None:
        pages = {
            "https://www.city.a.jp/top.html": "<title>x</title>",
            "https://www.city.a.jp/r7.html": '<h1>令和7年度</h1><a href="https://example.com/x.pdf">外部</a>',
        }
        found, result, _fetcher = collect(pages, target())
        self.assertEqual(found, [])
        self.assertEqual(result.offsite_files, 1)

    def test_sample_page_files_are_taken_without_going_further(self) -> None:
        pages = {
            "https://www.city.a.jp/top.html": "<title>x</title>",
            "https://www.city.a.jp/r7.html": "<h1>令和7年度</h1>",
            "https://www.city.a.jp/sheet.html": '<h1>事業</h1><a href="s.pdf">シート</a><a href="next.html">事務事業評価シート</a>',
        }
        item = target(samples=[{"fiscal_year": "令和6年度", "url": "https://www.city.a.jp/sheet.html"}])
        found, _result, fetcher = collect(pages, item)
        self.assertEqual([entry.url.rsplit("/", 1)[1] for entry in found], ["sheet.html", "s.pdf"])
        self.assertEqual(found[1].path_year, 2024)
        self.assertNotIn("https://www.city.a.jp/next.html", fetcher.opened)


class HtmlSheetTest(unittest.TestCase):
    TREE = "https://www.city.a.jp/section/hyoka/r6/2024/npm/0/T1.html"
    SHEET = "https://www.city.a.jp/section/hyoka/r6/2024/sheet/proj/100.html"

    def pages(self) -> dict[str, str]:
        return {
            "https://www.city.a.jp/top.html": "<title>行政評価</title>",
            self.TREE: '<h1>令和6年度</h1><a href="T1/1.html">分野</a><a href="/other/x.html">市の案内</a>',
            "https://www.city.a.jp/section/hyoka/r6/2024/npm/0/T1/1.html": '<a href="../../../sheet/proj/100.html">総合事業審査支払事業</a>',
            self.SHEET: "<h1>総合事業審査支払事業</h1>",
            "https://www.city.a.jp/other/x.html": "<h1>市の案内</h1>",
        }

    def test_walks_to_sheets_inside_the_evaluation_folder(self) -> None:
        item = target(url=self.TREE, html_sheets=True, year_pages=[{"fiscal_year": "令和6年度", "url": self.TREE}])
        found, _result, fetcher = collect(self.pages(), item)
        sheets = {entry.url: entry for entry in found}
        self.assertIn(self.SHEET, sheets)
        self.assertEqual(sheets[self.SHEET].file_type, "html")
        self.assertEqual(sheets[self.SHEET].origin, "followed")
        self.assertEqual(sheets[self.SHEET].path_year, 2024)
        self.assertNotIn("https://www.city.a.jp/other/x.html", fetcher.opened)

    def test_shallow_site_is_not_walked(self) -> None:
        self.assertEqual(c.walk_prefix("https://www.city.a.jp/0000001586.html"), "")
        self.assertEqual(c.walk_prefix("https://www.city.a.jp/a/b/c.html"), "")
        self.assertEqual(c.walk_prefix(self.TREE), "/section/hyoka/r6/2024/")

    def test_page_is_not_a_candidate_unless_the_town_publishes_html(self) -> None:
        pages = {
            "https://www.city.a.jp/top.html": "<title>x</title>",
            "https://www.city.a.jp/r7.html": '<h1>令和7年度</h1><a href="a.pdf">事業シート</a>',
        }
        found, _result, _fetcher = collect(pages, target())
        self.assertEqual([entry.file_type for entry in found], ["pdf"])

    def test_sample_page_is_a_candidate(self) -> None:
        pages = {
            "https://www.city.a.jp/top.html": "<title>x</title>",
            "https://www.city.a.jp/r7.html": "<h1>令和7年度</h1>",
            self.SHEET: "<h1>総合事業審査支払事業</h1>",
        }
        found, _result, _fetcher = collect(pages, target(samples=[{"fiscal_year": "令和6年度", "url": self.SHEET}]))
        self.assertEqual([(entry.url, entry.file_type) for entry in found], [(self.SHEET, "html")])

    def test_evaluation_table_page_is_a_candidate(self) -> None:
        table = "<table><tr><th>事業名</th><th>事業費</th><th>評価</th><th>今後の方向</th></tr><tr><td>道路</td><td>10</td><td>A</td><td>継続</td></tr></table>"
        pages = {"https://www.city.a.jp/top.html": "<title>x</title>", "https://www.city.a.jp/r7.html": f"<h1>令和7年度</h1>{table}"}
        found, _result, _fetcher = collect(pages, target())
        self.assertEqual([(entry.url, entry.file_type) for entry in found], [("https://www.city.a.jp/r7.html", "html")])


class HtmlTextTest(unittest.TestCase):
    def test_table_rows_stay_apart(self) -> None:
        raw = "<html><style>x{}</style><body><nav>案内</nav><table><tr><th>事業費</th><td>1,234千円</td></tr><tr><td>評価</td><td>A&nbsp;</td></tr></table></body></html>".encode("utf-8")
        self.assertEqual(scrape.extract_html_text(raw), "事業費 | 1,234千円\n評価 | A")

    def test_old_page_in_shift_jis(self) -> None:
        raw = '<meta charset="Shift_JIS"><p>事務事業評価</p>'.encode("cp932")
        self.assertEqual(scrape.extract_html_text(raw), "事務事業評価")

    def test_shift_jis_without_declaration(self) -> None:
        self.assertEqual(scrape.decode_html("事務事業評価".encode("cp932")), "事務事業評価")

    def test_page_without_body_is_rejected(self) -> None:
        response = mock.Mock(headers={"Content-Type": "text/html"}, url="https://a.jp/x.html")
        with self.assertRaises(ValueError):
            scrape.check_html_response(response, b"<html><body><p>x</p></body></html>")

    def test_sheet_page_is_accepted(self) -> None:
        response = mock.Mock(headers={"Content-Type": "text/html"}, url="https://a.jp/x.html")
        body = "<html><title>事業</title><body>" + "<p>事業の目的と成果</p>" * 40 + "</body></html>"
        scrape.check_html_response(response, body.encode("utf-8"))

    def test_sheet_titled_with_other_scheme_words_is_accepted(self) -> None:
        response = mock.Mock(headers={"Content-Type": "text/html"}, url="https://a.jp/x.html")
        body = "<html><title>事務事業 「介護保険利用料助成等事業」</title><body>" + "<p>事業の目的と成果</p>" * 40 + "</body></html>"
        scrape.check_html_response(response, body.encode("utf-8"))

    def test_not_found_page_is_rejected(self) -> None:
        response = mock.Mock(headers={"Content-Type": "text/html"}, url="https://a.jp/x.html")
        body = "<html><title>お探しのページは見つかりませんでした</title><body>" + "<p>トップへ戻る</p>" * 60 + "</body></html>"
        with self.assertRaises(ValueError):
            scrape.check_html_response(response, body.encode("utf-8"))


class SelectedTest(unittest.TestCase):
    def test_only_chosen_documents_are_taken(self) -> None:
        item = target()

        def candidate(name: str) -> scrape.Candidate:
            return scrape.Candidate(
                url=f"https://www.city.a.jp/{name}", label=name, page_url="", heading="", page_title="", file_type="pdf",
                kind="project_evaluation", origin="listed",
            )

        with tempfile.TemporaryDirectory() as temporary:
            lists = Path(temporary)
            c.write_candidates(lists / f"{item.slug}.jsonl", [candidate("a.pdf"), candidate("b.pdf"), candidate("c.pdf")])
            roles = {"https://www.city.a.jp/a.pdf": "sheet", "https://www.city.a.jp/b.pdf": "other"}
            result = scrape.RunResult(slug=item.slug)
            found = c.selected_collector(lists, {item.code: {"roles": roles}})(None, item, max_pages=1, max_depth=1, result=result)
        self.assertEqual([entry.url.rsplit("/", 1)[1] for entry in found], ["a.pdf"])
        self.assertEqual(found[0].origin, "listed:sheet")
        self.assertTrue(result.entry_opened)


class ShardTest(unittest.TestCase):
    def test_every_group_once(self) -> None:
        groups = [[target(code=f"{number:05d}", slug=str(number))] for number in range(7)]
        shards = [c.shard_groups(groups, f"{index}/3") for index in range(3)]
        codes = sorted(group[0].code for shard in shards for group in shard)
        self.assertEqual(codes, [f"{number:05d}" for number in range(7)])
        self.assertEqual([len(shard) for shard in shards], [3, 2, 2])


class LoadSurveyTest(unittest.TestCase):
    def test_levels_choose_targets(self) -> None:
        row = {"entry_url": "https://a.jp/", "year_pages": [], "samples": [], "forms": [{"file_format": "HTML"}], "verdict": "project_sheets"}
        rows = [{"jis_code": code, "name": code, **row} for code in ("01000", "02000", "03000")]
        labels = [
            {"jis_code": "01000", "source_kind": "evaluation_system", "project_evaluation": "yes"},
            {"jis_code": "02000", "source_kind": "plan_review", "project_evaluation": "partial"},
            {"jis_code": "03000", "source_kind": "reform_plan", "project_evaluation": "no"},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            survey = Path(temporary) / "survey.jsonl"
            label_path = Path(temporary) / "labels.jsonl"
            survey.write_text("\n".join(json.dumps(item) for item in rows), encoding="utf-8")
            label_path.write_text("\n".join(json.dumps(item) for item in labels), encoding="utf-8")
            with mock.patch.object(c, "load_master", lambda: {}):
                targets = c.load_survey(survey, label_path)
        self.assertEqual([item.code for item in targets], ["01000", "02000"])
        self.assertEqual(targets[1].survey["source_kind"], "plan_review")
        self.assertTrue(targets[0].html_sheets)


class ReuseTest(unittest.TestCase):
    def test_copies_saved_file_and_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            old = Path(temporary) / "old"
            (old / "a" / "source").mkdir(parents=True)
            (old / "a" / "source" / "d.pdf").write_bytes(b"pdf")
            manifest = {"documents": {"d": {"file": "source/d.pdf", "sha256": "abc", "checked_at": "2026-10-28"}}}
            (old / "a" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            work = Path(temporary) / "new" / "a"
            entry: dict = {}
            c.PreviousCollection(old)("d", entry, work)
            self.assertEqual((work / "source" / "d.pdf").read_bytes(), b"pdf")
            self.assertEqual(entry["sha256"], "abc")

    def test_unknown_document_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            entry: dict = {}
            c.PreviousCollection(Path(temporary))("d", entry, Path(temporary) / "new" / "a")
            self.assertEqual(entry, {})


if __name__ == "__main__":
    unittest.main()
