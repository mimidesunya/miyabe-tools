#!/usr/bin/env python3
"""ためたお知らせを、API が引く索引（SQLite）に足す。

取得の 1 回の中で、フィード・本文・添付をそれぞれ読んだすぐ後に呼ぶ
（`deploy/scraper_runtime/celery/tasks.py` の `run_news_fetch`）。
項目・本文・添付の記録は追記だけなので、ファイルごとにどこまで読んだか（バイトの位置）を
覚え、増えた分だけ読む。

    work/news/index.sqlite

    items           項目。自治体と id ごとに最後の版
    bodies          本文の抜粋と、本文の行の位置（ファイルと、先頭からのバイト数）
    attachments     添付の PDF の頁数・文字数と、行の位置
    municipalities  自治体の名前と、ためたお知らせの数
    sources         ファイルごとの読んだ位置

本文と添付の全文は索引に入れない（本文だけで年に 5GB、添付で 24GB ほどになる）。
web は行の位置から元のファイルを 1 行だけ読む（`lib/news_api.php`）。

- web は読むだけで開く。書くのはこの台本だけで、自治体 1 つごとに確定する。
  読んだ位置も同じ確定に入れるので、途中で止まっても、次は止まったところから読む。
- 書いている途中の最後の行（改行で終わっていない行）は読まず、次に回す。
- 同じ自治体の同じ題名・同じ日付は、先に見つけた 1 件だけを出す（`hidden`）。
  新着と分野のフィードで URL が違うことがある（`latest_news.py` と同じ）。
- 索引の形を変えたら `SCHEMA_VERSION` を上げる。次の回に、別のファイルへ一から作り直して差し替える。

    python tools/news/news_index.py
    python tools/news/news_index.py --rebuild
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from tools.document_crawl import JST, load_master  # noqa: E402

DEFAULT_DATA_ROOT = WORKSPACE_ROOT / "data" / "news"
DEFAULT_WORK_ROOT = WORKSPACE_ROOT / "work" / "news"
INDEX_NAME = "index.sqlite"

SCHEMA_VERSION = "1"
SUMMARY_CHARS = 300
EXCERPT_CHARS = 160
EXCERPT_MIN_LINE = 15
BOILERPLATE = re.compile(r"^(ページの先頭|メニューを飛ばして|本文へ|ここから本文|現在の位置|このページ|ページ番号|更新日|最終更新)")
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SLUG_PATTERN = re.compile(r"^(\d{5})(-[a-z0-9-]+)?$")
YEAR_FILE = re.compile(r"^\d{4}\.jsonl$")
KINDS = ("items", "bodies", "attachments")

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sources (path TEXT PRIMARY KEY, offset INTEGER NOT NULL);
CREATE TABLE municipalities (
    jis_code TEXT PRIMARY KEY,
    slug TEXT,
    name TEXT NOT NULL,
    pref_code TEXT NOT NULL,
    pref_name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    items INTEGER NOT NULL DEFAULT 0,
    last_date TEXT
);
CREATE INDEX municipalities_by_name ON municipalities (name);
CREATE TABLE items (
    slug TEXT NOT NULL,
    item_id TEXT NOT NULL,
    jis_code TEXT NOT NULL,
    pref_code TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    date TEXT,
    sort_date TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    revision INTEGER NOT NULL,
    summary TEXT,
    categories TEXT,
    event_start TEXT,
    event_end TEXT,
    hidden INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (slug, item_id)
);
CREATE INDEX items_by_date ON items (hidden, sort_date DESC, first_seen_at DESC, item_id);
CREATE INDEX items_by_pref ON items (pref_code, hidden, sort_date DESC, first_seen_at DESC, item_id);
CREATE INDEX items_by_code ON items (jis_code, hidden, sort_date DESC, first_seen_at DESC, item_id);
CREATE INDEX items_by_title ON items (slug, title, sort_date);
CREATE TABLE bodies (
    slug TEXT NOT NULL,
    item_id TEXT NOT NULL,
    kind TEXT,
    status INTEGER,
    chars INTEGER NOT NULL DEFAULT 0,
    pages INTEGER,
    excerpt TEXT,
    page_title TEXT,
    final_url TEXT,
    fetched_at TEXT,
    ref TEXT NOT NULL,
    offset INTEGER NOT NULL,
    attachments TEXT,
    attachments_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (slug, item_id)
);
CREATE TABLE attachments (
    slug TEXT NOT NULL,
    url TEXT NOT NULL,
    item_id TEXT,
    kind TEXT,
    status INTEGER,
    pages INTEGER,
    chars INTEGER NOT NULL DEFAULT 0,
    text_pages INTEGER,
    truncated INTEGER NOT NULL DEFAULT 0,
    bytes INTEGER,
    fetched_at TEXT,
    error TEXT,
    ref TEXT NOT NULL,
    offset INTEGER NOT NULL,
    PRIMARY KEY (slug, url)
);
"""

