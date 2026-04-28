#!/usr/bin/env python3
"""Build a lean SQLite full-text index for scraped local assembly minutes."""

from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote_to_bytes, urlsplit

sys.path.append(str(Path(__file__).parent))
sys.path.append(str(Path(__file__).resolve().parent.parent.parent / "lib" / "python"))
import gijiroku_storage
import gijiroku_targets
import japanese_search_tokenizer


FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
ERA_BASE_YEAR = {"昭和": 1925, "平成": 1988, "令和": 2018}
DATE_PATTERN = re.compile(
    r"(昭和|平成|令和)\s*([元\d０-９]+)年(?:・(昭和|平成|令和)元年)?\s*([\d０-９]{1,2})月\s*([\d０-９]{1,2})日"
)
YEAR_LABEL_PATTERN = re.compile(r"(昭和|平成|令和)\s*([元\d０-９]+)年(?:・(昭和|平成|令和)元年)?")
FILE_DATE_PATTERN = re.compile(r"([0-9]{2})月([0-9]{2})日")
INCREMENTAL_COMMIT_BATCH_SIZE = 25
SQLITE_INCREMENTAL_CACHE_SIZE_KIB = 32 * 1024
SQLITE_BULK_LOAD_CACHE_SIZE_KIB = 128 * 1024
FTS_RUNTIME_AUTOMERGE = 8
FTS_RUNTIME_CRISISMERGE = 64
FTS_RUNTIME_MERGE_PAGES = 1000
MINUTES_TERMS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS minutes_terms (
    id INTEGER PRIMARY KEY,
    title_terms TEXT NOT NULL,
    meeting_name_terms TEXT NOT NULL,
    content_terms TEXT NOT NULL,
    FOREIGN KEY(id) REFERENCES minutes(id) ON DELETE CASCADE
);
"""
MINUTES_FTS_TABLE_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS minutes_fts USING fts5(
    title_terms,
    meeting_name_terms,
    content_terms,
    content='minutes_terms',
    content_rowid='id',
    tokenize='unicode61'
)
"""
INCREMENTAL_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS minutes (
    id INTEGER PRIMARY KEY,
    rel_path TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    meeting_name TEXT,
    year_label TEXT NOT NULL,
    held_on TEXT,
    gregorian_year INTEGER,
    month INTEGER,
    day INTEGER,
    doc_type TEXT NOT NULL,
    ext TEXT NOT NULL,
    source_fino INTEGER,
    source_year INTEGER,
    source_url TEXT,
    content TEXT NOT NULL,
    indexed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS minutes_terms (
    id INTEGER PRIMARY KEY,
    title_terms TEXT NOT NULL,
    meeting_name_terms TEXT NOT NULL,
    content_terms TEXT NOT NULL,
    FOREIGN KEY(id) REFERENCES minutes(id) ON DELETE CASCADE
);

CREATE VIRTUAL TABLE IF NOT EXISTS minutes_fts USING fts5(
    title_terms,
    meeting_name_terms,
    content_terms,
    content='minutes_terms',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE INDEX IF NOT EXISTS idx_minutes_held_on ON minutes(held_on);
CREATE INDEX IF NOT EXISTS idx_minutes_doc_type ON minutes(doc_type);
CREATE INDEX IF NOT EXISTS idx_minutes_doc_type_held_on_id ON minutes(doc_type, held_on DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_minutes_source_fino ON minutes(source_fino);
CREATE INDEX IF NOT EXISTS idx_minutes_year_label ON minutes(year_label);
"""
REQUIRED_MINUTES_COLUMNS = {
    "id",
    "rel_path",
    "title",
    "meeting_name",
    "year_label",
    "held_on",
    "gregorian_year",
    "month",
    "day",
    "doc_type",
    "ext",
    "source_fino",
    "source_year",
    "source_url",
    "content",
    "indexed_at",
}
REQUIRED_MINUTES_TERMS_COLUMNS = ["id", "title_terms", "meeting_name_terms", "content_terms"]


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def load_config(root: Path) -> dict:
    for candidate in (root / "data" / "config.json", root / "data" / "config.example.json"):
        if candidate.exists():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                return {}
            return data if isinstance(data, dict) else {}
    return {}


def default_slug(root: Path) -> str:
    config = load_config(root)
    # 自治体一覧は master から引けるので、ここで必要なのは「未指定時の既定 slug」だけ。
    value = str(config.get("DEFAULT_SLUG", "")).strip()
    if value == "":
        return ""
    try:
        return str(gijiroku_targets.load_gijiroku_target(value)["slug"])
    except Exception:
        return value


def municipality_gijiroku_paths(_root: Path, slug: str) -> tuple[Path, Path, Path]:
    # code-only や旧 name-only 指定でも、保存先は canonical な code-name slug に正規化して求める。
    target = gijiroku_targets.load_gijiroku_target(slug)
    return (Path(target["downloads_dir"]), Path(target["index_json_path"]), Path(target["db_path"]))


@dataclass(frozen=True)
class SourceMeta:
    title: str
    year_label: str
    meeting_name_hint: str | None
    source_url: str
    source_year: int | None
    source_fino: int | None


@dataclass(frozen=True)
class MinuteRecord:
    rel_path: str
    title: str
    meeting_name: str | None
    year_label: str
    held_on: str | None
    gregorian_year: int | None
    month: int | None
    day: int | None
    doc_type: str
    ext: str
    source_fino: int | None
    source_year: int | None
    source_url: str | None
    content: str
    title_terms: str
    meeting_name_terms: str
    content_terms: str
    indexed_at: str


_PENDING_INCREMENTAL_RECORDS: dict[Path, list[MinuteRecord]] = {}
_ATEXIT_REGISTERED = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scraped議事録をSQLite FTS5に登録します。"
    )
    parser.add_argument(
        "--slug",
        default=None,
        help="自治体slug。未指定時は config の DEFAULT_SLUG を使います。",
    )
    parser.add_argument(
        "--downloads-dir",
        type=Path,
        default=None,
        help="スクレイプ済みファイルのディレクトリ",
    )
    parser.add_argument(
        "--index-json",
        type=Path,
        default=None,
        help="meetings_index.json のパス（任意）",
    )
    parser.add_argument(
        "--output-db",
        type=Path,
        default=None,
        help="作成するSQLite DBパス",
    )
    args = parser.parse_args()
    root = project_root()
    args.slug = (args.slug or default_slug(root)).strip()
    if args.slug == "":
        parser.error("自治体slugを決定できませんでした。--slug を指定してください。")
    default_downloads, default_index_json, default_output_db = municipality_gijiroku_paths(root, args.slug)
    if args.downloads_dir is None:
        args.downloads_dir = default_downloads
    if args.index_json is None:
        args.index_json = default_index_json
    if args.output_db is None:
        args.output_db = default_output_db
    return args


def read_text_auto(path: Path) -> str:
    return gijiroku_storage.read_text_auto(path)


def html_to_text(html: str) -> str:
    text = re.sub(r"<script[\\s\\S]*?</script>", "", html, flags=re.IGNORECASE)
    text = re.sub(r"<style[\\s\\S]*?</style>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|tr|table|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\r\n|\r", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_title(file_path: Path) -> str:
    return file_path.stem.strip() or file_path.name


def normalize_space(value: str) -> str:
    return re.sub(r"[ \t\u3000]+", " ", value).strip()


def decode_query_component(value: str) -> str:
    if not value:
        return ""

    try:
        raw = unquote_to_bytes(value)
    except Exception:
        return ""

    for encoding in ("cp932", "shift_jis", "utf-8"):
        try:
            return normalize_space(raw.decode(encoding))
        except Exception:
            continue

    return normalize_space(raw.decode("utf-8", errors="ignore"))


def raw_query_values(url: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for part in urlsplit(url).query.split("&"):
        if not part:
            continue
        key, _, value = part.partition("=")
        values.setdefault(key, []).append(value)
    return values


def to_ascii_digits(value: str) -> str:
    return value.translate(FULLWIDTH_DIGITS)


def japanese_year_to_int(value: str) -> int | None:
    raw = to_ascii_digits(value.strip())
    if raw == "元":
        return 1
    if raw.isdigit():
        return int(raw)
    return None


def era_to_gregorian(era: str, year_text: str) -> int | None:
    era_year = japanese_year_to_int(year_text)
    if era_year is None:
        return None
    base_year = ERA_BASE_YEAR.get(era)
    if base_year is None:
        return None
    return base_year + era_year


def parse_optional_int(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return int(text)


def first_nonempty_lines(text: str, limit: int = 8) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        clean = normalize_space(line)
        if clean:
            lines.append(clean)
        if len(lines) >= limit:
            break
    return lines


def extract_year_label(text: str, fallback: str | None = None) -> str | None:
    head = "\n".join(first_nonempty_lines(text, limit=6))
    match = YEAR_LABEL_PATTERN.search(head)
    if match:
        label = f"{match.group(1)}{to_ascii_digits(match.group(2))}年"
        if match.group(3):
            label += f"・{match.group(3)}元年"
        return label
    return fallback


def normalize_year_label_candidate(value: str) -> str | None:
    candidate = normalize_space(value)
    match = YEAR_LABEL_PATTERN.fullmatch(candidate)
    if not match:
        return None

    label = f"{match.group(1)}{to_ascii_digits(match.group(2))}年"
    if match.group(3):
        label += f"・{match.group(3)}元年"
    return label


def extract_held_on(text: str, title: str, source_year: int | None) -> tuple[str | None, int | None, int | None, int | None]:
    head = "\n".join(first_nonempty_lines(text, limit=20))
    match = DATE_PATTERN.search(head)
    if match:
        gregorian_year = era_to_gregorian(match.group(1), match.group(2))
        month = int(to_ascii_digits(match.group(4)))
        day = int(to_ascii_digits(match.group(5)))
        if gregorian_year is not None:
            held_on = date(gregorian_year, month, day).isoformat()
            return held_on, gregorian_year, month, day

    match = FILE_DATE_PATTERN.search(title)
    if match and source_year is not None:
        month = int(match.group(1))
        day = int(match.group(2))
        held_on = date(source_year, month, day).isoformat()
        return held_on, source_year, month, day

    return None, source_year, None, None


def extract_meeting_name(text: str) -> str | None:
    lines = first_nonempty_lines(text, limit=5)
    if len(lines) >= 2:
        second = lines[1]
        if "－" not in second and len(second) >= 4:
            return second

    if lines:
        first = re.sub(r"－[^－]+$", "", lines[0]).strip()
        if len(first) >= 4:
            return first
    return None


def trim_meta_meeting_name(label: str, title: str) -> str:
    trimmed = normalize_space(label)
    trimmed = re.sub(r"^(昭和|平成|令和)\s*[元\d０-９]+年\s*", "", trimmed)
    title_pattern = re.escape(normalize_space(title))
    trimmed = re.sub(rf"[｜|－-]\s*{title_pattern}$", "", trimmed).strip()
    return normalize_space(trimmed)


def extract_meta_meeting_name(source_url: str, title: str) -> str | None:
    query = raw_query_values(source_url)
    title_hint = decode_query_component((query.get("TITL") or [""])[0])
    title_subt = decode_query_component((query.get("TITL_SUBT") or [""])[0])

    candidates: list[str] = []
    if title_hint:
        candidates.append(title_hint)
    if title_subt:
        trimmed = trim_meta_meeting_name(title_subt, title)
        if trimmed:
            candidates.append(trimmed)

    for candidate in candidates:
        if candidate and candidate != normalize_space(title):
            return candidate
    return None


def classify_doc_type(title: str, text: str) -> str:
    if title.endswith("目次"):
        return "toc"
    head = "\n".join(first_nonempty_lines(text, limit=6))
    if "会議録目次" in head:
        return "toc"
    return "minutes"


def table_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except sqlite3.DatabaseError:
        return []
    return [str(row[1]) for row in rows if len(row) >= 2]


def table_sql(conn: sqlite3.Connection, table_name: str) -> str:
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
            (table_name,),
        ).fetchone()
    except sqlite3.DatabaseError:
        return ""
    if row is None or not row[0]:
        return ""
    return str(row[0])


def fts_uses_external_content(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    content_table: str,
    content_rowid: str,
) -> bool:
    sql = re.sub(r"\s+", "", table_sql(conn, table_name).lower())
    if not sql:
        return False
    return (
        f"content='{content_table}'" in sql
        and f"content_rowid='{content_rowid}'" in sql
    )


def minutes_fts_columns(conn: sqlite3.Connection) -> list[str]:
    return table_columns(conn, "minutes_fts")


def minutes_table_columns(conn: sqlite3.Connection) -> set[str]:
    return set(table_columns(conn, "minutes"))


def minutes_terms_columns(conn: sqlite3.Connection) -> list[str]:
    return table_columns(conn, "minutes_terms")


def minutes_table_schema_matches(conn: sqlite3.Connection) -> bool:
    columns = minutes_table_columns(conn)
    return columns == set() or REQUIRED_MINUTES_COLUMNS.issubset(columns)


def minutes_terms_schema_matches(conn: sqlite3.Connection) -> bool:
    return minutes_terms_columns(conn) == REQUIRED_MINUTES_TERMS_COLUMNS


def minutes_fts_schema_matches(conn: sqlite3.Connection) -> bool:
    return (
        minutes_fts_columns(conn) == ["title_terms", "meeting_name_terms", "content_terms"]
        and fts_uses_external_content(
            conn,
            table_name="minutes_fts",
            content_table="minutes_terms",
            content_rowid="id",
        )
    )


def create_minutes_terms(conn: sqlite3.Connection) -> None:
    conn.execute(MINUTES_TERMS_TABLE_SQL)


def create_minutes_fts(conn: sqlite3.Connection) -> None:
    conn.execute(MINUTES_FTS_TABLE_SQL)


def configure_minutes_fts(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO minutes_fts(minutes_fts, rank) VALUES('automerge', ?)", (FTS_RUNTIME_AUTOMERGE,))
    conn.execute("INSERT INTO minutes_fts(minutes_fts, rank) VALUES('crisismerge', ?)", (FTS_RUNTIME_CRISISMERGE,))


def merge_minutes_fts(conn: sqlite3.Connection, *, pages: int = FTS_RUNTIME_MERGE_PAGES) -> None:
    conn.execute("INSERT INTO minutes_fts(minutes_fts, rank) VALUES('merge', ?)", (pages,))


def optimize_minutes_fts(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO minutes_fts(minutes_fts) VALUES('optimize')")


def delete_minutes_fts_row(
    conn: sqlite3.Connection,
    row_id: int,
    *,
    title_terms: str,
    meeting_name_terms: str,
    content_terms: str,
) -> None:
    conn.execute(
        """
        INSERT INTO minutes_fts(minutes_fts, rowid, title_terms, meeting_name_terms, content_terms)
        VALUES('delete', ?, ?, ?, ?)
        """,
        (row_id, title_terms, meeting_name_terms, content_terms),
    )


def insert_minutes_fts_row(
    conn: sqlite3.Connection,
    row_id: int,
    *,
    title_terms: str,
    meeting_name_terms: str,
    content_terms: str,
) -> None:
    conn.execute(
        """
        INSERT INTO minutes_fts(rowid, title_terms, meeting_name_terms, content_terms)
        VALUES (?, ?, ?, ?)
        """,
        (row_id, title_terms, meeting_name_terms, content_terms),
    )


def rebuild_minutes_terms(
    conn: sqlite3.Connection,
    *,
    heartbeat_callback: Callable[[], None] | None = None,
) -> None:
    conn.execute("DELETE FROM minutes_terms")
    last_heartbeat_at = emit_heartbeat(heartbeat_callback, 0.0, force=True)
    processed = 0
    for row_id, title, meeting_name, content, doc_type in conn.execute(
        "SELECT id, title, meeting_name, content, doc_type FROM minutes"
    ):
        if str(doc_type or "") != "minutes":
            continue
        conn.execute(
            """
            INSERT INTO minutes_terms(id, title_terms, meeting_name_terms, content_terms)
            VALUES (?, ?, ?, ?)
            """,
            (
                int(row_id),
                japanese_search_tokenizer.document_terms_text(str(title or "")),
                japanese_search_tokenizer.document_terms_text(str(meeting_name or "")),
                japanese_search_tokenizer.document_terms_text(str(content or "")),
            ),
        )
        processed += 1
        if processed % 250 == 0:
            last_heartbeat_at = emit_heartbeat(heartbeat_callback, last_heartbeat_at)
    emit_heartbeat(heartbeat_callback, last_heartbeat_at, force=True)


def minutes_terms_needs_rebuild(conn: sqlite3.Connection) -> bool:
    try:
        indexed_count = int(
            conn.execute("SELECT COUNT(*) FROM minutes WHERE doc_type = 'minutes'").fetchone()[0]
        )
        terms_count = int(conn.execute("SELECT COUNT(*) FROM minutes_terms").fetchone()[0])
    except Exception:
        return True
    return indexed_count != terms_count


def emit_heartbeat(
    heartbeat_callback: Callable[[], None] | None,
    last_heartbeat_at: float,
    *,
    force: bool = False,
    interval_seconds: float = 5.0,
) -> float:
    if heartbeat_callback is None:
        return last_heartbeat_at
    now = time.monotonic()
    if not force and last_heartbeat_at and (now - last_heartbeat_at) < interval_seconds:
        return last_heartbeat_at
    heartbeat_callback()
    return now


def rebuild_minutes_fts(
    conn: sqlite3.Connection,
    *,
    heartbeat_callback: Callable[[], None] | None = None,
) -> None:
    conn.execute("DROP TABLE IF EXISTS minutes_fts")
    create_minutes_fts(conn)
    emit_heartbeat(heartbeat_callback, 0.0, force=True)
    conn.execute("INSERT INTO minutes_fts(minutes_fts) VALUES('rebuild')")
    configure_minutes_fts(conn)
    emit_heartbeat(heartbeat_callback, 0.0, force=True)


def parse_source_meta(index_json: Path) -> dict[tuple[str, str, str], SourceMeta]:
    if not index_json.exists():
        return {}

    try:
        rows = json.loads(index_json.read_text(encoding="utf-8"))
    except Exception:
        return {}

    metas: dict[tuple[str, str, str], SourceMeta] = {}
    if not isinstance(rows, list):
        return metas

    for row in rows:
        if not isinstance(row, dict):
            continue

        title = str(row.get("title", "")).strip()
        year_label = normalize_space(str(row.get("year_label", "")))
        source_url = str(row.get("url", "")).strip()
        if not title or not year_label or not source_url:
            continue

        query = parse_qs(urlsplit(source_url).query)
        source_year = parse_optional_int(row.get("source_year"))
        if source_year is None:
            source_year = parse_optional_int(query.get("YEAR", [None])[0])
        source_fino = parse_optional_int(row.get("source_fino"))
        if source_fino is None:
            source_fino = parse_optional_int(query.get("FINO", [None])[0])
        meeting_name_hint = normalize_space(str(row.get("meeting_name", "") or row.get("meeting_group", ""))) or None
        if meeting_name_hint is None:
            meeting_name_hint = extract_meta_meeting_name(source_url, title)
        meta = SourceMeta(
            title=title,
            year_label=year_label,
            meeting_name_hint=meeting_name_hint,
            source_url=source_url,
            source_year=source_year,
            source_fino=source_fino,
        )
        specific_key = (year_label, title, normalize_space(meeting_name_hint or ""))
        metas.setdefault(specific_key, meta)
        fallback_key = (year_label, title, "")
        metas.setdefault(fallback_key, meta)
    return metas


def choose_source_files(downloads_dir: Path) -> list[Path]:
    preferred: dict[str, Path] = {}
    for file_path in sorted(downloads_dir.rglob("*")):
        if not file_path.is_file():
            continue

        ext = gijiroku_storage.logical_suffix(file_path)
        if ext not in {".txt", ".html", ".htm"}:
            continue

        rel_stem = gijiroku_storage.source_key(file_path, downloads_dir)
        current = preferred.get(rel_stem)
        if current is None:
            preferred[rel_stem] = file_path
            continue

        if gijiroku_storage.logical_suffix(current) != ".txt" and ext == ".txt":
            preferred[rel_stem] = file_path
    return sorted(preferred.values())


def fallback_year_label_from_path(file_path: Path, downloads_dir: Path) -> str | None:
    for parent in file_path.parents:
        if parent == downloads_dir:
            break
        label = normalize_year_label_candidate(parent.name)
        if label:
            return label

    if file_path.parent != downloads_dir:
        candidate = normalize_space(file_path.parent.name)
        return candidate or None

    return None


def build_record(file_path: Path, downloads_dir: Path, meta_map: dict[tuple[str, str, str], SourceMeta], indexed_at: str) -> MinuteRecord | None:
    ext = gijiroku_storage.logical_suffix(file_path)
    title = normalize_title(file_path)

    try:
        raw_text = read_text_auto(file_path)
    except Exception:
        return None

    content = html_to_text(raw_text) if ext in {".html", ".htm"} else raw_text.strip()
    if not content:
        return None

    fallback_year_label = fallback_year_label_from_path(file_path, downloads_dir)
    extracted_year_label = extract_year_label(content, fallback=fallback_year_label)
    if extracted_year_label is None:
        extracted_year_label = fallback_year_label or "不明"

    meeting_name = extract_meeting_name(content)
    meta = meta_map.get((extracted_year_label, title, normalize_space(meeting_name or "")))
    if meta is None:
        meta = meta_map.get((extracted_year_label, title, ""))
    year_label = meta.year_label if meta else extracted_year_label
    held_on, gregorian_year, month, day = extract_held_on(content, title, meta.source_year if meta else None)
    doc_type = classify_doc_type(title, content)
    rel_path = file_path.relative_to(downloads_dir).as_posix()
    title_terms = japanese_search_tokenizer.document_terms_text(title)
    meeting_name_terms = japanese_search_tokenizer.document_terms_text(meeting_name or "")
    content_terms = japanese_search_tokenizer.document_terms_text(content)

    return MinuteRecord(
        rel_path=rel_path,
        title=title,
        meeting_name=meeting_name,
        year_label=year_label,
        held_on=held_on,
        gregorian_year=gregorian_year,
        month=month,
        day=day,
        doc_type=doc_type,
        ext=ext,
        source_fino=meta.source_fino if meta else None,
        source_year=meta.source_year if meta else gregorian_year,
        source_url=meta.source_url if meta else None,
        content=content,
        title_terms=title_terms,
        meeting_name_terms=meeting_name_terms,
        content_terms=content_terms,
        indexed_at=indexed_at,
    )


def schema_path() -> Path:
    return Path(__file__).with_name("schema.sql")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(schema_path().read_text(encoding="utf-8"))


def ensure_db_schema(
    conn: sqlite3.Connection,
    *,
    heartbeat_callback: Callable[[], None] | None = None,
) -> None:
    # 逐次更新時は既存 DB を消さず、最小限の CREATE IF NOT EXISTS だけを流す。
    conn.executescript(INCREMENTAL_SCHEMA_SQL)
    terms_rebuilt = False
    if not minutes_terms_schema_matches(conn):
        conn.execute("DROP TABLE IF EXISTS minutes_terms")
        create_minutes_terms(conn)
        terms_rebuilt = True
    if minutes_terms_needs_rebuild(conn):
        rebuild_minutes_terms(conn, heartbeat_callback=heartbeat_callback)
        terms_rebuilt = True
    if terms_rebuilt or not minutes_fts_schema_matches(conn):
        rebuild_minutes_fts(conn, heartbeat_callback=heartbeat_callback)
    else:
        create_minutes_fts(conn)
        configure_minutes_fts(conn)


def _db_cache_size_pragma(kibibytes: int) -> str:
    return str(-max(kibibytes, 1024))


def open_sqlite_connection(path: Path, *, bulk_load: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    # 会議録 DB も逐次 upsert と検索が並行するため、WAL を基本にする。
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA synchronous={'OFF' if bulk_load else 'NORMAL'}")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute(
        f"PRAGMA cache_size={_db_cache_size_pragma(SQLITE_BULK_LOAD_CACHE_SIZE_KIB if bulk_load else SQLITE_INCREMENTAL_CACHE_SIZE_KIB)}"
    )
    if bulk_load:
        conn.execute("PRAGMA locking_mode=EXCLUSIVE")
    return conn


def checkpoint_wal(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.DatabaseError:
        pass


def incremental_db_key(output_db: Path) -> Path:
    try:
        return output_db.resolve()
    except Exception:
        return output_db


def register_incremental_flush_at_exit() -> None:
    global _ATEXIT_REGISTERED
    if _ATEXIT_REGISTERED:
        return
    atexit.register(flush_all_incremental_indexes)
    _ATEXIT_REGISTERED = True


def drop_pending_incremental_records(output_db: Path) -> None:
    _PENDING_INCREMENTAL_RECORDS.pop(incremental_db_key(output_db), None)


def flush_incremental_index(output_db: Path) -> int:
    key = incremental_db_key(output_db)
    pending = _PENDING_INCREMENTAL_RECORDS.get(key)
    if not pending:
        return 0

    output_db.parent.mkdir(parents=True, exist_ok=True)
    if not output_db.exists():
        ensure_output_db(output_db)

    with open_sqlite_connection(output_db) as conn:
        configure_minutes_fts(conn)
        for record in pending:
            upsert_record(conn, record)
        conn.commit()
    ensure_output_db_permissions(output_db)
    flushed = len(pending)
    _PENDING_INCREMENTAL_RECORDS.pop(key, None)
    return flushed


def flush_all_incremental_indexes() -> None:
    for output_db in list(_PENDING_INCREMENTAL_RECORDS.keys()):
        try:
            flush_incremental_index(output_db)
        except Exception:
            drop_pending_incremental_records(output_db)


def ensure_output_db_permissions(path: Path) -> None:
    try:
        if path.exists():
            path.chmod(0o664)
    except Exception:
        pass


def recreate_output_db(
    output_db: Path,
    *,
    heartbeat_callback: Callable[[], None] | None = None,
) -> None:
    output_db.unlink(missing_ok=True)
    with open_sqlite_connection(output_db) as conn:
        ensure_db_schema(conn, heartbeat_callback=heartbeat_callback)
        conn.commit()


def ensure_output_db(
    output_db: Path,
    *,
    heartbeat_callback: Callable[[], None] | None = None,
) -> None:
    output_db.parent.mkdir(parents=True, exist_ok=True)
    recreate = False
    if output_db.exists():
        with open_sqlite_connection(output_db) as conn:
            recreate = not minutes_table_schema_matches(conn)
    if recreate:
        recreate_output_db(output_db, heartbeat_callback=heartbeat_callback)
        ensure_output_db_permissions(output_db)
        return
    with open_sqlite_connection(output_db) as conn:
        ensure_db_schema(conn, heartbeat_callback=heartbeat_callback)
        conn.commit()
    ensure_output_db_permissions(output_db)


def prepare_incremental_index(
    output_db: Path,
    *,
    logger: Callable[[str], None] | None = None,
    context: str = "",
) -> bool:
    # 会議録取得そのものは止めず、逐次 index だけを optional 扱いにする。
    try:
        register_incremental_flush_at_exit()
        ensure_output_db(output_db)
        return True
    except Exception as exc:
        if logger is not None:
            prefix = f"{context}: " if context else ""
            logger(
                f"[WARN] {prefix}minutes.sqlite の準備に失敗したため、逐次インデックス更新を無効化します: "
                f"[{type(exc).__name__}] db={output_db} error={exc}"
            )
        return False


def upsert_record(conn: sqlite3.Connection, record: MinuteRecord) -> int:
    existing_row = conn.execute(
        "SELECT id FROM minutes WHERE rel_path = ?",
        (record.rel_path,),
    ).fetchone()
    existing_terms: tuple[str, str, str] | None = None

    params = (
        record.rel_path,
        record.title,
        record.meeting_name,
        record.year_label,
        record.held_on,
        record.gregorian_year,
        record.month,
        record.day,
        record.doc_type,
        record.ext,
        record.source_fino,
        record.source_year,
        record.source_url,
        record.content,
        record.indexed_at,
    )

    if existing_row is None:
        cur = conn.execute(
            """
            INSERT INTO minutes (
                rel_path, title, meeting_name, year_label, held_on,
                gregorian_year, month, day, doc_type, ext,
                source_fino, source_year, source_url, content, indexed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
        row_id = int(cur.lastrowid)
    else:
        row_id = int(existing_row[0])
        terms_row = conn.execute(
            "SELECT title_terms, meeting_name_terms, content_terms FROM minutes_terms WHERE id = ?",
            (row_id,),
        ).fetchone()
        if terms_row is not None:
            existing_terms = (
                str(terms_row[0] or ""),
                str(terms_row[1] or ""),
                str(terms_row[2] or ""),
            )
            delete_minutes_fts_row(
                conn,
                row_id,
                title_terms=existing_terms[0],
                meeting_name_terms=existing_terms[1],
                content_terms=existing_terms[2],
            )
        conn.execute(
            """
            UPDATE minutes
               SET rel_path = ?, title = ?, meeting_name = ?, year_label = ?, held_on = ?,
                   gregorian_year = ?, month = ?, day = ?, doc_type = ?, ext = ?,
                   source_fino = ?, source_year = ?, source_url = ?, content = ?, indexed_at = ?
             WHERE id = ?
            """,
            params + (row_id,),
        )

    if record.doc_type == "minutes":
        conn.execute(
            """
            INSERT INTO minutes_terms (id, title_terms, meeting_name_terms, content_terms)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title_terms = excluded.title_terms,
                meeting_name_terms = excluded.meeting_name_terms,
                content_terms = excluded.content_terms
            """,
            (row_id, record.title_terms, record.meeting_name_terms, record.content_terms),
        )
        insert_minutes_fts_row(
            conn,
            row_id,
            title_terms=record.title_terms,
            meeting_name_terms=record.meeting_name_terms,
            content_terms=record.content_terms,
        )
    elif existing_terms is not None:
        conn.execute("DELETE FROM minutes_terms WHERE id = ?", (row_id,))
    return row_id


def upsert_source_file(
    output_db: Path,
    downloads_dir: Path,
    source_file: Path,
    *,
    meta_map: dict[tuple[str, str, str], SourceMeta] | None = None,
    index_json: Path | None = None,
    indexed_at: str | None = None,
) -> bool:
    if not source_file.exists():
        return False

    if meta_map is None:
        meta_map = parse_source_meta(index_json) if index_json is not None else {}
    if indexed_at is None:
        indexed_at = datetime.now(timezone.utc).isoformat()

    record = build_record(source_file, downloads_dir, meta_map, indexed_at)
    if record is None:
        return False

    register_incremental_flush_at_exit()
    key = incremental_db_key(output_db)
    pending = _PENDING_INCREMENTAL_RECORDS.setdefault(key, [])
    pending.append(record)
    if len(pending) >= INCREMENTAL_COMMIT_BATCH_SIZE:
        flush_incremental_index(output_db)
    return True


def best_effort_upsert_source_file(
    output_db: Path,
    downloads_dir: Path,
    source_file: Path,
    *,
    meta_map: dict[tuple[str, str, str], SourceMeta] | None = None,
    index_json: Path | None = None,
    indexed_at: str | None = None,
    logger: Callable[[str], None] | None = None,
    context: str = "",
) -> str:
    # 増分 index の失敗で自治体スクレイパ全体を落とさないためのラッパー。
    try:
        updated = upsert_source_file(
            output_db,
            downloads_dir,
            source_file,
            meta_map=meta_map,
            index_json=index_json,
            indexed_at=indexed_at,
        )
    except Exception as exc:
        drop_pending_incremental_records(output_db)
        if logger is not None:
            prefix = f"{context}: " if context else ""
            logger(
                f"[WARN] {prefix}minutes.sqlite への逐次反映に失敗しました: "
                f"[{type(exc).__name__}] db={output_db} source={source_file} error={exc}"
            )
        return "error"
    return "ok" if updated else "skipped"


def finalize_incremental_index(
    output_db: Path,
    *,
    logger: Callable[[str], None] | None = None,
    context: str = "",
) -> bool:
    try:
        if flush_incremental_index(output_db):
            with open_sqlite_connection(output_db) as conn:
                configure_minutes_fts(conn)
                merge_minutes_fts(conn)
                conn.commit()
        return True
    except Exception as exc:
        drop_pending_incremental_records(output_db)
        if logger is not None:
            prefix = f"{context}: " if context else ""
            logger(
                f"[WARN] {prefix}minutes.sqlite の増分バッチ flush に失敗しました: "
                f"[{type(exc).__name__}] db={output_db} error={exc}"
            )
        return False


def existing_rel_paths(output_db: Path) -> set[str]:
    if not output_db.exists():
        return set()
    with open_sqlite_connection(output_db) as conn:
        return {str(row[0]) for row in conn.execute("SELECT rel_path FROM minutes")}


def backfill_missing_rows(
    downloads_dir: Path,
    index_json: Path,
    output_db: Path,
    *,
    progress_callback: Callable[[dict[str, int | str]], None] | None = None,
    heartbeat_callback: Callable[[], None] | None = None,
) -> dict[str, int]:
    # 既存 DB を壊さず、rel_path で未登録の会議録だけを後追いで差し込む。
    source_files = choose_source_files(downloads_dir)
    if not source_files:
        return {"added": 0, "existing": 0, "skipped": 0, "total_files": 0}

    total_files = len(source_files)
    if progress_callback is not None:
        progress_callback(
            {
                "stage": "prepare_db",
                "processed": 0,
                "total_files": total_files,
                "added": 0,
                "existing": 0,
                "skipped": 0,
            }
        )

    last_heartbeat_at = emit_heartbeat(heartbeat_callback, 0.0, force=True)
    ensure_output_db(output_db, heartbeat_callback=heartbeat_callback)
    last_heartbeat_at = emit_heartbeat(heartbeat_callback, last_heartbeat_at)
    indexed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    meta_map = parse_source_meta(index_json)
    existing = existing_rel_paths(output_db)
    last_heartbeat_at = emit_heartbeat(heartbeat_callback, last_heartbeat_at)
    added = 0
    existing_count = 0
    skipped = 0
    processed = 0
    last_report_at = time.monotonic()

    if progress_callback is not None:
        progress_callback(
            {
                "stage": "indexing",
                "processed": 0,
                "total_files": total_files,
                "added": 0,
                "existing": 0,
                "skipped": 0,
            }
        )
    last_heartbeat_at = emit_heartbeat(heartbeat_callback, last_heartbeat_at)

    with open_sqlite_connection(output_db) as conn:
        # prepare_db 済みなので、ここでは行追加だけにして schema lock の競合を減らす。
        for file_path in source_files:
            rel_path = file_path.relative_to(downloads_dir).as_posix()
            if rel_path in existing:
                existing_count += 1
                processed += 1
                if progress_callback is not None:
                    now = time.monotonic()
                    if processed == total_files or processed % 250 == 0 or (now - last_report_at) >= 5.0:
                        last_report_at = now
                        progress_callback(
                            {
                                "stage": "indexing",
                                "processed": processed,
                                "total_files": total_files,
                                "added": added,
                                "existing": existing_count,
                                "skipped": skipped,
                            }
                        )
                        last_heartbeat_at = emit_heartbeat(heartbeat_callback, last_heartbeat_at, force=True)
                continue
            record = build_record(file_path, downloads_dir, meta_map, indexed_at)
            if record is None:
                skipped += 1
                processed += 1
                if progress_callback is not None:
                    now = time.monotonic()
                    if processed == total_files or processed % 250 == 0 or (now - last_report_at) >= 5.0:
                        last_report_at = now
                        progress_callback(
                            {
                                "stage": "indexing",
                                "processed": processed,
                                "total_files": total_files,
                                "added": added,
                                "existing": existing_count,
                                "skipped": skipped,
                            }
                        )
                        last_heartbeat_at = emit_heartbeat(heartbeat_callback, last_heartbeat_at, force=True)
                continue
            upsert_record(conn, record)
            existing.add(rel_path)
            added += 1
            processed += 1
            if progress_callback is not None:
                now = time.monotonic()
                if processed == total_files or processed % 250 == 0 or (now - last_report_at) >= 5.0:
                    last_report_at = now
                    progress_callback(
                        {
                            "stage": "indexing",
                            "processed": processed,
                            "total_files": total_files,
                            "added": added,
                            "existing": existing_count,
                            "skipped": skipped,
                        }
                    )
                    last_heartbeat_at = emit_heartbeat(heartbeat_callback, last_heartbeat_at, force=True)
        conn.commit()
    emit_heartbeat(heartbeat_callback, last_heartbeat_at, force=True)
    ensure_output_db_permissions(output_db)

    return {
        "added": added,
        "existing": existing_count,
        "skipped": skipped,
        "total_files": total_files,
    }


def build_index(
    downloads_dir: Path,
    index_json: Path,
    output_db: Path,
    *,
    progress_callback: Callable[[dict[str, int | str]], None] | None = None,
    heartbeat_callback: Callable[[], None] | None = None,
) -> tuple[int, int]:
    if not downloads_dir.exists():
        raise FileNotFoundError(f"downloads dir not found: {downloads_dir}")

    output_db.parent.mkdir(parents=True, exist_ok=True)
    meta_map = parse_source_meta(index_json)
    source_files = choose_source_files(downloads_dir)
    total_files = len(source_files)
    temp_fd, temp_name = tempfile.mkstemp(
        prefix=f"{output_db.name}.",
        suffix=".tmp",
        dir=str(output_db.parent),
    )
    os.close(temp_fd)
    temp_db = Path(temp_name)

    try:
        conn = open_sqlite_connection(temp_db, bulk_load=True)
        init_db(conn)
        last_heartbeat_at = emit_heartbeat(heartbeat_callback, 0.0, force=True)

        indexed = 0
        skipped = 0
        processed = 0
        indexed_at = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        last_report_at = time.monotonic()

        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "prepare_db",
                    "processed": 0,
                    "total_files": total_files,
                    "added": 0,
                    "existing": 0,
                    "skipped": 0,
                }
            )
            progress_callback(
                {
                    "stage": "indexing",
                    "processed": 0,
                    "total_files": total_files,
                    "added": 0,
                    "existing": 0,
                    "skipped": 0,
                }
            )
        last_heartbeat_at = emit_heartbeat(heartbeat_callback, last_heartbeat_at)

        for file_path in source_files:
            record = build_record(file_path, downloads_dir, meta_map, indexed_at)
            if record is None:
                skipped += 1
                processed += 1
                if progress_callback is not None:
                    now = time.monotonic()
                    if processed == total_files or processed % 250 == 0 or (now - last_report_at) >= 5.0:
                        last_report_at = now
                        progress_callback(
                            {
                                "stage": "indexing",
                                "processed": processed,
                                "total_files": total_files,
                                "added": indexed,
                                "existing": 0,
                                "skipped": skipped,
                            }
                        )
                        last_heartbeat_at = emit_heartbeat(heartbeat_callback, last_heartbeat_at, force=True)
                continue

            cur.execute(
                """
                INSERT INTO minutes (
                    rel_path, title, meeting_name, year_label, held_on,
                    gregorian_year, month, day, doc_type, ext,
                    source_fino, source_year, source_url, content, indexed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.rel_path,
                    record.title,
                    record.meeting_name,
                    record.year_label,
                    record.held_on,
                    record.gregorian_year,
                    record.month,
                    record.day,
                    record.doc_type,
                    record.ext,
                    record.source_fino,
                    record.source_year,
                    record.source_url,
                    record.content,
                    record.indexed_at,
                ),
            )
            row_id = int(cur.lastrowid)
            if record.doc_type == "minutes":
                cur.execute(
                    """
                    INSERT INTO minutes_terms (id, title_terms, meeting_name_terms, content_terms)
                    VALUES (?, ?, ?, ?)
                    """,
                    (row_id, record.title_terms, record.meeting_name_terms, record.content_terms),
                )
            indexed += 1
            processed += 1
            if progress_callback is not None:
                now = time.monotonic()
                if processed == total_files or processed % 250 == 0 or (now - last_report_at) >= 5.0:
                    last_report_at = now
                    progress_callback(
                        {
                            "stage": "indexing",
                            "processed": processed,
                            "total_files": total_files,
                            "added": indexed,
                            "existing": 0,
                            "skipped": skipped,
                        }
                    )
                    last_heartbeat_at = emit_heartbeat(heartbeat_callback, last_heartbeat_at, force=True)

        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "rebuild_fts",
                    "processed": processed,
                    "total_files": total_files,
                    "added": indexed,
                    "existing": 0,
                    "skipped": skipped,
                }
            )
        rebuild_minutes_fts(conn, heartbeat_callback=heartbeat_callback)
        optimize_minutes_fts(conn)
        conn.commit()
        checkpoint_wal(conn)
        conn.close()
        emit_heartbeat(heartbeat_callback, last_heartbeat_at, force=True)
        temp_db.replace(output_db)
        ensure_output_db_permissions(output_db)
        return indexed, skipped
    except Exception:
        try:
            temp_db.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def main() -> int:
    args = parse_args()
    indexed, skipped = build_index(args.downloads_dir, args.index_json, args.output_db)
    print(f"[DONE] indexed={indexed} skipped={skipped} db={args.output_db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
