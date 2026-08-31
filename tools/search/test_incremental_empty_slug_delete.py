"""0件の自治体を、取得失敗と全廃の区別なしに削除しない。"""

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_opensearch_index as indexer  # noqa: E402


class FakeClient:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict]] = []
        self.bulk_counts: list[int] = []

    def request(self, method: str, path: str, **kwargs):
        self.requests.append((method, path, kwargs))
        if method == "GET" and path.startswith("/_alias/"):
            return {"minutes-v1": {"aliases": {}}}
        if path.endswith("/_delete_by_query"):
            return {"deleted": 1}
        return {}

    def bulk_lines(self, _lines, count: int):
        self.bulk_counts.append(count)
        return {"errors": False}

    def deleted_slugs(self) -> list[str]:
        calls = [item for item in self.requests if item[1].endswith("/_delete_by_query")]
        self.assert_single(calls)
        filters = calls[0][2]["body"]["query"]["bool"]["filter"]
        return next(item["terms"]["slug"] for item in filters if "terms" in item)

    @staticmethod
    def assert_single(items) -> None:
        if len(items) != 1:
            raise AssertionError(f"expected one delete call, got {len(items)}")


def update(client: FakeClient, documents, slugs, **kwargs) -> int:
    return indexer.update_one(
        client,
        doc_type="minutes",
        index_prefix="minutes",
        alias="minutes-current",
        documents_alias="documents-current",
        minutes_alias="minutes-current",
        reiki_alias="reiki-current",
        build_id="test",
        documents=documents,
        slugs=set(slugs),
        shards=1,
        replicas=0,
        bulk_size=10,
        bulk_bytes=1024 * 1024,
        bulk_concurrency=1,
        switch_alias=True,
        **kwargs,
    )


class IncrementalEmptySlugDeleteTest(unittest.TestCase):
    def test_only_a_slug_that_yielded_documents_is_generation_deleted(self) -> None:
        client = FakeClient()
        stderr = io.StringIO()
        documents = [("minutes:a:1", {"slug": "slug-a", "indexed_at": "2026-08-31T00:00:01Z"})]
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(update(client, documents, {"slug-a", "slug-b"}), 1)
        self.assertEqual(client.deleted_slugs(), ["slug-a"])
        self.assertIn("slug-b", stderr.getvalue())
        self.assertIn("--allow-empty-slug-delete", stderr.getvalue())

    def test_explicit_empty_delete_works_without_bulk_documents(self) -> None:
        client = FakeClient()
        self.assertEqual(
            update(
                client,
                [],
                {"slug-b"},
                allow_empty_slug_delete=True,
            ),
            0,
        )
        self.assertEqual(client.bulk_counts, [])
        self.assertEqual(client.deleted_slugs(), ["slug-b"])


if __name__ == "__main__":
    unittest.main()