UPSERT_ITEM = """
INSERT INTO items (slug, item_id, jis_code, pref_code, title, url, date, sort_date, first_seen_at, revision,
                   summary, categories, event_start, event_end)
VALUES (:slug, :item_id, :jis_code, :pref_code, :title, :url, :date, :sort_date, :first_seen_at, :revision,
        :summary, :categories, :event_start, :event_end)
ON CONFLICT (slug, item_id) DO UPDATE SET
    title = excluded.title, url = excluded.url, date = excluded.date, sort_date = excluded.sort_date,
    revision = excluded.revision, summary = excluded.summary, categories = excluded.categories,
    event_start = excluded.event_start, event_end = excluded.event_end
WHERE excluded.revision >= items.revision
"""

# 読み直して消えていた（gone）・読めなかった行で、前に読めた本文を消さない。
UPSERT_BODY = """
INSERT INTO bodies (slug, item_id, kind, status, chars, pages, excerpt, page_title, final_url, fetched_at,
                    ref, offset, attachments, attachments_count)
VALUES (:slug, :item_id, :kind, :status, :chars, :pages, :excerpt, :page_title, :final_url, :fetched_at,
        :ref, :offset, :attachments, :attachments_count)
ON CONFLICT (slug, item_id) DO UPDATE SET
    kind = excluded.kind, status = excluded.status, chars = excluded.chars, pages = excluded.pages,
    excerpt = excluded.excerpt, page_title = excluded.page_title, final_url = excluded.final_url,
    fetched_at = excluded.fetched_at, ref = excluded.ref, offset = excluded.offset,
    attachments = excluded.attachments, attachments_count = excluded.attachments_count
WHERE excluded.chars > 0 OR bodies.chars = 0
"""

UPSERT_ATTACHMENT = """
INSERT INTO attachments (slug, url, item_id, kind, status, pages, chars, text_pages, truncated, bytes,
                         fetched_at, error, ref, offset)
VALUES (:slug, :url, :item_id, :kind, :status, :pages, :chars, :text_pages, :truncated, :bytes,
        :fetched_at, :error, :ref, :offset)
ON CONFLICT (slug, url) DO UPDATE SET
    item_id = excluded.item_id, kind = excluded.kind, status = excluded.status, pages = excluded.pages,
    chars = excluded.chars, text_pages = excluded.text_pages, truncated = excluded.truncated,
    bytes = excluded.bytes, fetched_at = excluded.fetched_at, error = excluded.error,
    ref = excluded.ref, offset = excluded.offset
WHERE excluded.chars > 0 OR attachments.chars = 0
"""

# 同じ自治体の同じ題名・同じ日付は、先に見つけた 1 件（同じなら id の小さいもの）だけを出す。
MARK_DUPLICATES = """
UPDATE items SET hidden = CASE WHEN EXISTS (
    SELECT 1 FROM items AS other
    WHERE other.slug = items.slug AND other.title = items.title AND other.sort_date = items.sort_date
      AND (other.first_seen_at < items.first_seen_at
           OR (other.first_seen_at = items.first_seen_at AND other.item_id < items.item_id))
) THEN 1 ELSE 0 END
WHERE slug = ?
"""

COUNT_MUNICIPALITY = """
UPDATE municipalities SET slug = :slug,
    items = (SELECT COUNT(*) FROM items WHERE slug = :slug AND hidden = 0),
    last_date = (SELECT MAX(sort_date) FROM items WHERE slug = :slug AND hidden = 0)
WHERE jis_code = :jis_code
"""


def collapse(value: object) -> str:
    return " ".join(str(value or "").split())


def int_or_none(value: object) -> int | None:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def valid_date(value: object) -> str | None:
    text = str(value or "")[:10]
    return text if DATE_PATTERN.match(text) else None


def item_row(record: dict, slug: str, code: str) -> dict | None:
    item_id = str(record.get("id") or "")
    title = collapse(record.get("title"))
    url = str(record.get("url") or "")
    first_seen = str(record.get("first_seen_at") or "")
    if not item_id or not title or not url.startswith(("https://", "http://")) or not first_seen:
        return None
    date = valid_date(record.get("date"))
    seen_date = valid_date(first_seen)
    # 見つけた日より後の日付は催しの日なので、並べるときは見つけた日を使う。
    sort_date = date if date and (seen_date is None or date <= seen_date) else (seen_date or date)
    if sort_date is None:
        return None
    categories = [collapse(value) for value in record.get("categories") or [] if collapse(value)]
    return {
        "slug": slug, "item_id": item_id, "jis_code": code, "pref_code": code[:2],
        "title": title, "url": url, "date": date, "sort_date": sort_date, "first_seen_at": first_seen,
        "revision": int_or_none(record.get("revision")) or 0,
        "summary": collapse(record.get("summary"))[:SUMMARY_CHARS] or None,
        "categories": json.dumps(categories, ensure_ascii=False) if categories else None,
        "event_start": valid_date(record.get("event_start")), "event_end": valid_date(record.get("event_end")),
    }


