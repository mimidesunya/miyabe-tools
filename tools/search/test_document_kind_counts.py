"""会議録の種別件数を排他的に数え、実際の yield 数と一致させる。"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gijiroku"))

import build_opensearch_index as indexer  # noqa: E402
import gijiroku_targets  # noqa: E402


def record(path: Path, body: str, kind: str = "minutes") -> SimpleNamespace:
    return SimpleNamespace(
        rel_path=path.name,
        title=path.stem,
        meeting_name="定例会",
        year_label="令和8年",
        held_on="2026-08-31",
        doc_type=kind,
        source_url="https://example.invalid/minutes",
        content=body,
        title_terms=path.stem,
        meeting_name_terms="定例会",
        content_terms=body,
        indexed_at="2026-08-31T00:00:00Z",
    )


class DocumentKindCountsTest(unittest.TestCase):
    def test_duplicate_body_is_an_exclusive_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloads = root / "downloads"
            downloads.mkdir()
            paths = [downloads / f"{index}.txt" for index in range(5)]
            for path in paths:
                path.write_text("fixture", encoding="utf-8")
            target = {
                "slug": "01000-example",
                "code": "01000",
                "name": "例道",
                "downloads_dir": str(downloads),
                "index_json_path": str(root / "meetings_index.json"),
                "work_dir": str(root),
                "system_type": "fixture",
            }
            records = {
                paths[0]: record(paths[0], "同じ本文"),
                paths[1]: record(paths[1], "同じ本文"),
                paths[2]: record(paths[2], "別の本文"),
                paths[3]: record(paths[3], "目次", "toc"),
                paths[4]: record(paths[4], "一覧", "aux"),
            }
            indexer.reset_source_integrity_tracking()
            with (
                mock.patch.object(gijiroku_targets, "iter_gijiroku_targets", return_value=iter([target])),
                mock.patch.object(indexer, "choose_minutes_source_files", return_value=paths),
                mock.patch.object(indexer, "parse_minutes_source_meta", return_value={}),
                mock.patch.object(indexer, "build_minutes_record", side_effect=lambda path, *_: records[path]),
            ):
                documents = list(indexer.iter_minutes_documents(strict=False))

            self.assertEqual(len(documents), 2)
            payload = json.loads((root / "document_kinds.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], 2)
            self.assertEqual(payload["raw_total"], 5)
            self.assertEqual(payload["indexable_before_dedupe"], 3)
            self.assertEqual(payload["deduplicated"], 2)
            self.assertEqual(payload["yielded"], 2)
            self.assertEqual(payload["total"], 5)
            self.assertEqual(payload["indexable"], 2)
            self.assertEqual(
                payload["kinds"],
                {"aux": 1, "duplicate_body": 1, "minutes": 2, "toc": 1},
            )
            exclusions = sum(
                count for kind, count in payload["kinds"].items() if kind != "minutes"
            )
            self.assertEqual(payload["raw_total"], payload["yielded"] + exclusions)
            self.assertEqual(
                payload["indexable_before_dedupe"],
                payload["deduplicated"] + payload["kinds"]["duplicate_body"],
            )


class EmptyResultIsCurrentTest(unittest.TestCase):
    """0 件の結果が今も有効なら、掃き取りは積み直さない。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.downloads = root / "downloads"
        self.downloads.mkdir()
        self.paths = [self.downloads / f"dayori{index}.txt" for index in range(2)]
        for path in self.paths:
            path.write_text("議会だより", encoding="utf-8")
        self.root = root
        self.target = {
            "slug": "01609-erimo-cho",
            "code": "01609",
            "name": "えりも町",
            "downloads_dir": str(self.downloads),
            "index_json_path": str(root / "meetings_index.json"),
            "work_dir": str(root),
            "system_type": "fixture",
        }

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _index(self, kind: str = "aux") -> None:
        records = {path: record(path, "議会だより", kind) for path in self.paths}
        indexer.reset_source_integrity_tracking()
        with (
            mock.patch.object(gijiroku_targets, "iter_gijiroku_targets", return_value=iter([self.target])),
            mock.patch.object(indexer, "parse_minutes_source_meta", return_value={}),
            mock.patch.object(indexer, "build_minutes_record", side_effect=lambda path, *_: records[path]),
        ):
            list(indexer.iter_minutes_documents(strict=False))

    def test_unchanged_empty_result_is_current(self) -> None:
        self._index()
        self.assertTrue(indexer.minutes_empty_result_is_current(self.target))

    def test_a_new_file_makes_it_stale(self) -> None:
        self._index()
        (self.downloads / "minutes.txt").write_text("会議録", encoding="utf-8")
        self.assertFalse(indexer.minutes_empty_result_is_current(self.target))

    def test_a_rewritten_file_makes_it_stale(self) -> None:
        self._index()
        later = self.paths[0].stat().st_mtime + 3600
        os.utime(self.paths[0], (later, later))
        self.assertFalse(indexer.minutes_empty_result_is_current(self.target))

    def test_a_yielding_result_is_not_empty(self) -> None:
        self._index(kind="minutes")
        self.assertFalse(indexer.minutes_empty_result_is_current(self.target))

    def test_another_parser_generation_makes_it_stale(self) -> None:
        self._index()
        with mock.patch.object(indexer, "PARSER_GENERATION", indexer.PARSER_GENERATION + 1):
            self.assertFalse(indexer.minutes_empty_result_is_current(self.target))

    def test_an_old_payload_without_signature_is_not_trusted(self) -> None:
        payload_path = self.root / "document_kinds.json"
        payload_path.write_text(
            json.dumps({"version": 2, "raw_total": 2, "yielded": 0, "kinds": {"aux": 2}}),
            encoding="utf-8",
        )
        self.assertFalse(indexer.minutes_empty_result_is_current(self.target))


if __name__ == "__main__":
    unittest.main()
