"""会議録の種別件数を排他的に数え、実際の yield 数と一致させる。"""

import json
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


if __name__ == "__main__":
    unittest.main()