def excerpt(text: str, title: str = "") -> str:
    """一覧に添える抜粋。ページ番号・更新日・「メニューを飛ばして本文へ」などを飛ばし、
    15 字以上の最初の行（お知らせの題名と同じ行は除く）から取る。そんな行が無ければ頭から。"""
    lines = [collapse(line) for line in text.split("\n")]
    title = collapse(title)
    for index, line in enumerate(lines):
        if len(line) >= EXCERPT_MIN_LINE and line != title and not BOILERPLATE.match(line):
            return collapse(" ".join(lines[index:]))[:EXCERPT_CHARS]
    return collapse(text)[:EXCERPT_CHARS]


def body_row(record: dict, slug: str, ref: str, offset: int, title: str = "") -> dict | None:
    item_id = str(record.get("id") or "")
    if not item_id:
        return None
    text = str(record.get("text") or "")
    links = [{"url": str(link.get("url")), "text": collapse(link.get("text"))[:200]}
             for link in record.get("attachments") or []
             if isinstance(link, dict) and str(link.get("url") or "").startswith(("https://", "http://"))]
    return {
        "slug": slug, "item_id": item_id, "kind": str(record.get("kind") or "") or None,
        "status": int_or_none(record.get("status")), "chars": len(text), "pages": int_or_none(record.get("pages")),
        "excerpt": excerpt(text, title) or None, "page_title": collapse(record.get("page_title")) or None,
        "final_url": str(record.get("final_url") or "") or None, "fetched_at": str(record.get("fetched_at") or "") or None,
        "ref": ref, "offset": offset,
        "attachments": json.dumps(links, ensure_ascii=False) if links else None, "attachments_count": len(links),
    }


def attachment_row(record: dict, slug: str, ref: str, offset: int) -> dict | None:
    url = str(record.get("url") or "")
    if not url.startswith(("https://", "http://")):
        return None
    return {
        "slug": slug, "url": url, "item_id": str(record.get("item") or "") or None,
        "kind": str(record.get("kind") or "") or None, "status": int_or_none(record.get("status")),
        "pages": int_or_none(record.get("pages")), "chars": len(str(record.get("text") or "")),
        "text_pages": int_or_none(record.get("text_pages")), "truncated": 1 if record.get("truncated") else 0,
        "bytes": int_or_none(record.get("bytes")), "fetched_at": str(record.get("fetched_at") or "") or None,
        "error": str(record.get("error") or "")[:300] or None, "ref": ref, "offset": offset,
    }


def new_lines(path: Path, offset: int):
    """`offset` から後の、改行で終わった行を (行の先頭の位置, 中身) で返す。最後に読み終えた位置も返す。"""
    lines: list[tuple[int, bytes]] = []
    position = offset
    with path.open("rb") as handle:
        handle.seek(offset)
        for raw in handle:
            if not raw.endswith(b"\n"):
                break
            lines.append((position, raw))
            position += len(raw)
    return lines, position


def year_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir() if YEAR_FILE.match(path.name))


