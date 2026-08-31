"""取得済みファイルの黙った drop を、公開 alias の外へ留める。"""

import argparse
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
from scraped_source_records import load_json  # noqa: E402


def minute_record(path: Path, body: str, *, kind: str = "minutes") -> SimpleNamespace:
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


class SourceIntegrityGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.downloads = root / "downloads"
        self.downloads.mkdir()
        self.work_dir = root / "work"
        self.work_dir.mkdir()
        self.index_json = root / "meetings_index.json"
        self.index_json.write_text("[]", encoding="utf-8")
        self.slug = "13101-example"
        self.target = {
            "slug": self.slug,
            "code": "13101",
            "name": "例市",
            "downloads_dir": str(self.downloads),
            "index_json_path": str(self.index_json),
            "work_dir": str(self.work_dir),
            "system_type": "fixture",
        }

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_parse_drops_are_recorded_by_path_and_block_publish(self) -> None:
        paths = [self.downloads / name for name in ("ok.txt", "toc.txt", "none.txt", "broken.txt")]
        for path in paths:
            path.write_text("fixture", encoding="utf-8")

        records = {
            paths[0]: minute_record(paths[0], "本文"),
            paths[1]: minute_record(paths[1], "目次", kind="toc"),
            paths[2]: None,
        }

        def build(path: Path, *_args):
            if path == paths[3]:
                raise ValueError("broken fixture")
            return records[path]

        indexer.reset_source_integrity_tracking()
        with (
            mock.patch.object(gijiroku_targets, "iter_gijiroku_targets", return_value=iter([self.target])),
            mock.patch.object(indexer, "choose_minutes_source_files", return_value=paths),
            mock.patch.object(indexer, "parse_minutes_source_meta", return_value={}),
            mock.patch.object(indexer, "build_minutes_record", side_effect=build),
        ):
            documents = list(indexer.iter_minutes_documents(strict=False))

        self.assertEqual(len(documents), 1)
        failures = indexer.source_integrity_failures("minutes", self.slug)
        self.assertEqual({item["path"] for item in failures}, {str(paths[2]), str(paths[3])})
        summary = indexer.source_integrity_summary("minutes", self.slug)
        self.assertEqual(summary["raw_total"], 4)
        self.assertEqual(summary["yielded"], 1)
        self.assertEqual(summary["intentional_excluded"], 1)
        self.assertEqual(summary["unexplained_drop"], 2)
        self.assertEqual(
            summary["expected_indexable"] - summary["yielded"],
            summary["unexplained_drop"],
        )
        self.assertFalse(indexer.source_slug_can_be_published("minutes", self.slug))
        self.assertTrue(
            indexer.source_slug_can_be_published(
                "minutes", self.slug, allow_partial_alias=True
            )
        )

    def test_strict_json_loader_raises_for_malformed_json(self) -> None:
        path = Path(self._tmp.name) / "broken.json"
        path.write_text("{", encoding="utf-8")
        self.assertEqual(load_json(path, [], strict=False), [])
        with self.assertRaisesRegex(ValueError, str(path).replace("\\", "\\\\")):
            load_json(path, [], strict=True)

    def test_strict_minutes_iterator_does_not_hide_broken_index_json(self) -> None:
        source = self.downloads / "minutes.txt"
        source.write_text("令和8年8月31日\n会議を開きます。", encoding="utf-8")
        self.index_json.write_text("{", encoding="utf-8")
        indexer.reset_source_integrity_tracking()
        with mock.patch.object(
            gijiroku_targets, "iter_gijiroku_targets", return_value=iter([self.target])
        ):
            with self.assertRaisesRegex(RuntimeError, "failed to enumerate minutes"):
                list(indexer.iter_minutes_documents(strict=True))

    def test_reiki_parse_drops_are_recorded_by_path(self) -> None:
        root = Path(self._tmp.name) / "reiki"
        html_dir = root / "html"
        html_dir.mkdir(parents=True)
        paths = [html_dir / name for name in ("ok.html", "none.html", "broken.html")]
        for path in paths:
            path.write_text("<p>fixture</p>", encoding="utf-8")
        target = {
            "slug": self.slug,
            "code": "13101",
            "name": "例市",
            "system_type": "fixture",
            "work_root": str(root),
            "source_dir": str(html_dir),
            "html_dir": str(html_dir),
            "markdown_dir": str(root / "markdown"),
            "classification_dir": str(root / "classification"),
        }

        def build(_key, path: Path, *_args, **_kwargs):
            if path == paths[2]:
                raise ValueError("broken fixture")
            if path == paths[1]:
                return None
            return {
                "filename": path.stem,
                "title": "例規",
                "content_text": "条文本文",
                "source_url": "https://example.invalid/reiki",
            }

        indexer.reset_source_integrity_tracking()
        with (
            mock.patch.object(
                indexer.reiki_targets, "iter_reiki_targets", return_value=iter([target])
            ),
            mock.patch.object(indexer, "build_reiki_record", side_effect=build),
        ):
            documents = list(indexer.iter_reiki_documents(strict=False))

        self.assertEqual(len(documents), 1)
        failures = indexer.source_integrity_failures("reiki", self.slug)
        self.assertEqual({item["path"] for item in failures}, {str(paths[1]), str(paths[2])})
        summary = indexer.source_integrity_summary("reiki", self.slug)
        self.assertEqual(summary["raw_total"], 3)
        self.assertEqual(summary["yielded"], 1)
        self.assertEqual(summary["unexplained_drop"], 2)
        self.assertEqual(
            summary["expected_indexable"] - summary["yielded"],
            summary["unexplained_drop"],
        )

    def _run_rebuild_with_drop(self, *, allow_partial_alias: bool):
        paths = [self.downloads / "ok.txt", self.downloads / "broken.txt"]
        for path in paths:
            path.write_text("fixture", encoding="utf-8")
        args = argparse.Namespace(
            mode="rebuild",
            doc_type="minutes",
            slug=[],
            build_id="test",
            resume_index="",
            opensearch_url="http://example.invalid:9200",
            opensearch_user="",
            opensearch_password="",
            insecure_dev=False,
            documents_alias="documents-current",
            minutes_alias="minutes-current",
            reiki_alias="reiki-current",
            shards=1,
            replicas=0,
            bulk_size=10,
            bulk_bytes=1024 * 1024,
            bulk_concurrency=1,
            limit=0,
            no_switch_alias=False,
            allow_partial_alias=allow_partial_alias,
            allow_empty_slug_delete=False,
        )

        def build_record(path: Path, *_args):
            return minute_record(path, "本文") if path == paths[0] else None

        def build_one(_client, **kwargs):
            documents = list(kwargs["documents"])
            callback = kwargs.get("slug_complete_callback")
            if documents and callback is not None:
                callback(self.slug, documents[-1][1], len(documents))
            return len(documents)

        with (
            mock.patch.object(indexer, "parse_args", return_value=args),
            mock.patch.object(indexer, "OpenSearchClient", return_value=object()),
            mock.patch.object(indexer, "indices_for_alias", return_value=[]),
            mock.patch.object(
                indexer, "count_minutes_documents_by_slug", return_value={self.slug: 2}
            ),
            mock.patch.object(indexer, "search_rebuild_status_start", return_value={}),
            mock.patch.object(indexer, "search_rebuild_status_progress"),
            mock.patch.object(indexer, "search_rebuild_status_slug_published"),
            mock.patch.object(indexer, "search_rebuild_status_finish"),
            mock.patch.object(indexer, "build_one", side_effect=build_one),
            mock.patch.object(indexer, "publish_completed_slug") as publish,
            mock.patch.object(indexer, "switch_aliases") as switch,
            mock.patch.object(
                gijiroku_targets, "iter_gijiroku_targets", return_value=iter([self.target])
            ),
            mock.patch.object(indexer, "choose_minutes_source_files", return_value=paths),
            mock.patch.object(indexer, "parse_minutes_source_meta", return_value={}),
            mock.patch.object(indexer, "build_minutes_record", side_effect=build_record),
        ):
            result = indexer.main()
        return result, publish, switch

    def test_rebuild_does_not_publish_a_slug_or_switch_alias_after_drop(self) -> None:
        result, publish, switch = self._run_rebuild_with_drop(allow_partial_alias=False)
        self.assertEqual(result, 2)
        publish.assert_not_called()
        switch.assert_not_called()

    def test_allow_partial_alias_is_the_explicit_escape_hatch(self) -> None:
        result, publish, switch = self._run_rebuild_with_drop(allow_partial_alias=True)
        self.assertEqual(result, 0)
        publish.assert_called_once()
        switch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
