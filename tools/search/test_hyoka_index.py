"""hyoka の保存物・監査・世代更新を、OpenSearch に接続せず確かめる。"""

import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_opensearch_index as indexer
import hyoka_source_records as records

SLUG = "14100-yokohama"
DOC_ID = "0123456789abcdef"


class FakeClient:
    def __init__(self):
        self.aliases = {
            "minutes-current": {"minutes-old": {"filter": {"terms": {"slug": ["other"]}}}},
            "reiki-current": {"reiki-old": {}},
            "hyoka-current": {"hyoka-old": {}},
            "documents-current": {"minutes-old": {}, "reiki-old": {}, "hyoka-old": {}},
        }
        self.calls = []
        self.batches = []
        self.public_slugs = [SLUG]

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if method == "GET" and path.startswith("/_alias/"):
            alias = path[len("/_alias/"):]
            return {index: {"aliases": {alias: info}} for index, info in self.aliases.get(alias, {}).items()}
        if path.endswith("/_search"):
            return {"aggregations": {"slugs": {"buckets": [{"key": slug} for slug in self.public_slugs]}}}
        if path == "/_aliases":
            for action in kwargs["body"]["actions"]:
                if "remove" in action:
                    value = action["remove"]
                    self.aliases.get(value["alias"], {}).pop(value["index"], None)
                if "add" in action:
                    value = action["add"]
                    self.aliases.setdefault(value["alias"], {})[value["index"]] = {
                        key: item for key, item in value.items() if key not in {"index", "alias"}
                    }
        return {}

    def bulk_lines(self, lines, count):
        self.batches.append((lines, count))
        return {"errors": False}


class HyokaIndexTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / SLUG
        (self.work / "text").mkdir(parents=True)
        self.entry = {
            "title": "令和7年度 評価表", "year_label": "令和7年度", "fiscal_year": 2025,
            "kind": "project_evaluation", "source_url": "https://example.jp/report.pdf?download=1#old",
            "page_url": "https://example.jp/hyoka", "link_text": "評価表PDF", "heading": "行政評価",
            "sha256": "a" * 64, "fetched_at": "2026-10-20T10:00:00+09:00",
            "text_status": "ok", "status": "ok", "file_type": "pdf", "pages": 3,
            "text_file": f"text/{DOC_ID}.txt.gz", "file": f"source/{DOC_ID}.pdf",
            "previous_versions": [{"text_file": "_archive/old/body.txt.gz"}],
        }
        self.manifest = {"slug": SLUG, "jis_code": "14100", "name": "横浜市", "documents": {DOC_ID: self.entry}}
        self.write("第一頁\f\f第三頁\f")
        indexer.reset_source_integrity_tracking()
        self.addCleanup(indexer.reset_source_integrity_tracking)

    def write(self, body=None):
        if body is not None:
            header = "\n".join(f"{label}: {self.entry.get(key, '')}" for label, key in (
                ("題名", "title"), ("年度", "year_label"), ("種別", "kind"), ("出典", "source_url"), ("掲載ページ", "page_url")
            ))
            (self.work / "text" / f"{DOC_ID}.txt.gz").write_bytes(gzip.compress((header + "\n\n" + body + "\n").encode("utf-8")))
        (self.work / "manifest.json").write_text(json.dumps(self.manifest, ensure_ascii=False), encoding="utf-8")

    def documents(self, **kwargs):
        return list(indexer.iter_hyoka_documents(root=self.root, **kwargs))

    def run_main(self, client, *args):
        parsed = indexer.parse_args([
            "--doc-type", "hyoka", "--hyoka-root", str(self.root), "--build-id", "test",
            "--minutes-alias", "minutes-current", "--reiki-alias", "reiki-current",
            "--hyoka-alias", "hyoka-current", "--documents-alias", "documents-current", *args,
        ])
        with (
            mock.patch.object(indexer, "parse_args", return_value=parsed),
            mock.patch.object(indexer, "OpenSearchClient", return_value=client),
            mock.patch.object(indexer, "search_rebuild_status_start", return_value=None),
            mock.patch.object(indexer, "search_rebuild_status_progress"),
            mock.patch.object(indexer, "search_rebuild_status_slug_published"),
            mock.patch.object(indexer, "search_rebuild_status_finish"),
        ):
            return indexer.main()

    def test_physical_pages_keep_blank_page_number_and_provenance(self):
        docs = self.documents()
        self.assertEqual([row[1]["page"] for row in docs], [1, 3])
        first = docs[0][1]
        self.assertEqual(docs[0][0], f"hyoka:{SLUG}:{DOC_ID}:p1")
        self.assertEqual(first["body"], "第一頁")
        self.assertEqual(first["source_url"], self.entry["source_url"])
        self.assertEqual(first["source_page_link"], "https://example.jp/report.pdf?download=1#page=1")
        self.assertEqual(first["source_page_url"], self.entry["page_url"])
        self.assertEqual(first["sha256"], self.entry["sha256"])
        self.assertEqual(first["fetched_at"], self.entry["fetched_at"])
        self.assertEqual(first["municipality_code"], "14100")
        self.assertEqual(first["pref_name"], "神奈川県")
        self.assertNotIn("sort_date", first)
        self.assertEqual(indexer.source_integrity_summary("hyoka", SLUG)["yielded"], 1)

    def test_old_extractor_without_boundaries_stays_one_document(self):
        self.write("第一頁\n\n段落か次頁か分からない\n\n最終頁")
        docs = self.documents()
        self.assertEqual(len(docs), 1)
        self.assertNotIn("page", docs[0][1])
        self.assertEqual(docs[0][1]["page_split_reason"], "saved_text_no_page_boundaries")
        self.assertEqual(docs[0][0], f"hyoka:{SLUG}:{DOC_ID}")

    def test_single_page_pdf_has_confirmed_page_one(self):
        self.entry["pages"] = 1
        self.write("単頁本文")
        self.assertEqual(self.documents()[0][1]["page"], 1)

    def test_inconsistent_page_count_does_not_invent_pages(self):
        self.write("頁かもしれない\f続き")
        doc = self.documents()[0][1]
        self.assertNotIn("page", doc)
        self.assertEqual(doc["page_split_reason"], "page_boundary_count_mismatch")

    def test_non_pdf_document_is_not_split_into_physical_pages(self):
        self.entry["file_type"] = "xlsx"
        self.write("シート一\fシート二")
        doc = self.documents()[0][1]
        self.assertNotIn("page", doc)
        self.assertEqual(doc["page_split_reason"], "non_pdf_document")

    def test_unknown_page_count_is_explicit(self):
        self.entry.pop("pages")
        self.write("本文")
        self.assertEqual(self.documents()[0][1]["page_split_reason"], "page_count_unknown")

    def test_gone_is_retained_and_archive_is_never_scanned(self):
        self.entry["status"] = "gone"
        (self.work / "_archive").mkdir()
        (self.work / "_archive" / "bad.json").write_text("broken", encoding="utf-8")
        (self.root / "_archive").mkdir()
        self.write()
        self.assertEqual(self.documents()[0][1]["status"], "gone")

    def test_ocr_wait_is_an_audited_intentional_exclusion(self):
        self.entry["text_status"] = "needs_ocr"
        self.write()
        self.assertEqual(self.documents(), [])
        self.assertEqual(indexer.source_integrity_summary("hyoka", SLUG)["intentional_excluded"], 1)
        self.assertEqual(indexer.source_integrity_failures(), [])

    def test_ok_missing_text_blocks_publication(self):
        (self.work / self.entry["text_file"]).unlink()
        self.assertEqual(self.documents(), [])
        self.assertFalse(indexer.source_slug_can_be_published("hyoka", SLUG))

    def test_corrupt_gzip_is_audited(self):
        (self.work / self.entry["text_file"]).write_bytes(b"not gzip")
        self.assertEqual(self.documents(), [])
        self.assertEqual(len(indexer.source_integrity_failures()), 1)

    def test_empty_ok_body_is_not_indexed_as_header_only(self):
        self.write("")
        self.assertEqual(self.documents(), [])
        self.assertFalse(indexer.source_slug_can_be_published("hyoka", SLUG))

    def test_archive_or_external_text_reference_is_rejected(self):
        for path in ("_archive/old.txt.gz", "../other/text/example.txt.gz", str(self.root / "other.txt.gz")):
            with self.subTest(path=path):
                self.entry["text_file"] = path
                self.write()
                with self.assertRaises(ValueError):
                    self.documents(strict=True)

    def test_malformed_manifest_is_not_rewritten(self):
        path = self.work / "manifest.json"
        path.write_text("broken", encoding="utf-8")
        self.assertEqual(self.documents(), [])
        self.assertEqual(path.read_text(encoding="utf-8"), "broken")
        self.assertEqual(len(indexer.SKIPPED_SOURCES), 1)

    def test_missing_root_and_requested_slug_fail_closed(self):
        with self.assertRaises(ValueError):
            list(indexer.iter_hyoka_documents(root=self.root / "absent"))
        with self.assertRaises(FileNotFoundError):
            self.documents(slugs={"missing"}, strict=True)

    def test_null_year_remains_unknown(self):
        self.entry["fiscal_year"] = None
        self.entry["year_label"] = ""
        self.write()
        self.assertNotIn("fiscal_year", self.documents()[0][1])

    def test_missing_provenance_blocks_publication(self):
        for key in ("source_url", "page_url", "sha256", "fetched_at", "status"):
            with self.subTest(key=key):
                old = self.entry.pop(key)
                self.write()
                self.assertEqual(self.documents(), [])
                self.assertFalse(indexer.source_slug_can_be_published("hyoka", SLUG))
                self.entry[key] = old

    def test_limit_and_excluded_slugs(self):
        self.assertEqual(len(self.documents(limit=1)), 1)
        self.assertEqual(self.documents(exclude_slugs={SLUG}), [])

    def test_rebuild_switches_all_aliases_atomically_and_preserves_other_filters(self):
        client = FakeClient()
        minutes_before = dict(client.aliases["minutes-current"])
        self.assertEqual(self.run_main(client, "--mode", "rebuild"), 0)
        self.assertEqual(client.aliases["hyoka-current"], {"miyabe-hyoka-vtest": {}})
        self.assertEqual(client.aliases["minutes-current"], minutes_before)
        self.assertEqual(client.aliases["documents-current"]["minutes-old"], minutes_before["minutes-old"])
        self.assertEqual(sum(count for _, count in client.batches), 2)
        self.assertNotIn("hyoka-old", client.aliases["documents-current"])

    def test_rebuild_with_one_broken_document_keeps_old_hyoka_alias(self):
        self.manifest["documents"]["fedcba9876543210"] = {**self.entry, "text_file": "text/absent.txt.gz"}
        self.write()
        client = FakeClient()
        self.assertEqual(self.run_main(client, "--mode", "rebuild"), 2)
        self.assertEqual(client.aliases["hyoka-current"], {"hyoka-old": {}})
        self.assertFalse(any(path == "/_aliases" for _, path, _ in client.calls))

    def test_update_validates_all_text_before_any_bulk_or_delete(self):
        self.manifest["documents"]["fedcba9876543210"] = {**self.entry, "text_file": "text/absent.txt.gz"}
        self.write()
        client = FakeClient()
        with self.assertRaises(FileNotFoundError):
            self.run_main(client, "--mode", "update", "--slug", SLUG)
        self.assertEqual(client.batches, [])
        self.assertFalse(any(path.endswith("/_delete_by_query") for _, path, _ in client.calls))

    def test_update_bulk_precedes_old_generation_delete(self):
        client = FakeClient()
        self.assertEqual(self.run_main(client, "--mode", "update", "--slug", SLUG), 0)
        deletion = next(options for _, path, options in client.calls if path.endswith("/_delete_by_query"))
        filters = deletion["body"]["query"]["bool"]["filter"]
        self.assertIn({"term": {"doc_type": "hyoka"}}, filters)
        self.assertIn({"terms": {"slug": [SLUG]}}, filters)
        self.assertTrue(any("range" in item for item in filters))
        self.assertEqual(len(client.batches), 1)

    def test_resume_keeps_completed_slugs_and_finishes_without_reindexing_them(self):
        client = FakeClient()
        client.aliases["hyoka-current"] = {"miyabe-hyoka-vtest": {"filter": {"terms": {"slug": [SLUG]}}}}
        self.assertEqual(self.run_main(client, "--mode", "resume", "--resume-index", "miyabe-hyoka-vtest"), 0)
        self.assertEqual(client.batches, [])
        self.assertEqual(client.aliases["hyoka-current"], {"miyabe-hyoka-vtest": {}})

    def test_existing_rebuild_preserves_hyoka_partial_filters(self):
        client = FakeClient()
        current = {"hyoka-new": {"filter": {"terms": {"slug": [SLUG]}}}, "hyoka-old": {"filter": {"bool": {"must_not": [{"terms": {"slug": [SLUG]}}]}}}}
        client.aliases["hyoka-current"] = json.loads(json.dumps(current))
        indexer.switch_aliases(client, minutes_index="minutes-new", reiki_index=None,
                               minutes_alias="minutes-current", reiki_alias="reiki-current",
                               hyoka_alias="hyoka-current", documents_alias="documents-current")
        self.assertEqual(client.aliases["hyoka-current"], current)
        self.assertEqual(client.aliases["documents-current"]["hyoka-new"], current["hyoka-new"])

    def test_partial_publication_keeps_old_documents_for_unfinished_slugs(self):
        client = FakeClient()
        indexer.publish_partial_aliases(
            client, minutes_index=None, reiki_index=None, hyoka_index="hyoka-new",
            initial_minutes_indices=["minutes-old"], initial_reiki_indices=["reiki-old"],
            initial_hyoka_indices=["hyoka-old"], completed_minutes_slugs=set(), completed_reiki_slugs=set(),
            completed_hyoka_slugs={SLUG}, minutes_alias="minutes-current", reiki_alias="reiki-current",
            hyoka_alias="hyoka-current", documents_alias="documents-current",
        )
        expected = {
            "hyoka-new": {"filter": {"terms": {"slug": [SLUG]}}},
            "hyoka-old": {"filter": {"bool": {"must_not": [{"terms": {"slug": [SLUG]}}]}}},
        }
        self.assertEqual(client.aliases["hyoka-current"], expected)
        for name, metadata in expected.items():
            self.assertEqual(client.aliases["documents-current"][name], metadata)

    def test_no_switch_keeps_aliases_and_partial_rebuild_requires_opt_in(self):
        client = FakeClient()
        self.assertEqual(self.run_main(client, "--mode", "rebuild", "--slug", SLUG), 2)
        self.assertEqual(client.calls, [])
        self.assertEqual(self.run_main(client, "--mode", "rebuild", "--slug", SLUG, "--no-switch-alias"), 0)
        self.assertFalse(any(path == "/_aliases" for _, path, _ in client.calls))

    def test_bulk_failure_never_switches_alias(self):
        client = FakeClient()
        with mock.patch.object(client, "bulk_lines", side_effect=RuntimeError("simulated bulk error")):
            with self.assertRaises(RuntimeError):
                self.run_main(client, "--mode", "rebuild")
        self.assertFalse(any(path == "/_aliases" for _, path, _ in client.calls))

    def test_all_stays_minutes_and_reiki_for_existing_workers(self):
        client = FakeClient()
        args = indexer.parse_args(["--mode", "update", "--doc-type", "all", "--slug", SLUG])
        with (
            mock.patch.object(indexer, "parse_args", return_value=args),
            mock.patch.object(indexer, "OpenSearchClient", return_value=client),
            mock.patch.object(indexer, "update_one") as update,
        ):
            self.assertEqual(indexer.main(), 0)
        self.assertEqual([call.kwargs["doc_type"] for call in update.call_args_list], ["minutes", "reiki"])

    def test_another_rebuild_preserves_previous_partial_filters_for_each_type(self):
        for kind in ("hyoka", "minutes", "reiki"):
            with self.subTest(kind=kind):
                client = FakeClient()
                alias = f"{kind}-current"
                original = {
                    "old": {"filter": {"bool": {"must_not": [{"terms": {"slug": ["previous-done"]}}]}}},
                    "interrupted": {"filter": {"terms": {"slug": ["previous-done"]}}},
                }
                client.aliases[alias] = json.loads(json.dumps(original))
                snapshot = {}
                kwargs = {
                    "minutes_index": None, "reiki_index": None, "hyoka_index": None,
                    "initial_minutes_indices": ["minutes-old"], "initial_reiki_indices": ["reiki-old"],
                    "initial_hyoka_indices": ["hyoka-old"],
                    "completed_minutes_slugs": set(), "completed_reiki_slugs": set(), "completed_hyoka_slugs": set(),
                    "minutes_alias": "minutes-current", "reiki_alias": "reiki-current",
                    "hyoka_alias": "hyoka-current", "documents_alias": "documents-current",
                    "initial_alias_members": snapshot,
                }
                kwargs[f"{kind}_index"] = "new"
                kwargs[f"initial_{kind}_indices"] = ["old", "interrupted"]
                for completed in ({"new-done"}, {"new-done", "another-done"}):
                    kwargs[f"completed_{kind}_slugs"] = completed
                    indexer.publish_partial_aliases(client, **kwargs)
                    for index, metadata in original.items():
                        expected = {"bool": {"filter": [metadata["filter"], {
                            "bool": {"must_not": [{"terms": {"slug": sorted(completed)}}]}
                        }]}}
                        self.assertEqual(client.aliases[alias][index]["filter"], expected)
                        self.assertEqual(client.aliases["documents-current"][index]["filter"], expected)

    def test_missing_entire_published_slug_refuses_rebuild_and_resume_before_writes(self):
        for options in (("--mode", "rebuild"), ("--mode", "resume", "--resume-index", "miyabe-hyoka-vtest")):
            with self.subTest(options=options):
                client = FakeClient()
                client.public_slugs.append("missing-municipality")
                self.assertEqual(self.run_main(client, *options), 2)
                self.assertEqual(client.batches, [])
                self.assertFalse(any(method == "PUT" or path in {"/_aliases", "/miyabe-hyoka-vtest/_delete_by_query"}
                                     for method, path, _ in client.calls))

    def test_missing_published_slug_is_allowed_for_unpublished_trial_or_explicit_override(self):
        for option in ("--no-switch-alias", "--allow-partial-alias"):
            with self.subTest(option=option):
                client = FakeClient()
                client.public_slugs.append("missing-municipality")
                self.assertEqual(self.run_main(client, "--mode", "rebuild", option), 0)

    def test_incomplete_published_slug_aggregation_fails_closed(self):
        for response in ({}, {"aggregations": {"slugs": {"buckets": [], "sum_other_doc_count": 1}}}):
            with mock.patch.object(FakeClient, "request", return_value=response):
                with self.assertRaises(RuntimeError):
                    indexer.missing_published_hyoka_slugs(FakeClient(), "hyoka-current", self.root)

    def test_resume_clears_stale_page_ids_but_keeps_completed_slug(self):
        client = FakeClient()
        candidate = "miyabe-hyoka-vtest"
        client.aliases["hyoka-current"] = {
            candidate: {"filter": {"terms": {"slug": ["completed"]}}},
            "hyoka-old": {"filter": {"bool": {"must_not": [{"terms": {"slug": ["completed"]}}]}}},
        }
        # 候補indexに途中投入した旧3頁と、既に公開した別自治体を置く。
        stored = {f"hyoka:{SLUG}:{DOC_ID}:p{page}": SLUG for page in (1, 2, 3)}
        stored["completed:keep"] = "completed"
        stored[f"hyoka:{SLUG}:{DOC_ID}"] = SLUG  # 文書単位だった版も消す。
        request = client.request
        bulk = client.bulk_lines

        def fake_request(method, path, **kwargs):
            if path.endswith("/_delete_by_query"):
                self.assertEqual(path, f"/{candidate}/_delete_by_query")
                query = kwargs["body"]["query"]["bool"]
                self.assertEqual(query["filter"], [{"term": {"doc_type": "hyoka"}}])
                self.assertEqual(query["must_not"], [{"terms": {"slug": ["completed"]}}])
                for doc_id in list(stored):
                    if stored[doc_id] != "completed":
                        del stored[doc_id]
            return request(method, path, **kwargs)

        def fake_bulk(lines, count):
            for position in range(0, len(lines), 2):
                action = json.loads(lines[position])["index"]
                self.assertEqual(action["_index"], candidate)
                stored[action["_id"]] = json.loads(lines[position + 1])["slug"]
            return bulk(lines, count)

        self.entry["pages"] = 2
        self.write("改訂第一頁\f改訂第二頁")
        with mock.patch.object(client, "request", side_effect=fake_request), mock.patch.object(client, "bulk_lines", side_effect=fake_bulk):
            self.assertEqual(self.run_main(client, "--mode", "resume", "--resume-index", candidate), 0)
        self.assertEqual(set(stored), {"completed:keep", f"hyoka:{SLUG}:{DOC_ID}:p1", f"hyoka:{SLUG}:{DOC_ID}:p2"})

    def test_resume_cannot_clear_an_already_fully_published_index(self):
        client = FakeClient()
        client.aliases["hyoka-current"] = {"miyabe-hyoka-vtest": {}}
        self.assertEqual(self.run_main(client, "--mode", "resume", "--resume-index", "miyabe-hyoka-vtest"), 2)
        self.assertFalse(any(path.endswith("/_delete_by_query") for _, path, _ in client.calls))


if __name__ == "__main__":
    unittest.main()