class NewsIndex:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.offsets = dict(conn.execute("SELECT path, offset FROM sources"))

    def ingest(self, directory: Path) -> dict[str, int]:
        """1 自治体の増えた分を読み、確定する。"""
        slug = directory.name
        code = SLUG_PATTERN.match(slug).group(1)  # type: ignore[union-attr]
        counts = {"items": 0, "bodies": 0, "attachments": 0}
        handlers = {"items": self._item, "bodies": self._body, "attachments": self._attachment}
        with self.conn:
            for kind in KINDS:
                for path in year_files(directory / kind):
                    key = f"{slug}/{kind}/{path.name}"
                    offset = self.offsets.get(key, 0)
                    try:
                        size = path.stat().st_size
                    except OSError:
                        continue
                    if size < offset:  # 作り直されたファイル。初めから読む（同じ行は上書きになるだけ）
                        offset = 0
                    if size == offset:
                        continue
                    lines, position = new_lines(path, offset)
                    ref = f"{kind}/{path.name}"
                    for line_offset, raw in lines:
                        try:
                            record = json.loads(raw)
                        except ValueError:
                            continue
                        if isinstance(record, dict) and handlers[kind](record, slug, code, ref, line_offset):
                            counts[kind] += 1
                    if position != offset:
                        self.conn.execute("INSERT INTO sources (path, offset) VALUES (?, ?) "
                                          "ON CONFLICT (path) DO UPDATE SET offset = excluded.offset", (key, position))
                        self.offsets[key] = position
            if counts["items"]:
                self.conn.execute(MARK_DUPLICATES, (slug,))
                self.conn.execute(COUNT_MUNICIPALITY, {"slug": slug, "jis_code": code})
        return counts

    def _item(self, record: dict, slug: str, code: str, ref: str, offset: int) -> bool:
        row = item_row(record, slug, code)
        if row is None:
            return False
        self.conn.execute(UPSERT_ITEM, row)
        return True

    def _body(self, record: dict, slug: str, code: str, ref: str, offset: int) -> bool:
        found = self.conn.execute("SELECT title FROM items WHERE slug = ? AND item_id = ?",
                                  (slug, str(record.get("id") or ""))).fetchone()
        row = body_row(record, slug, ref, offset, found[0] if found else "")
        if row is None:
            return False
        self.conn.execute(UPSERT_BODY, row)
        return True

    def _attachment(self, record: dict, slug: str, code: str, ref: str, offset: int) -> bool:
        row = attachment_row(record, slug, ref, offset)
        if row is None:
            return False
        self.conn.execute(UPSERT_ATTACHMENT, row)
        return True


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=60)
    # web は読むだけで開くので WAL は使わない（読み手が -shm を書けない）。確定の一瞬だけ読み手を待たせる。
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def create(path: Path, master: dict[str, dict[str, str]]) -> sqlite3.Connection:
    conn = connect(path)
    with conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
        conn.executemany(
            "INSERT INTO municipalities (jis_code, name, pref_code, pref_name, entity_type) VALUES (?, ?, ?, ?, ?)",
            [(code, str(row.get("name") or code), code[:2], str(row.get("pref_name") or ""),
              str(row.get("entity_type") or ""))
             for code, row in master.items() if re.fullmatch(r"\d{5}", code)],
        )
    return conn


def schema_version(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return str(row[0]) if row else None


def municipality_dirs(data_root: Path, codes: set[str] | None) -> list[Path]:
    if not data_root.is_dir():
        return []
    found = []
    for directory in sorted(data_root.iterdir()):
        match = SLUG_PATTERN.match(directory.name)
        if not directory.is_dir() or match is None:
            continue
        if codes and match.group(1) not in codes:
            continue
        found.append(directory)
    return found


def update_index(*, data_root: Path = DEFAULT_DATA_ROOT, work_root: Path = DEFAULT_WORK_ROOT,
                 master: dict[str, dict[str, str]] | None = None, codes: set[str] | None = None,
                 rebuild: bool = False, now: datetime | None = None) -> dict[str, object]:
    """増えた分を索引に足す。索引が無い・形が古い・`rebuild` のときは別のファイルに一から作って差し替える。"""
    started = time.monotonic()
    path = work_root / INDEX_NAME
    work_root.mkdir(parents=True, exist_ok=True)
    fresh = rebuild or schema_version(path) != SCHEMA_VERSION
    master = master if master is not None else load_master()
    if fresh:
        target = path.with_name(path.name + ".building")
        for leftover in (target, target.with_name(target.name + "-journal")):
            if leftover.exists():
                leftover.unlink()
        conn = create(target, master)
        codes = None  # 一から作るときは全部の自治体
    else:
        target = path
        conn = connect(target)
    counts = {"items": 0, "bodies": 0, "attachments": 0}
    municipalities = 0
    try:
        index = NewsIndex(conn)
        for directory in municipality_dirs(data_root, codes):
            added = index.ingest(directory)
            municipalities += 1 if any(added.values()) else 0
            for key, value in added.items():
                counts[key] += value
        stamp = (now or datetime.now(JST)).astimezone(JST).strftime(TIME_FORMAT)
        with conn:
            conn.execute("INSERT INTO meta (key, value) VALUES ('updated_at', ?) "
                         "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (stamp,))
    finally:
        conn.close()
    if fresh:
        os.replace(target, path)
    return {
        "index_rebuilt": fresh,
        "index_municipalities": municipalities,
        "index_items": counts["items"],
        "index_bodies": counts["bodies"],
        "index_attachments": counts["attachments"],
        "index_seconds": round(time.monotonic() - started, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="ためたお知らせを API が引く索引（SQLite）に足す")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--code", action="append", default=[], help="この自治体コードだけ（何度でも）")
    parser.add_argument("--rebuild", action="store_true", help="一から作り直す")
    args = parser.parse_args()
    result = update_index(data_root=args.data_root, work_root=args.work_root,
                          codes=set(args.code) or None, rebuild=args.rebuild)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
