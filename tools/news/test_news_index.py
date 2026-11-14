"""お知らせの索引（API が引く SQLite）。"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.document_crawl import JST  # noqa: E402
from tools.news import news_index  # noqa: E402

NOW = datetime(2026, 11, 14, 16, 5, tzinfo=JST)
MASTER = {
    "13000": {"name": "東京都", "pref_name": "東京都", "entity_type": "prefecture"},
    "13209": {"name": "町田市", "pref_name": "東京都", "entity_type": "city"},
    "01100": {"name": "札幌市", "pref_name": "北海道", "entity_type": "designated_city"},
}
SLUG = "13209-machida-shi"


def item(item_id: str, title: str, date: str | None, seen: str, revision: int = 0, **extra) -> dict:
    row = {"id": item_id, "jis_code": "13209", "url": f"https://www.city.machida.tokyo.jp/{item_id}.html",
           "title": title, "first_seen_at": seen, "revision": revision, **extra}
    if date:
        row["date"] = date
    return row


class NewsIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.data_root = root / "data"
        self.work_root = root / "work"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def append(self, kind: str, rows: list[dict], slug: str = SLUG, year: int = 2026, tail: str = "") -> Path:
        path = self.data_root / slug / kind / f"{year}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.write(tail)
        return path

    def update(self, **kwargs) -> dict:
        return news_index.update_index(data_root=self.data_root, work_root=self.work_root, master=MASTER,
                                       now=NOW, **kwargs)

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        conn = sqlite3.connect(self.work_root / news_index.INDEX_NAME)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def test_items_take_the_last_revision_and_sort_by_the_found_day_for_future_dates(self) -> None:
        self.append("items", [
            item("a1", "図書館の休館", "2026-11-13", "2026-11-13 10:00:00"),
            item("a2", "市民まつり", "2026-12-01", "2026-11-14 10:00:00"),  # 催しの日
            item("a3", "日付の無いお知らせ", None, "2026-11-12 09:00:00"),
            item("a1", "図書館の臨時休館", "2026-11-14", "2026-11-13 10:00:00", revision=1),
            item("a1", "古い版", "2026-11-01", "2026-11-13 10:00:00", revision=0),
        ])
        result = self.update()
        self.assertTrue(result["index_rebuilt"])
        rows = self.query("SELECT item_id, title, date, sort_date FROM items ORDER BY item_id")
        self.assertEqual(rows, [
            ("a1", "図書館の臨時休館", "2026-11-14", "2026-11-13"),
            ("a2", "市民まつり", "2026-12-01", "2026-11-14"),
            ("a3", "日付の無いお知らせ", None, "2026-11-12"),
        ])
        self.assertEqual(self.query("SELECT slug, items, last_date FROM municipalities WHERE jis_code = '13209'"),
                         [(SLUG, 3, "2026-11-14")])

    def test_only_new_complete_lines_are_read(self) -> None:
        path = self.append("items", [item("a1", "一つ目", "2026-11-13", "2026-11-13 10:00:00")],
                           tail='{"id": "a2", "title": "書きかけ')
        self.update()
        self.assertEqual(self.query("SELECT COUNT(*) FROM items"), [(1,)])
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write('", "jis_code": "13209", "url": "https://www.city.machida.tokyo.jp/a2.html", '
                         '"first_seen_at": "2026-11-14 10:00:00", "date": "2026-11-14"}\n')
        result = self.update()
        self.assertFalse(result["index_rebuilt"])
        self.assertEqual(result["index_items"], 1)
        self.assertEqual(self.query("SELECT item_id, title FROM items ORDER BY item_id"),
                         [("a1", "一つ目"), ("a2", "書きかけ")])
        self.assertEqual(self.update()["index_items"], 0)

    def test_same_title_and_date_shows_the_first_found(self) -> None:
        self.append("items", [
            item("b2", "ごみの収集日", "2026-11-10", "2026-11-10 09:00:00"),
            item("b1", "ごみの収集日", "2026-11-10", "2026-11-10 09:00:00"),
            item("b3", "ごみの収集日", "2026-11-10", "2026-11-10 08:00:00"),
            item("b4", "ごみの収集日", "2026-11-11", "2026-11-11 09:00:00"),
        ])
        self.update()
        self.assertEqual(self.query("SELECT item_id FROM items WHERE hidden = 0 ORDER BY item_id"), [("b3",), ("b4",)])
        self.assertEqual(self.query("SELECT items FROM municipalities WHERE jis_code = '13209'"), [(2,)])

    def test_bodies_point_at_their_line_and_keep_text_when_the_page_is_gone_later(self) -> None:
        self.append("items", [item("a1", "お知らせ", "2026-11-13", "2026-11-13 10:00:00")])
        text = "市役所の窓口の受付時間を変えます。\n\n" + "詳しくは担当まで。" * 40
        path = self.append("bodies", [
            {"id": "zz", "jis_code": "13209", "kind": "gone"},
            {"id": "a1", "jis_code": "13209", "revision": 0, "kind": "html", "status": 200, "text": text,
             "page_title": "お知らせ | 町田市", "fetched_at": "2026-11-13 11:00:00",
             "attachments": [{"url": "https://www.city.machida.tokyo.jp/a.pdf", "text": "資料（PDF 120KB）"},
                             {"url": "javascript:void(0)", "text": "x"}]},
        ])
        self.update()
        self.append("bodies", [{"id": "a1", "jis_code": "13209", "revision": 0, "kind": "gone", "status": 404}])
        self.update()
        kind, chars, excerpt, ref, offset, attachments, count = self.query(
            "SELECT kind, chars, excerpt, ref, offset, attachments, attachments_count FROM bodies WHERE item_id = 'a1'")[0]
        self.assertEqual((kind, chars, ref, count), ("html", len(text), "bodies/2026.jsonl", 1))
        self.assertTrue(excerpt.startswith("市役所の窓口の受付時間を変えます。 詳しくは"))
        self.assertLessEqual(len(excerpt), news_index.EXCERPT_CHARS)
        self.assertEqual(json.loads(attachments)[0]["text"], "資料（PDF 120KB）")
        with path.open("rb") as handle:
            handle.seek(offset)
            self.assertEqual(json.loads(handle.readline())["text"], text)

    def test_attachments_prefer_read_text(self) -> None:
        url = "https://www.city.machida.tokyo.jp/a.pdf"
        path = self.append("attachments", [
            {"url": url, "item": "a1", "jis_code": "13209", "kind": "pdf", "pages": 3, "text": "本文\f二頁",
             "text_pages": 2, "bytes": 1200, "fetched_at": "2026-11-13 12:00:00"},
            {"url": url, "item": "a1", "jis_code": "13209", "kind": "failed", "error": "HTTP 503"},
            {"url": "ftp://x", "kind": "pdf"},
        ])
        self.update()
        rows = self.query("SELECT url, kind, pages, chars, text_pages, ref, offset FROM attachments")
        self.assertEqual(rows, [(url, "pdf", 3, 5, 2, "attachments/2026.jsonl", 0)])
        with path.open("rb") as handle:
            self.assertEqual(json.loads(handle.readline())["url"], url)

    def test_codes_limit_the_update_and_old_schema_is_rebuilt(self) -> None:
        self.append("items", [item("a1", "町田", "2026-11-13", "2026-11-13 10:00:00")])
        self.append("items", [{"id": "s1", "jis_code": "01100", "url": "https://www.city.sapporo.jp/s1.html",
                               "title": "札幌", "first_seen_at": "2026-11-13 10:00:00", "date": "2026-11-13"}],
                    slug="01100-sapporo-shi")
        self.update()
        self.append("items", [item("a2", "町田 2", "2026-11-14", "2026-11-14 10:00:00")])
        self.append("items", [{"id": "s2", "jis_code": "01100", "url": "https://www.city.sapporo.jp/s2.html",
                               "title": "札幌 2", "first_seen_at": "2026-11-14 10:00:00", "date": "2026-11-14"}],
                    slug="01100-sapporo-shi")
        self.assertEqual(self.update(codes={"01100"})["index_items"], 1)
        self.assertEqual(self.query("SELECT COUNT(*) FROM items WHERE jis_code = '13209'"), [(1,)])

        conn = sqlite3.connect(self.work_root / news_index.INDEX_NAME)
        with conn:
            conn.execute("UPDATE meta SET value = '0' WHERE key = 'schema_version'")
        conn.close()
        result = self.update(codes={"01100"})
        self.assertTrue(result["index_rebuilt"])
        self.assertEqual(self.query("SELECT COUNT(*) FROM items"), [(4,)])
        self.assertEqual(self.query("SELECT value FROM meta WHERE key = 'updated_at'"), [("2026-11-14 16:05:00",)])
        self.assertFalse((self.work_root / "index.sqlite.building").exists())

    def test_broken_lines_and_unknown_directories_are_skipped(self) -> None:
        self.append("items", [item("a1", "読める", "2026-11-13", "2026-11-13 10:00:00")], tail="not json\n")
        self.append("items", [item("x1", "", "2026-11-13", "2026-11-13 10:00:00")])
        (self.data_root / "latest").mkdir(parents=True)
        (self.data_root / SLUG / "items" / "notes.jsonl").write_text("{}\n", encoding="utf-8")
        result = self.update()
        self.assertEqual(result["index_items"], 1)
        self.assertEqual(self.query("SELECT item_id FROM items"), [("a1",)])

    def test_a_failure_drops_only_the_uncommitted_batch_and_the_next_run_reads_it_again(self) -> None:
        self.append("items", [item("a1", "町田", "2026-11-13", "2026-11-13 10:00:00")])
        self.update()
        self.append("items", [item("a2", "町田 2", "2026-11-14", "2026-11-14 10:00:00")])
        self.append("items", [{"id": "s1", "jis_code": "01100", "url": "https://www.city.sapporo.jp/s1.html",
                               "title": "札幌", "first_seen_at": "2026-11-14 10:00:00", "date": "2026-11-14"}],
                    slug="01100-sapporo-shi")
        original_item = news_index.NewsIndex._item
        original_seconds = news_index.COMMIT_SECONDS

        def broken(index, record, slug, code, ref, offset):
            if slug == SLUG:
                raise RuntimeError("壊れた")
            return original_item(index, record, slug, code, ref, offset)

        news_index.NewsIndex._item = broken
        news_index.COMMIT_SECONDS = 0.0  # 自治体ごとに確定させる（札幌が先、町田が後）
        try:
            with self.assertRaises(RuntimeError):
                self.update()
        finally:
            news_index.NewsIndex._item = original_item
            news_index.COMMIT_SECONDS = original_seconds
        self.assertEqual(self.query("SELECT item_id FROM items ORDER BY item_id"), [("a1",), ("s1",)])
        result = self.update()
        self.assertEqual(result["index_items"], 1)
        self.assertEqual(self.query("SELECT item_id FROM items ORDER BY item_id"), [("a1",), ("a2",), ("s1",)])

    def test_excerpt_skips_page_furniture_and_the_title(self) -> None:
        text = "\n".join([
            "「ホリデーイベント」の参加者募集", "ページ番号：1014261", "更新日", "令和8年11月13日", "印刷大きな文字で印刷",
            "キャンプ座間で行われるイベントの参加者を募集します。", "申し込みは12月1日まで。",
        ])
        self.assertEqual(news_index.excerpt(text, "「ホリデーイベント」の参加者募集"),
                         "キャンプ座間で行われるイベントの参加者を募集します。 申し込みは12月1日まで。")
        self.assertEqual(news_index.excerpt("ページの先頭です。メニューを飛ばして本文へ\n短い"),
                         "ページの先頭です。メニューを飛ばして本文へ 短い")


if __name__ == "__main__":
    unittest.main()
