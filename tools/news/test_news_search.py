"""お知らせの全文検索の索引（OpenSearch）。OpenSearch は手元の偽の相手に差し替える。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.document_crawl import JST  # noqa: E402
from tools.news import news_index, news_search  # noqa: E402

NOW = datetime(2026, 11, 14, 16, 5, tzinfo=JST)
MASTER = {
    "13209": {"name": "町田市", "pref_name": "東京都", "entity_type": "city"},
    "01100": {"name": "札幌市", "pref_name": "北海道", "entity_type": "designated_city"},
}
MACHIDA = "13209-machida-shi"
SAPPORO = "01100-sapporo-shi"
PDF = "https://www.city.machida.tokyo.jp/list.pdf"


def plain_terms(pair: tuple[str, str]) -> tuple[str, str]:
    """試験では分かち書きの部品を使わず、字を空白で区切るだけにする。"""
    return tuple(" ".join(text.split()) for text in pair)  # type: ignore[return-value]


def item(item_id: str, title: str, date: str | None, seen: str, code: str = "13209", revision: int = 0, **extra) -> dict:
    row = {"id": item_id, "jis_code": code, "url": f"https://www.example.jp/{code}/{item_id}.html", "title": title,
           "first_seen_at": seen, "revision": revision, **extra}
    if date:
        row["date"] = date
    return row


class FakeClient:
    def __init__(self, aliases: dict[str, list[str]] | None = None, fail_bulk: int = 0) -> None:
        self.aliases = aliases if aliases is not None else {}
        self.indices: dict[str, dict] = {}
        self.docs: dict[str, dict[str, dict]] = {}
        self.calls: list[tuple[str, str, object]] = []
        self.fail_bulk = fail_bulk
        self.bulks = 0
        self.order: list[str] = []

    def alias_targets(self, name: str) -> list[str]:
        return [index for index, names in self.aliases.items() if name in names]

    def resolve(self, name: str) -> list[str]:
        targets = self.alias_targets(name)
        return targets or [name]

    def request(self, method: str, path: str, body=None, **kwargs):
        self.calls.append((method, path, body))
        if method == "GET" and path.startswith("/_alias/"):
            targets = self.alias_targets(path.split("/")[2])
            if not targets:
                raise news_search.OpenSearchRequestError(method, path, 404, "missing")
            return {index: {} for index in targets}
        if path == "/_aliases":
            for action in body["actions"]:
                for verb, spec in action.items():
                    names = self.aliases.setdefault(spec["index"], [])
                    names.append(spec["alias"]) if verb == "add" else names.remove(spec["alias"])
            return {}
        if path.endswith("/_count"):
            return {"count": sum(len(self.docs.get(index, {})) for index in self.resolve(path.strip("/").split("/")[0]))}
        if method == "DELETE":
            self.docs.pop(path.strip("/"), None)
            self.indices.pop(path.strip("/"), None)
            return {}
        if method == "PUT" and "/" not in path.strip("/"):
            self.indices[path.strip("/")] = body
            self.docs.setdefault(path.strip("/"), {})
            return {}
        return {}

    def bulk_lines(self, lines: list[bytes], count: int) -> int:
        self.bulks += 1
        if self.fail_bulk and self.bulks >= self.fail_bulk:
            raise RuntimeError("bulk に失敗")
        payload = [json.loads(part) for line in lines for part in line.split(b"\n")]
        for action, document in zip(payload[::2], payload[1::2]):
            index = self.resolve(action["index"]["_index"])[0]
            self.docs.setdefault(index, {})[action["index"]["_id"]] = document
            self.order.append(action["index"]["_id"])
        return count


class NewsSearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.data_root = root / "data"
        self.work_root = root / "work"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def append(self, kind: str, rows: list[dict], slug: str = MACHIDA) -> None:
        path = self.data_root / slug / kind / "2026.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def seed(self) -> None:
        self.append("items", [
            item("a1", "電子入札の公表", "2026-11-13", "2026-11-13 10:00:00", summary="一般競争入札の案件", categories=["入札"]),
            item("a2", "図書館の休館", "2026-11-14", "2026-11-14 10:00:00"),
        ])
        self.append("bodies", [{"id": "a1", "jis_code": "13209", "kind": "html", "status": 200, "text": "町田市の入札です。",
                                "page_title": "入札 | 町田市", "fetched_at": "2026-11-13 10:05:00",
                                "attachments": [{"url": PDF, "text": "一覧表（PDF 120KB）"}]}])
        self.append("attachments", [{"url": PDF, "item": "a1", "jis_code": "13209", "kind": "pdf", "pages": 2,
                                     "text": "件名一覧\f工事", "fetched_at": "2026-11-13 10:10:00"}])
        self.append("items", [item("s1", "札幌のお知らせ", "2026-11-12", "2026-11-12 10:00:00", code="01100")], slug=SAPPORO)

    def build_index(self) -> None:
        news_index.update_index(data_root=self.data_root, work_root=self.work_root, master=MASTER, now=NOW)

    def sync(self, client: FakeClient, **kwargs) -> dict:
        path = self.work_root / news_index.INDEX_NAME
        conn = news_index.connect(path)
        try:
            return news_search.sync(conn, self.data_root, client, terms=plain_terms, **kwargs)
        finally:
            conn.close()

    def rebuild(self, client: FakeClient, **kwargs) -> dict:
        conn = news_index.connect(self.work_root / news_index.INDEX_NAME)
        try:
            return news_search.rebuild(conn, self.data_root, client, terms=plain_terms, log=lambda message: None, **kwargs)
        finally:
            conn.close()

    def dirty(self) -> list[tuple]:
        conn = news_index.connect(self.work_root / news_index.INDEX_NAME)
        try:
            return conn.execute("SELECT kind, slug, key FROM search_dirty ORDER BY kind, slug, key").fetchall()
        finally:
            conn.close()

    def test_writing_rows_marks_them_and_the_parent_attachments(self) -> None:
        self.seed()
        self.build_index()
        self.assertEqual(self.dirty(), [("attachment", MACHIDA, PDF), ("item", SAPPORO, "s1"),
                                        ("item", MACHIDA, "a1"), ("item", MACHIDA, "a2")])

    def test_sync_does_nothing_before_the_first_index_exists(self) -> None:
        self.seed()
        self.build_index()
        client = FakeClient()
        result = self.sync(client)
        self.assertIn("search_skipped", result)
        self.assertEqual(result["search_remaining"], 4)
        self.assertEqual(client.bulks, 0)

    def test_rebuild_fills_a_new_generation_and_switches_the_alias(self) -> None:
        self.seed()
        self.build_index()
        client = FakeClient()
        result = self.rebuild(client, build_id="1")
        index = "miyabe-news-v1"
        self.assertEqual((result["index"], result["documents"], result["previous"]), (index, 4, []))
        self.assertEqual(client.alias_targets("miyabe-news-current"), [index])
        mapping = client.indices[index]["mappings"]
        self.assertEqual(mapping["_source"]["excludes"], ["title_terms", "body_terms"])
        docs = client.docs[index]
        self.assertEqual(sorted(docs), sorted([f"news:{MACHIDA}:a1", f"news:{MACHIDA}:a2",
                                               news_search.attachment_id(MACHIDA, PDF), f"news:{SAPPORO}:s1"]))
        first = docs[f"news:{MACHIDA}:a1"]
        self.assertEqual((first["part"], first["body"], first["municipality_name"], first["hidden"], first["categories"]),
                         ("item", "町田市の入札です。", "町田市", False, ["入札"]))
        self.assertIn("一般競争入札の案件", first["body_terms"])
        attachment = docs[news_search.attachment_id(MACHIDA, PDF)]
        self.assertEqual((attachment["part"], attachment["title"], attachment["link_text"], attachment["item_key"],
                          attachment["attachment_url"], attachment["body"]),
                         ("attachment", "電子入札の公表", "一覧表（PDF 120KB）", f"news:{MACHIDA}:a1", PDF, "件名一覧\f工事"))
        self.assertIn("一覧表（PDF 120KB）", attachment["body_terms"])
        self.assertEqual(self.dirty(), [])  # 作り直しで入れた分の印は消える

    def test_sync_sends_only_what_changed_and_newest_first(self) -> None:
        self.seed()
        self.build_index()
        client = FakeClient()
        self.rebuild(client, build_id="1")
        self.append("items", [item("a2", "図書館の臨時休館", "2026-11-14", "2026-11-14 10:00:00", revision=1),
                              item("a3", "あしたのお知らせ", "2026-11-15", "2026-11-15 10:00:00")])
        self.build_index()
        client.order = []
        result = self.sync(client)
        self.assertEqual((result["search_indexed"], result["search_remaining"], result["search_out_of_time"]), (2, 0, False))
        self.assertEqual(client.order, [f"news:{MACHIDA}:a3", f"news:{MACHIDA}:a2"])  # 新しい日付から
        docs = client.docs["miyabe-news-v1"]
        self.assertEqual(docs[f"news:{MACHIDA}:a2"]["title"], "図書館の臨時休館")
        self.assertIn(f"news:{MACHIDA}:a3", docs)
        self.assertEqual(self.dirty(), [])

    def test_a_title_change_updates_the_attachment_documents_of_the_item(self) -> None:
        self.seed()
        self.build_index()
        client = FakeClient()
        self.rebuild(client, build_id="1")
        self.append("items", [item("a1", "入札の公表（更新）", "2026-11-13", "2026-11-13 10:00:00", revision=1)])
        self.build_index()
        self.sync(client)
        docs = client.docs["miyabe-news-v1"]
        self.assertEqual(docs[news_search.attachment_id(MACHIDA, PDF)]["title"], "入札の公表（更新）")

    def test_hidden_flips_reach_the_documents(self) -> None:
        self.append("items", [item("x1", "ごみの収集日", "2026-11-10", "2026-11-10 09:00:00")])
        self.build_index()
        client = FakeClient()
        self.rebuild(client, build_id="1")
        self.append("items", [item("x0", "ごみの収集日", "2026-11-10", "2026-11-10 08:00:00")])
        self.build_index()
        self.sync(client)
        docs = client.docs["miyabe-news-v1"]
        self.assertTrue(docs[f"news:{MACHIDA}:x1"]["hidden"])
        self.assertFalse(docs[f"news:{MACHIDA}:x0"]["hidden"])

    def test_a_failed_bulk_keeps_the_marks_for_the_next_run(self) -> None:
        self.seed()
        self.build_index()
        client = FakeClient()
        self.rebuild(client, build_id="1")
        self.append("items", [item("a4", "あたらしい", "2026-11-14", "2026-11-14 11:00:00")])
        self.build_index()
        client.fail_bulk = client.bulks + 1
        with self.assertRaises(RuntimeError):
            self.sync(client)
        self.assertEqual(self.dirty(), [("item", MACHIDA, "a4")])
        client.fail_bulk = 0
        self.assertEqual(self.sync(client)["search_indexed"], 1)
        self.assertEqual(self.dirty(), [])

    def test_the_time_budget_leaves_the_rest_for_the_next_run(self) -> None:
        self.append("items", [item(f"b{number:03d}", f"お知らせ {number}", "2026-11-13", "2026-11-13 10:00:00")
                              for number in range(150)])
        self.build_index()
        client = FakeClient()
        self.rebuild(client, build_id="1")
        self.append("items", [item(f"c{number:03d}", f"新着 {number}", "2026-11-14", "2026-11-14 10:00:00")
                              for number in range(150)])
        self.build_index()
        ticks = iter(range(0, 100000, 10))
        original_docs = news_search.BULK_DOCS
        news_search.BULK_DOCS = 10
        try:
            result = self.sync(client, budget_seconds=50, clock=lambda: next(ticks))
        finally:
            news_search.BULK_DOCS = original_docs
        self.assertTrue(result["search_out_of_time"])
        self.assertGreater(result["search_remaining"], 0)
        self.assertEqual(result["search_indexed"] + result["search_remaining"], 150)
        self.assertEqual(self.sync(client)["search_remaining"], 0)

    def test_a_mark_added_while_a_rebuild_runs_survives(self) -> None:
        self.seed()
        self.build_index()
        client = FakeClient()
        original_bulk = client.bulk_lines
        conn = news_index.connect(self.work_root / news_index.INDEX_NAME)

        def bulk(lines, count):  # 作り直している途中に、印が付け直される（項目が更新される）
            conn.execute(news_index.MARK_DIRTY, ("item", MACHIDA, "a1", "2026-11-13"))
            conn.commit()
            return original_bulk(lines, count)

        client.bulk_lines = bulk  # type: ignore[method-assign]
        try:
            self.rebuild(client, build_id="1")
        finally:
            conn.close()
        self.assertEqual(self.dirty(), [("item", MACHIDA, "a1")])

    def test_rebuild_refuses_to_publish_a_shrunken_index(self) -> None:
        self.seed()
        self.build_index()
        client = FakeClient()
        self.rebuild(client, build_id="1")
        client.docs["miyabe-news-v1"].update({f"old{number}": {} for number in range(100)})
        with self.assertRaises(RuntimeError):
            self.rebuild(client, build_id="2")
        self.assertEqual(client.alias_targets("miyabe-news-current"), ["miyabe-news-v1"])
        self.rebuild(client, build_id="3", allow_shrink=True)
        self.assertEqual(client.alias_targets("miyabe-news-current"), ["miyabe-news-v3"])

    def test_updates_wait_while_a_rebuild_runs_and_a_stale_mark_is_ignored(self) -> None:
        self.seed()
        self.build_index()
        client = FakeClient()
        self.rebuild(client, build_id="1")
        self.append("items", [item("a9", "作り直し中のお知らせ", "2026-11-15", "2026-11-15 10:00:00")])
        self.build_index()
        conn = news_index.connect(self.work_root / news_index.INDEX_NAME)
        try:
            news_search.mark_rebuild(conn, True)
        finally:
            conn.close()
        result = self.sync(client)
        self.assertIn("search_skipped", result)
        self.assertEqual(result["search_remaining"], 1)
        with self.assertRaises(RuntimeError):
            self.rebuild(client, build_id="2")  # 二つ同時には作り直さない
        conn = news_index.connect(self.work_root / news_index.INDEX_NAME)
        try:
            with conn:
                conn.execute("UPDATE meta SET value = '1' WHERE key = ?", (news_search.REBUILD_MARK,))  # 落ちて残った古い印
        finally:
            conn.close()
        self.assertEqual(self.sync(client)["search_indexed"], 1)
        self.assertEqual(self.rebuild(client, build_id="3")["index"], "miyabe-news-v3")
        conn = news_index.connect(self.work_root / news_index.INDEX_NAME)
        try:
            self.assertFalse(news_search.rebuild_running(conn))  # 終わったら印を消す
        finally:
            conn.close()

    def test_a_failed_rebuild_removes_the_unpublished_index_and_keeps_the_old_one(self) -> None:
        self.seed()
        self.build_index()
        client = FakeClient()
        self.rebuild(client, build_id="1")
        client.fail_bulk = client.bulks + 1
        with self.assertRaises(RuntimeError):
            self.rebuild(client, build_id="2")
        self.assertEqual(client.alias_targets("miyabe-news-current"), ["miyabe-news-v1"])
        self.assertNotIn("miyabe-news-v2", client.indices)
        conn = news_index.connect(self.work_root / news_index.INDEX_NAME)
        try:
            self.assertFalse(news_search.rebuild_running(conn))
        finally:
            conn.close()

    def test_a_rebuild_does_not_block_the_writer(self) -> None:
        self.seed()
        self.build_index()
        client = FakeClient()
        original = client.bulk_lines
        written: list[int] = []

        def bulk(lines, count):  # 作り直しの最中に、取得の worker が索引（SQLite）を書く
            writer = news_index.connect(self.work_root / news_index.INDEX_NAME)
            try:
                writer.execute("PRAGMA busy_timeout = 200")
                with writer:
                    writer.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('probe', '1')")
                written.append(1)
            finally:
                writer.close()
            return original(lines, count)

        client.bulk_lines = bulk  # type: ignore[method-assign]
        self.rebuild(client, build_id="1")
        self.assertTrue(written)

    def test_a_shifted_record_file_does_not_put_another_body_in(self) -> None:
        self.seed()
        self.build_index()
        path = self.data_root / MACHIDA / "bodies" / "2026.jsonl"
        path.write_text(json.dumps({"id": "other", "text": "別の本文"}) + "\n" + " " * 500 + "\n", encoding="utf-8")
        attachments = self.data_root / MACHIDA / "attachments" / "2026.jsonl"
        attachments.write_text(json.dumps({"url": "https://other.example/x.pdf", "text": "別の PDF"}) + "\n", encoding="utf-8")
        client = FakeClient()
        result = self.rebuild(client, build_id="1")
        docs = client.docs["miyabe-news-v1"]
        self.assertEqual(docs[f"news:{MACHIDA}:a1"]["body"], "")
        self.assertNotIn(news_search.attachment_id(MACHIDA, PDF), docs)
        self.assertEqual(result["dropped"], 1)

    def test_terms_need_the_tokenizer(self) -> None:
        self.assertEqual(news_search.terms_text("  "), "")
        original = sys.modules.get("japanese_search_tokenizer")
        sys.modules["japanese_search_tokenizer"] = None  # type: ignore[assignment]
        try:
            with self.assertRaises(news_search.MissingTokenizer):
                news_search.terms_text("市議会")
        finally:
            if original is not None:
                sys.modules["japanese_search_tokenizer"] = original
            else:
                del sys.modules["japanese_search_tokenizer"]

    def test_run_sync_without_the_index_file(self) -> None:
        self.assertIn("search_skipped", news_search.run_sync(data_root=self.data_root, work_root=self.work_root))


if __name__ == "__main__":
    unittest.main()
