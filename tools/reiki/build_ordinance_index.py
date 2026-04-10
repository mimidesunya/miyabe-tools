#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import html
import os
import re
import sqlite3
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).parent))
sys.path.append(str(Path(__file__).resolve().parent.parent.parent / "lib" / "python"))
import reiki_io
import reiki_targets
import japanese_search_tokenizer


DATE_PATTERN = re.compile(r'<div class="law-date">.*?\((\d{4}-\d{2}-\d{2})\)</div>', re.IGNORECASE | re.DOTALL)
TITLE_PATTERN = re.compile(r'<div class="law-title">([^<]+)</div>', re.IGNORECASE)
NUMBER_PATTERN = re.compile(r'<div class="law-number">([^<]+)</div>', re.IGNORECASE)
TAG_PATTERN = re.compile(r"<[^>]+>")
SPACE_PATTERN = re.compile(r"[ \t\u3000]+")
LINEBREAK_PATTERN = re.compile(r"\n{3,}")
INCREMENTAL_COMMIT_BATCH_SIZE = 25
SQLITE_INCREMENTAL_CACHE_SIZE_KIB = 32 * 1024
SQLITE_BULK_LOAD_CACHE_SIZE_KIB = 128 * 1024
FTS_RUNTIME_AUTOMERGE = 8
FTS_RUNTIME_CRISISMERGE = 64
FTS_RUNTIME_MERGE_PAGES = 1000
ORDINANCES_TERMS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ordinances_terms (
    id INTEGER PRIMARY KEY,
    title_terms TEXT NOT NULL,
    reading_terms TEXT NOT NULL,
    content_terms TEXT NOT NULL,
    department_terms TEXT NOT NULL,
    combined_reason_terms TEXT NOT NULL,
    reason_terms TEXT NOT NULL,
    secondary_terms TEXT NOT NULL,
    lens_terms TEXT NOT NULL,
    taxonomy_terms TEXT NOT NULL,
    FOREIGN KEY(id) REFERENCES ordinances(id) ON DELETE CASCADE
);
"""
ORDINANCES_FTS_TABLE_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS ordinances_fts USING fts5(
    title_terms,
    reading_terms,
    content_terms,
    department_terms,
    combined_reason_terms,
    reason_terms,
    secondary_terms,
    lens_terms,
    taxonomy_terms,
    content = 'ordinances_terms',
    content_rowid = 'id',
    tokenize = 'unicode61'
)
"""

SCHEMA_SQL = """
CREATE TABLE ordinances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    reading_kana TEXT,
    sortable_kana TEXT,
    primary_class TEXT,
    secondary_tags TEXT,
    necessity_score INTEGER,
    fiscal_impact_score REAL,
    regulatory_burden_score REAL,
    policy_effectiveness_score REAL,
    lens_tags TEXT,
    lens_a_stance TEXT,
    lens_b_stance TEXT,
    combined_stance TEXT,
    combined_reason TEXT,
    document_type TEXT,
    responsible_department TEXT,
    reason TEXT,
    enactment_date TEXT,
    analyzed_at TEXT,
    updated_at TEXT,
    source_url TEXT,
    source_file TEXT,
    taxonomy_path TEXT,
    taxonomy_paths TEXT,
    content_text TEXT NOT NULL,
    content_length INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE ordinances_terms (
    id INTEGER PRIMARY KEY,
    title_terms TEXT NOT NULL,
    reading_terms TEXT NOT NULL,
    content_terms TEXT NOT NULL,
    department_terms TEXT NOT NULL,
    combined_reason_terms TEXT NOT NULL,
    reason_terms TEXT NOT NULL,
    secondary_terms TEXT NOT NULL,
    lens_terms TEXT NOT NULL,
    taxonomy_terms TEXT NOT NULL,
    FOREIGN KEY(id) REFERENCES ordinances(id) ON DELETE CASCADE
);

CREATE INDEX idx_ordinances_sortable_kana ON ordinances(sortable_kana);
CREATE INDEX idx_ordinances_class ON ordinances(primary_class);
CREATE INDEX idx_ordinances_necessity ON ordinances(necessity_score);
CREATE INDEX idx_ordinances_date ON ordinances(enactment_date);
CREATE INDEX idx_ordinances_combined_stance ON ordinances(combined_stance);
CREATE INDEX idx_ordinances_document_type ON ordinances(document_type);

CREATE VIRTUAL TABLE ordinances_fts USING fts5(
    title_terms,
    reading_terms,
    content_terms,
    department_terms,
    combined_reason_terms,
    reason_terms,
    secondary_terms,
    lens_terms,
    taxonomy_terms,
    content = 'ordinances_terms',
    content_rowid = 'id',
    tokenize = 'unicode61'
);
"""

INCREMENTAL_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ordinances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    reading_kana TEXT,
    sortable_kana TEXT,
    primary_class TEXT,
    secondary_tags TEXT,
    necessity_score INTEGER,
    fiscal_impact_score REAL,
    regulatory_burden_score REAL,
    policy_effectiveness_score REAL,
    lens_tags TEXT,
    lens_a_stance TEXT,
    lens_b_stance TEXT,
    combined_stance TEXT,
    combined_reason TEXT,
    document_type TEXT,
    responsible_department TEXT,
    reason TEXT,
    enactment_date TEXT,
    analyzed_at TEXT,
    updated_at TEXT,
    source_url TEXT,
    source_file TEXT,
    taxonomy_path TEXT,
    taxonomy_paths TEXT,
    content_text TEXT NOT NULL,
    content_length INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ordinances_terms (
    id INTEGER PRIMARY KEY,
    title_terms TEXT NOT NULL,
    reading_terms TEXT NOT NULL,
    content_terms TEXT NOT NULL,
    department_terms TEXT NOT NULL,
    combined_reason_terms TEXT NOT NULL,
    reason_terms TEXT NOT NULL,
    secondary_terms TEXT NOT NULL,
    lens_terms TEXT NOT NULL,
    taxonomy_terms TEXT NOT NULL,
    FOREIGN KEY(id) REFERENCES ordinances(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ordinances_sortable_kana ON ordinances(sortable_kana);
CREATE INDEX IF NOT EXISTS idx_ordinances_class ON ordinances(primary_class);
CREATE INDEX IF NOT EXISTS idx_ordinances_necessity ON ordinances(necessity_score);
CREATE INDEX IF NOT EXISTS idx_ordinances_date ON ordinances(enactment_date);
CREATE INDEX IF NOT EXISTS idx_ordinances_combined_stance ON ordinances(combined_stance);
CREATE INDEX IF NOT EXISTS idx_ordinances_document_type ON ordinances(document_type);

CREATE VIRTUAL TABLE IF NOT EXISTS ordinances_fts USING fts5(
    title_terms,
    reading_terms,
    content_terms,
    department_terms,
    combined_reason_terms,
    reason_terms,
    secondary_terms,
    lens_terms,
    taxonomy_terms,
    content = 'ordinances_terms',
    content_rowid = 'id',
    tokenize = 'unicode61'
);
"""

REQUIRED_ORDINANCE_COLUMNS = {
    "id",
    "filename",
    "title",
    "reading_kana",
    "sortable_kana",
    "primary_class",
    "secondary_tags",
    "necessity_score",
    "fiscal_impact_score",
    "regulatory_burden_score",
    "policy_effectiveness_score",
    "lens_tags",
    "lens_a_stance",
    "lens_b_stance",
    "combined_stance",
    "combined_reason",
    "document_type",
    "responsible_department",
    "reason",
    "enactment_date",
    "analyzed_at",
    "updated_at",
    "source_url",
    "source_file",
    "taxonomy_path",
    "taxonomy_paths",
    "content_text",
    "content_length",
}
REQUIRED_ORDINANCES_TERMS_COLUMNS = [
    "id",
    "title_terms",
    "reading_terms",
    "content_terms",
    "department_terms",
    "combined_reason_terms",
    "reason_terms",
    "secondary_terms",
    "lens_terms",
    "taxonomy_terms",
]

REQUIRED_ORDINANCE_FTS_COLUMNS = [
    "title_terms",
    "reading_terms",
    "content_terms",
    "department_terms",
    "combined_reason_terms",
    "reason_terms",
    "secondary_terms",
    "lens_terms",
    "taxonomy_terms",
]


_PENDING_INCREMENTAL_RECORDS: dict[Path, list[dict[str, Any]]] = {}
_ATEXIT_REGISTERED = False


def parse_args() -> argparse.Namespace:
    default_slug = reiki_targets.default_slug_for_system()
    parser = argparse.ArgumentParser(
        description="ダウンロード済みの例規 HTML / JSON / manifest を走査して ordinances.sqlite を再構築します。"
    )
    parser.add_argument("--slug", default=default_slug, help="自治体 slug")
    parser.add_argument("--clean-html-dir", type=Path, default=None, help="整形 HTML ディレクトリ")
    parser.add_argument("--classification-dir", type=Path, default=None, help="分類 JSON ディレクトリ")
    parser.add_argument("--markdown-dir", type=Path, default=None, help="Markdown ディレクトリ")
    parser.add_argument("--manifest-json", type=Path, default=None, help="source_manifest.json(.gz) のパス")
    parser.add_argument("--output-db", type=Path, default=None, help="出力 SQLite パス")
    args = parser.parse_args()

    target = reiki_targets.load_reiki_target(str(args.slug).strip())
    args.slug = str(target["slug"]).strip()
    args.clean_html_dir = args.clean_html_dir or Path(target["html_dir"])
    args.classification_dir = args.classification_dir or Path(target["classification_dir"])
    args.markdown_dir = args.markdown_dir or Path(target["markdown_dir"])
    args.manifest_json = args.manifest_json or (Path(target["work_root"]) / "source_manifest.json.gz")
    args.output_db = args.output_db or Path(target["db_path"])
    return args


def _db_cache_size_pragma(kibibytes: int) -> str:
    return str(-max(kibibytes, 1024))


def open_sqlite_connection(path: Path, *, bulk_load: bool = False) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30)
    # 逐次 upsert 中でも検索リクエストを止めにくいよう、WAL を基本にする。
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(f"PRAGMA synchronous={'OFF' if bulk_load else 'NORMAL'}")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.execute(
        f"PRAGMA cache_size={_db_cache_size_pragma(SQLITE_BULK_LOAD_CACHE_SIZE_KIB if bulk_load else SQLITE_INCREMENTAL_CACHE_SIZE_KIB)}"
    )
    if bulk_load:
        connection.execute("PRAGMA locking_mode=EXCLUSIVE")
    return connection


def checkpoint_wal(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
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

    with open_sqlite_connection(output_db) as connection:
        configure_ordinances_fts(connection)
        for record in pending:
            upsert_record(connection, record)
        connection.commit()
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


def strip_municipality_name_kana_suffix(name: str, name_kana: str) -> str:
    normalized_name = str(name).strip()
    normalized_kana = normalize_space(name_kana).replace(" ", "")
    if normalized_name == "" or normalized_kana == "":
        return ""
    if normalized_name == "北海道":
        return ""

    suffix_map = (
        ("都", ("と",)),
        ("道", ("どう",)),
        ("府", ("ふ",)),
        ("県", ("けん",)),
        ("市", ("し",)),
        ("区", ("く",)),
        ("町", ("ちょう", "まち")),
        ("村", ("むら", "そん")),
    )
    for kanji_suffix, kana_suffixes in suffix_map:
        if not normalized_name.endswith(kanji_suffix):
            continue
        for kana_suffix in kana_suffixes:
            if normalized_kana.endswith(kana_suffix) and len(normalized_kana) > len(kana_suffix):
                return normalized_kana[: -len(kana_suffix)]
    return ""


def municipality_sortable_prefixes(slug: str) -> list[str]:
    target = reiki_targets.load_reiki_target(str(slug).strip())
    name = str(target.get("name", "")).strip()
    name_kana = normalize_space(str(target.get("name_kana", "")).strip()).replace(" ", "")
    prefixes: list[str] = []
    for candidate in (
        name_kana,
        strip_municipality_name_kana_suffix(name, name_kana),
    ):
        if candidate and candidate not in prefixes:
            prefixes.append(candidate)
    return prefixes


def decode_html_text(value: object) -> str:
    return html.unescape(str(value or "")).strip()


def normalize_space(value: str) -> str:
    return SPACE_PATTERN.sub(" ", value).strip()


def html_to_text(value: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", "", value, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(div|p|li|tr|table|section|article|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = TAG_PATTERN.sub("", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = LINEBREAK_PATTERN.sub("\n\n", text)
    return text.strip()


def markdown_to_text(value: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", value)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^[#>*`\-\+\s]+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = LINEBREAK_PATTERN.sub("\n\n", text)
    return text.strip()


def logical_key_from_path(path: Path, root: Path) -> str:
    logical = reiki_io.logical_path(path)
    relative = logical.relative_to(root)
    return relative.with_suffix("").as_posix()


def logical_key_from_string(value: str) -> str:
    candidate = Path(str(value).replace("\\", "/"))
    logical = reiki_io.logical_path(candidate)
    return logical.with_suffix("").as_posix()


def collect_preferred_files(root: Path, suffixes: set[str]) -> dict[str, Path]:
    preferred: dict[str, Path] = {}
    if not root.exists():
        return preferred

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        logical = reiki_io.logical_path(path)
        if logical.suffix.lower() not in suffixes:
            continue

        key = logical_key_from_path(path, root)
        current = preferred.get(key)
        if current is None:
            preferred[key] = path
            continue
        if current.suffix.lower() == ".gz" and path.suffix.lower() != ".gz":
            preferred[key] = path

    return preferred


def load_manifest_index(path: Path) -> dict[str, dict[str, Any]]:
    rows = reiki_io.load_json(path, [])
    if not isinstance(rows, list):
        return {}

    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_file = str(row.get("source_file") or row.get("stored_source_file") or "").strip()
        if source_file == "":
            continue
        key = logical_key_from_string(source_file)
        if key == "":
            continue
        index[key] = row
        index.setdefault(Path(key).name, row)
    return index


def build_alias_map(files: dict[str, Path]) -> dict[str, Path]:
    alias: dict[str, Path] = {}
    for key, path in files.items():
        alias[key] = path
        alias.setdefault(Path(key).name, path)
    return alias


def extract_title_from_html(html_content: str, fallback: str) -> str:
    match = TITLE_PATTERN.search(html_content)
    if match:
        title = decode_html_text(match.group(1))
        if title:
            return title
    return fallback


def extract_number_from_html(html_content: str) -> str:
    match = NUMBER_PATTERN.search(html_content)
    if not match:
        return ""
    return decode_html_text(match.group(1))


def extract_date_from_html(html_content: str) -> str:
    match = DATE_PATTERN.search(html_content)
    if not match:
        return ""
    return match.group(1)


def join_strings(value: object) -> str:
    if isinstance(value, list):
        return ",".join(normalize_space(decode_html_text(item)) for item in value if normalize_space(decode_html_text(item)))
    return normalize_space(decode_html_text(value))


def normalize_kana(value: str, prefixes: list[str]) -> str:
    normalized = value.strip()
    for prefix in prefixes:
        if prefix and normalized.startswith(prefix):
            return normalized[len(prefix):].strip()
    return normalized


def safe_int(value: object, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def safe_float(value: object, default: float) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return default


def normalize_document_type(value: str) -> str:
    text = normalize_space(value)
    if text in {"条例", "規則", "規程", "要綱"}:
        return text
    return "その他"


def table_columns(connection: sqlite3.Connection, table_name: str) -> list[str]:
    try:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    except sqlite3.DatabaseError:
        return []
    return [str(row[1]) for row in rows if len(row) >= 2]


def table_sql(connection: sqlite3.Connection, table_name: str) -> str:
    try:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
            (table_name,),
        ).fetchone()
    except sqlite3.DatabaseError:
        return ""
    if row is None or not row[0]:
        return ""
    return str(row[0])


def fts_uses_external_content(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    content_table: str,
    content_rowid: str,
) -> bool:
    sql = re.sub(r"\s+", "", table_sql(connection, table_name).lower())
    if not sql:
        return False
    return (
        f"content='{content_table}'" in sql
        and f"content_rowid='{content_rowid}'" in sql
    )


def ordinance_fts_columns(connection: sqlite3.Connection) -> list[str]:
    return table_columns(connection, "ordinances_fts")


def ordinance_terms_columns(connection: sqlite3.Connection) -> list[str]:
    return table_columns(connection, "ordinances_terms")


def ordinance_fts_schema_matches(connection: sqlite3.Connection) -> bool:
    return (
        ordinance_fts_columns(connection) == REQUIRED_ORDINANCE_FTS_COLUMNS
        and fts_uses_external_content(
            connection,
            table_name="ordinances_fts",
            content_table="ordinances_terms",
            content_rowid="id",
        )
    )


def ordinance_terms_schema_matches(connection: sqlite3.Connection) -> bool:
    return ordinance_terms_columns(connection) == REQUIRED_ORDINANCES_TERMS_COLUMNS


def create_ordinances_terms(connection: sqlite3.Connection) -> None:
    connection.execute(ORDINANCES_TERMS_TABLE_SQL)


def create_ordinances_fts(connection: sqlite3.Connection) -> None:
    connection.execute(ORDINANCES_FTS_TABLE_SQL)


def configure_ordinances_fts(connection: sqlite3.Connection) -> None:
    connection.execute("INSERT INTO ordinances_fts(ordinances_fts, rank) VALUES('automerge', ?)", (FTS_RUNTIME_AUTOMERGE,))
    connection.execute("INSERT INTO ordinances_fts(ordinances_fts, rank) VALUES('crisismerge', ?)", (FTS_RUNTIME_CRISISMERGE,))


def merge_ordinances_fts(connection: sqlite3.Connection, *, pages: int = FTS_RUNTIME_MERGE_PAGES) -> None:
    connection.execute("INSERT INTO ordinances_fts(ordinances_fts, rank) VALUES('merge', ?)", (pages,))


def optimize_ordinances_fts(connection: sqlite3.Connection) -> None:
    connection.execute("INSERT INTO ordinances_fts(ordinances_fts) VALUES('optimize')")


def delete_ordinances_fts_row(connection: sqlite3.Connection, row_id: int, record: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO ordinances_fts(
            ordinances_fts, rowid, title_terms, reading_terms, content_terms, department_terms,
            combined_reason_terms, reason_terms, secondary_terms, lens_terms, taxonomy_terms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "delete",
            row_id,
            record["title_terms"],
            record["reading_terms"],
            record["content_terms"],
            record["department_terms"],
            record["combined_reason_terms"],
            record["reason_terms"],
            record["secondary_terms"],
            record["lens_terms"],
            record["taxonomy_terms"],
        ),
    )


def insert_ordinances_fts_row(connection: sqlite3.Connection, row_id: int, record: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO ordinances_fts(
            rowid, title_terms, reading_terms, content_terms, department_terms,
            combined_reason_terms, reason_terms, secondary_terms, lens_terms, taxonomy_terms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row_id,
            record["title_terms"],
            record["reading_terms"],
            record["content_terms"],
            record["department_terms"],
            record["combined_reason_terms"],
            record["reason_terms"],
            record["secondary_terms"],
            record["lens_terms"],
            record["taxonomy_terms"],
        ),
    )


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


def ordinance_search_terms_text(value: object) -> str:
    return japanese_search_tokenizer.document_terms_text(normalize_space(decode_html_text(value)))


def rebuild_ordinances_terms(
    connection: sqlite3.Connection,
    *,
    heartbeat_callback: Callable[[], None] | None = None,
) -> None:
    connection.execute("DELETE FROM ordinances_terms")
    last_heartbeat_at = emit_heartbeat(heartbeat_callback, 0.0, force=True)
    processed = 0
    for row in connection.execute(
        """
        SELECT
            id,
            title,
            reading_kana,
            content_text,
            responsible_department,
            combined_reason,
            reason,
            secondary_tags,
            lens_tags,
            taxonomy_path
        FROM ordinances
        """
    ):
        (
            row_id,
            title,
            reading_kana,
            content_text,
            responsible_department,
            combined_reason,
            reason,
            secondary_tags,
            lens_tags,
            taxonomy_path,
        ) = row
        connection.execute(
            """
            INSERT INTO ordinances_terms (
                id, title_terms, reading_terms, content_terms, department_terms,
                combined_reason_terms, reason_terms, secondary_terms, lens_terms, taxonomy_terms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(row_id),
                ordinance_search_terms_text(title),
                ordinance_search_terms_text(reading_kana),
                ordinance_search_terms_text(content_text),
                ordinance_search_terms_text(responsible_department),
                ordinance_search_terms_text(combined_reason),
                ordinance_search_terms_text(reason),
                ordinance_search_terms_text(secondary_tags),
                ordinance_search_terms_text(lens_tags),
                ordinance_search_terms_text(taxonomy_path),
            ),
        )
        processed += 1
        if processed % 250 == 0:
            last_heartbeat_at = emit_heartbeat(heartbeat_callback, last_heartbeat_at)
    emit_heartbeat(heartbeat_callback, last_heartbeat_at, force=True)


def ordinances_terms_needs_rebuild(connection: sqlite3.Connection) -> bool:
    try:
        ordinances_count = int(connection.execute("SELECT COUNT(*) FROM ordinances").fetchone()[0])
        terms_count = int(connection.execute("SELECT COUNT(*) FROM ordinances_terms").fetchone()[0])
    except Exception:
        return True
    return ordinances_count != terms_count


def rebuild_ordinances_fts(
    connection: sqlite3.Connection,
    *,
    heartbeat_callback: Callable[[], None] | None = None,
) -> None:
    connection.execute("DROP TABLE IF EXISTS ordinances_fts")
    create_ordinances_fts(connection)
    emit_heartbeat(heartbeat_callback, 0.0, force=True)
    connection.execute("INSERT INTO ordinances_fts(ordinances_fts) VALUES('rebuild')")
    configure_ordinances_fts(connection)
    emit_heartbeat(heartbeat_callback, 0.0, force=True)


def detect_document_type(title: str, number: str) -> str:
    for candidate in (number, title):
        if "条例" in candidate:
            return "条例"
        if "規則" in candidate:
            return "規則"
        if "規程" in candidate or "訓令" in candidate:
            return "規程"
        if "要綱" in candidate:
            return "要綱"
    return "その他"


def record_updated_at(*paths: Path | None) -> str:
    mtimes = [path.stat().st_mtime for path in paths if path is not None and path.exists()]
    if not mtimes:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return datetime.fromtimestamp(max(mtimes), timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def build_record(
    key: str,
    html_path: Path,
    markdown_path: Path | None,
    classification_path: Path | None,
    manifest: dict[str, Any] | None,
    prefixes: list[str],
) -> dict[str, Any] | None:
    html_content = reiki_io.read_text_auto(html_path)
    content_text = html_to_text(html_content)
    if content_text == "" and markdown_path is not None and markdown_path.exists():
        content_text = markdown_to_text(reiki_io.read_text_auto(markdown_path))
    if content_text == "":
        return None

    classification = reiki_io.load_json(classification_path, {}) if classification_path is not None else {}
    if not isinstance(classification, dict):
        classification = {}
    manifest = manifest if isinstance(manifest, dict) else {}

    fallback_title = normalize_space(str(manifest.get("title", "")).strip()) or Path(key).name
    title = normalize_space(
        decode_html_text(classification.get("title", ""))
        or extract_title_from_html(html_content, fallback_title)
    )
    if title == "":
        title = Path(key).name

    number = normalize_space(
        decode_html_text(classification.get("number", ""))
        or extract_number_from_html(html_content)
        or decode_html_text(manifest.get("number", ""))
    )
    reading_kana = normalize_space(decode_html_text(classification.get("readingKana", ""))) or title
    sortable_kana = normalize_kana(reading_kana, prefixes)

    lens_eval = classification.get("lensEvaluation", {})
    if not isinstance(lens_eval, dict):
        lens_eval = {}
    lens_a = lens_eval.get("lensA", {})
    if not isinstance(lens_a, dict):
        lens_a = {}
    lens_b = lens_eval.get("lensB", {})
    if not isinstance(lens_b, dict):
        lens_b = {}
    combined = lens_eval.get("combined", {})
    if not isinstance(combined, dict):
        combined = {}

    document_type = normalize_document_type(str(classification.get("documentType", "")).strip())
    if document_type == "その他":
        document_type = detect_document_type(title, number)

    title_terms = japanese_search_tokenizer.document_terms_text(title)
    reading_terms = japanese_search_tokenizer.document_terms_text(reading_kana)
    content_terms = japanese_search_tokenizer.document_terms_text(content_text)
    department_terms = japanese_search_tokenizer.document_terms_text(
        normalize_space(str(classification.get("responsibleDepartment", "")).strip())
    )
    combined_reason_terms = japanese_search_tokenizer.document_terms_text(
        normalize_space(str(combined.get("reason", "")).strip())
    )
    reason_terms = japanese_search_tokenizer.document_terms_text(
        normalize_space(str(classification.get("reason", "")).strip())
    )
    secondary_terms = japanese_search_tokenizer.document_terms_text(
        join_strings(classification.get("secondaryTags", []))
    )
    lens_terms = japanese_search_tokenizer.document_terms_text(
        join_strings(classification.get("lensTags", []))
    )
    taxonomy_terms = japanese_search_tokenizer.document_terms_text(
        normalize_space(str(manifest.get("taxonomy_path", "")).strip())
    )

    return {
        "filename": key,
        "title": title,
        "reading_kana": reading_kana,
        "sortable_kana": sortable_kana,
        "primary_class": normalize_space(str(classification.get("primaryClass", "")).strip()),
        "secondary_tags": join_strings(classification.get("secondaryTags", [])),
        "necessity_score": safe_int(classification.get("necessityScore", -1), -1),
        "fiscal_impact_score": safe_float(classification.get("fiscalImpactScore", 0.0), 0.0),
        "regulatory_burden_score": safe_float(classification.get("regulatoryBurdenScore", 0.0), 0.0),
        "policy_effectiveness_score": safe_float(classification.get("policyEffectivenessScore", 0.0), 0.0),
        "lens_tags": join_strings(classification.get("lensTags", [])),
        "lens_a_stance": normalize_space(str(lens_a.get("stance", "")).strip()),
        "lens_b_stance": normalize_space(str(lens_b.get("stance", "")).strip()),
        "combined_stance": normalize_space(str(combined.get("stance", "")).strip()),
        "combined_reason": normalize_space(str(combined.get("reason", "")).strip()),
        "document_type": document_type,
        "responsible_department": normalize_space(str(classification.get("responsibleDepartment", "")).strip()),
        "reason": normalize_space(str(classification.get("reason", "")).strip()),
        "enactment_date": normalize_space(extract_date_from_html(html_content) or str(manifest.get("enactment_date", "")).strip()),
        "analyzed_at": normalize_space(str(classification.get("analyzedAt", "")).strip()),
        "updated_at": record_updated_at(html_path, markdown_path, classification_path),
        "source_url": normalize_space(str(manifest.get("detail_url") or manifest.get("source_url") or "").strip()),
        "source_file": normalize_space(str(manifest.get("source_file", "")).strip()),
        "taxonomy_path": normalize_space(str(manifest.get("taxonomy_path", "")).strip()),
        "taxonomy_paths": join_strings(manifest.get("taxonomy_paths", [])),
        "content_text": content_text,
        "content_length": len(content_text),
        "title_terms": title_terms,
        "reading_terms": reading_terms,
        "content_terms": content_terms,
        "department_terms": department_terms,
        "combined_reason_terms": combined_reason_terms,
        "reason_terms": reason_terms,
        "secondary_terms": secondary_terms,
        "lens_terms": lens_terms,
        "taxonomy_terms": taxonomy_terms,
        "has_classification": bool(classification_path is not None and classification_path.exists()),
    }


def init_db(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_SQL)


def ensure_db_schema(
    connection: sqlite3.Connection,
    *,
    heartbeat_callback: Callable[[], None] | None = None,
) -> None:
    # 逐次追加では既存 DB を温存しながら、必要なテーブルだけを補う。
    connection.executescript(INCREMENTAL_SCHEMA_SQL)
    terms_rebuilt = False
    if not ordinance_terms_schema_matches(connection):
        connection.execute("DROP TABLE IF EXISTS ordinances_terms")
        create_ordinances_terms(connection)
        terms_rebuilt = True
    if ordinances_terms_needs_rebuild(connection):
        rebuild_ordinances_terms(connection, heartbeat_callback=heartbeat_callback)
        terms_rebuilt = True
    if terms_rebuilt or not ordinance_fts_schema_matches(connection):
        rebuild_ordinances_fts(connection, heartbeat_callback=heartbeat_callback)
    else:
        create_ordinances_fts(connection)
        configure_ordinances_fts(connection)


def ordinance_table_columns(connection: sqlite3.Connection) -> set[str]:
    return set(table_columns(connection, "ordinances"))


def schema_is_compatible(connection: sqlite3.Connection) -> bool:
    columns = ordinance_table_columns(connection)
    if columns and not REQUIRED_ORDINANCE_COLUMNS.issubset(columns):
        return False
    return True


def recreate_output_db(
    output_db: Path,
    *,
    heartbeat_callback: Callable[[], None] | None = None,
) -> None:
    output_db.unlink(missing_ok=True)
    with open_sqlite_connection(output_db) as connection:
        ensure_db_schema(connection, heartbeat_callback=heartbeat_callback)
        connection.commit()


def ensure_output_db_permissions(path: Path) -> None:
    try:
        if path.exists():
            path.chmod(0o664)
    except Exception:
        pass


def ensure_output_db(
    output_db: Path,
    *,
    heartbeat_callback: Callable[[], None] | None = None,
) -> None:
    output_db.parent.mkdir(parents=True, exist_ok=True)
    recreate = False
    if output_db.exists():
        with open_sqlite_connection(output_db) as connection:
            recreate = not schema_is_compatible(connection)
    if recreate:
        # 旧 taikei の簡易 DB は列が足りないので、逐次追加へ切り替える前に作り直す。
        recreate_output_db(output_db, heartbeat_callback=heartbeat_callback)
        ensure_output_db_permissions(output_db)
        return
    with open_sqlite_connection(output_db) as connection:
        ensure_db_schema(connection, heartbeat_callback=heartbeat_callback)
        connection.commit()
    ensure_output_db_permissions(output_db)


def prepare_incremental_index(
    output_db: Path,
    *,
    logger: Callable[[str], None] | None = None,
    context: str = "",
) -> bool:
    # 例規本文の取得は進めつつ、逐次 index だけを best effort に落とす。
    try:
        register_incremental_flush_at_exit()
        ensure_output_db(output_db)
        return True
    except Exception as exc:
        if logger is not None:
            prefix = f"{context}: " if context else ""
            logger(
                f"[WARN] {prefix}ordinances.sqlite の準備に失敗したため、逐次インデックス更新を無効化します: "
                f"[{type(exc).__name__}] db={output_db} error={exc}"
            )
        return False


def resolve_record_paths(
    *,
    clean_html_dir: Path,
    classification_dir: Path,
    markdown_dir: Path,
    key: str,
) -> tuple[Path | None, Path | None, Path | None]:
    relative = Path(key.replace("\\", "/"))

    html_path: Path | None = None
    for suffix in (".html", ".htm"):
        candidate = clean_html_dir / relative.with_suffix(suffix)
        existing = reiki_io.existing_path(candidate)
        if existing is not None:
            html_path = existing
            break

    markdown_path = reiki_io.existing_path(markdown_dir / relative.with_suffix(".md"))
    classification_path = reiki_io.existing_path(classification_dir / relative.with_suffix(".json"))
    return html_path, markdown_path, classification_path


def upsert_record(connection: sqlite3.Connection, record: dict[str, Any]) -> int:
    existing_row = connection.execute(
        "SELECT id FROM ordinances WHERE filename = ?",
        (record["filename"],),
    ).fetchone()
    existing_terms: dict[str, Any] | None = None

    params = (
        record["filename"],
        record["title"],
        record["reading_kana"],
        record["sortable_kana"],
        record["primary_class"],
        record["secondary_tags"],
        record["necessity_score"],
        record["fiscal_impact_score"],
        record["regulatory_burden_score"],
        record["policy_effectiveness_score"],
        record["lens_tags"],
        record["lens_a_stance"],
        record["lens_b_stance"],
        record["combined_stance"],
        record["combined_reason"],
        record["document_type"],
        record["responsible_department"],
        record["reason"],
        record["enactment_date"] or None,
        record["analyzed_at"],
        record["updated_at"],
        record["source_url"],
        record["source_file"],
        record["taxonomy_path"],
        record["taxonomy_paths"],
        record["content_text"],
        record["content_length"],
    )

    if existing_row is None:
        cursor = connection.execute(
            """
            INSERT INTO ordinances (
                filename, title, reading_kana, sortable_kana, primary_class, secondary_tags,
                necessity_score, fiscal_impact_score, regulatory_burden_score, policy_effectiveness_score,
                lens_tags, lens_a_stance, lens_b_stance, combined_stance, combined_reason,
                document_type, responsible_department, reason, enactment_date, analyzed_at,
                updated_at, source_url, source_file, taxonomy_path, taxonomy_paths,
                content_text, content_length
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
        row_id = int(cursor.lastrowid)
    else:
        row_id = int(existing_row[0])
        terms_row = connection.execute(
            """
            SELECT
                title_terms,
                reading_terms,
                content_terms,
                department_terms,
                combined_reason_terms,
                reason_terms,
                secondary_terms,
                lens_terms,
                taxonomy_terms
            FROM ordinances_terms
            WHERE id = ?
            """,
            (row_id,),
        ).fetchone()
        if terms_row is not None:
            existing_terms = {
                "title_terms": str(terms_row[0] or ""),
                "reading_terms": str(terms_row[1] or ""),
                "content_terms": str(terms_row[2] or ""),
                "department_terms": str(terms_row[3] or ""),
                "combined_reason_terms": str(terms_row[4] or ""),
                "reason_terms": str(terms_row[5] or ""),
                "secondary_terms": str(terms_row[6] or ""),
                "lens_terms": str(terms_row[7] or ""),
                "taxonomy_terms": str(terms_row[8] or ""),
            }
            delete_ordinances_fts_row(connection, row_id, existing_terms)
        connection.execute(
            """
            UPDATE ordinances
               SET filename = ?, title = ?, reading_kana = ?, sortable_kana = ?, primary_class = ?, secondary_tags = ?,
                   necessity_score = ?, fiscal_impact_score = ?, regulatory_burden_score = ?, policy_effectiveness_score = ?,
                   lens_tags = ?, lens_a_stance = ?, lens_b_stance = ?, combined_stance = ?, combined_reason = ?,
                   document_type = ?, responsible_department = ?, reason = ?, enactment_date = ?, analyzed_at = ?,
                   updated_at = ?, source_url = ?, source_file = ?, taxonomy_path = ?, taxonomy_paths = ?,
                   content_text = ?, content_length = ?
             WHERE id = ?
            """,
            params + (row_id,),
        )

    connection.execute(
        """
        INSERT INTO ordinances_terms (
            id, title_terms, reading_terms, content_terms, department_terms,
            combined_reason_terms, reason_terms, secondary_terms, lens_terms, taxonomy_terms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title_terms = excluded.title_terms,
            reading_terms = excluded.reading_terms,
            content_terms = excluded.content_terms,
            department_terms = excluded.department_terms,
            combined_reason_terms = excluded.combined_reason_terms,
            reason_terms = excluded.reason_terms,
            secondary_terms = excluded.secondary_terms,
            lens_terms = excluded.lens_terms,
            taxonomy_terms = excluded.taxonomy_terms
        """,
        (
            row_id,
            record["title_terms"],
            record["reading_terms"],
            record["content_terms"],
            record["department_terms"],
            record["combined_reason_terms"],
            record["reason_terms"],
            record["secondary_terms"],
            record["lens_terms"],
            record["taxonomy_terms"],
        ),
    )
    insert_ordinances_fts_row(connection, row_id, record)
    return row_id


def upsert_source_key(
    *,
    slug: str,
    clean_html_dir: Path,
    classification_dir: Path,
    markdown_dir: Path,
    output_db: Path,
    key: str,
    manifest: dict[str, Any] | None = None,
    manifest_json: Path | None = None,
) -> bool:
    # 子スクレイパからは 1 件ごとの HTML/Markdown 完成直後にここを呼び、検索 DB へ追記する。
    html_path, markdown_path, classification_path = resolve_record_paths(
        clean_html_dir=clean_html_dir,
        classification_dir=classification_dir,
        markdown_dir=markdown_dir,
        key=key,
    )
    if html_path is None or not html_path.exists():
        return False

    manifest_row = manifest
    if manifest_row is None and manifest_json is not None:
        manifest_index = load_manifest_index(manifest_json)
        manifest_row = manifest_index.get(key) or manifest_index.get(Path(key).name)

    record = build_record(
        key,
        html_path,
        markdown_path,
        classification_path,
        manifest_row,
        municipality_sortable_prefixes(slug),
    )
    if record is None:
        return False

    register_incremental_flush_at_exit()
    key = incremental_db_key(output_db)
    pending = _PENDING_INCREMENTAL_RECORDS.setdefault(key, [])
    pending.append(record)
    if len(pending) >= INCREMENTAL_COMMIT_BATCH_SIZE:
        flush_incremental_index(output_db)
    return True


def best_effort_upsert_source_key(
    *,
    slug: str,
    clean_html_dir: Path,
    classification_dir: Path,
    markdown_dir: Path,
    output_db: Path,
    key: str,
    manifest: dict[str, Any] | None = None,
    manifest_json: Path | None = None,
    logger: Callable[[str], None] | None = None,
    context: str = "",
) -> str:
    # 条例ごとの逐次反映失敗は warning に落とし、自治体ワーカー本体は継続させる。
    try:
        updated = upsert_source_key(
            slug=slug,
            clean_html_dir=clean_html_dir,
            classification_dir=classification_dir,
            markdown_dir=markdown_dir,
            output_db=output_db,
            key=key,
            manifest=manifest,
            manifest_json=manifest_json,
        )
    except Exception as exc:
        drop_pending_incremental_records(output_db)
        if logger is not None:
            prefix = f"{context}: " if context else ""
            logger(
                f"[WARN] {prefix}ordinances.sqlite への逐次反映に失敗しました: "
                f"[{type(exc).__name__}] db={output_db} key={key} error={exc}"
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
            with open_sqlite_connection(output_db) as connection:
                configure_ordinances_fts(connection)
                merge_ordinances_fts(connection)
                connection.commit()
        return True
    except Exception as exc:
        drop_pending_incremental_records(output_db)
        if logger is not None:
            prefix = f"{context}: " if context else ""
            logger(
                f"[WARN] {prefix}ordinances.sqlite の増分バッチ flush に失敗しました: "
                f"[{type(exc).__name__}] db={output_db} error={exc}"
            )
        return False


def backfill_missing_rows(
    *,
    slug: str,
    clean_html_dir: Path,
    classification_dir: Path,
    markdown_dir: Path,
    manifest_json: Path,
    output_db: Path,
    progress_callback: Callable[[dict[str, int | str]], None] | None = None,
    heartbeat_callback: Callable[[], None] | None = None,
) -> dict[str, int]:
    # 既に clean HTML があるのに ordinances.sqlite に無い行だけを、自治体単位で補完する。
    if not clean_html_dir.exists():
        return {"added": 0, "existing": 0, "skipped": 0, "total_html": 0}

    html_files = collect_preferred_files(clean_html_dir, {".html", ".htm"})
    if not html_files:
        return {"added": 0, "existing": 0, "skipped": 0, "total_html": 0}

    total_html = len(html_files)
    if progress_callback is not None:
        progress_callback(
            {
                "stage": "prepare_db",
                "processed": 0,
                "total_html": total_html,
                "added": 0,
                "existing": 0,
                "skipped": 0,
            }
        )

    markdown_files = build_alias_map(collect_preferred_files(markdown_dir, {".md"}))
    classification_files = build_alias_map(collect_preferred_files(classification_dir, {".json"}))
    manifest_index = load_manifest_index(manifest_json)
    prefixes = municipality_sortable_prefixes(slug)

    last_heartbeat_at = emit_heartbeat(heartbeat_callback, 0.0, force=True)
    ensure_output_db(output_db, heartbeat_callback=heartbeat_callback)
    last_heartbeat_at = emit_heartbeat(heartbeat_callback, last_heartbeat_at)
    with open_sqlite_connection(output_db) as connection:
        # prepare_db 済みの接続で既存行を読み、ここでは DDL を挟まない。
        existing = {
            str(row[0])
            for row in connection.execute("SELECT filename FROM ordinances")
        }
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
                    "total_html": total_html,
                    "added": 0,
                    "existing": 0,
                    "skipped": 0,
                }
            )
        last_heartbeat_at = emit_heartbeat(heartbeat_callback, last_heartbeat_at)
        for key, html_path in html_files.items():
            if key in existing:
                existing_count += 1
                processed += 1
                if progress_callback is not None:
                    now = time.monotonic()
                    if processed == total_html or processed % 250 == 0 or (now - last_report_at) >= 5.0:
                        last_report_at = now
                        progress_callback(
                            {
                                "stage": "indexing",
                                "processed": processed,
                                "total_html": total_html,
                                "added": added,
                                "existing": existing_count,
                                "skipped": skipped,
                            }
                        )
                        last_heartbeat_at = emit_heartbeat(heartbeat_callback, last_heartbeat_at, force=True)
                continue
            record = build_record(
                key,
                html_path,
                markdown_files.get(key) or markdown_files.get(Path(key).name),
                classification_files.get(key) or classification_files.get(Path(key).name),
                manifest_index.get(key) or manifest_index.get(Path(key).name),
                prefixes,
            )
            if record is None:
                skipped += 1
                processed += 1
                if progress_callback is not None:
                    now = time.monotonic()
                    if processed == total_html or processed % 250 == 0 or (now - last_report_at) >= 5.0:
                        last_report_at = now
                        progress_callback(
                            {
                                "stage": "indexing",
                                "processed": processed,
                                "total_html": total_html,
                                "added": added,
                                "existing": existing_count,
                                "skipped": skipped,
                            }
                        )
                        last_heartbeat_at = emit_heartbeat(heartbeat_callback, last_heartbeat_at, force=True)
                continue
            upsert_record(connection, record)
            existing.add(key)
            added += 1
            processed += 1
            if progress_callback is not None:
                now = time.monotonic()
                if processed == total_html or processed % 250 == 0 or (now - last_report_at) >= 5.0:
                    last_report_at = now
                    progress_callback(
                        {
                            "stage": "indexing",
                            "processed": processed,
                            "total_html": total_html,
                            "added": added,
                            "existing": existing_count,
                            "skipped": skipped,
                        }
                    )
                    last_heartbeat_at = emit_heartbeat(heartbeat_callback, last_heartbeat_at, force=True)
        connection.commit()
    emit_heartbeat(heartbeat_callback, last_heartbeat_at, force=True)
    ensure_output_db_permissions(output_db)

    return {
        "added": added,
        "existing": existing_count,
        "skipped": skipped,
        "total_html": total_html,
    }


def build_index(
    *,
    slug: str,
    clean_html_dir: Path,
    classification_dir: Path,
    markdown_dir: Path,
    manifest_json: Path,
    output_db: Path,
) -> dict[str, int]:
    if not clean_html_dir.exists():
        raise FileNotFoundError(f"clean html dir not found: {clean_html_dir}")

    html_files = collect_preferred_files(clean_html_dir, {".html", ".htm"})
    if not html_files:
        raise RuntimeError(f"no clean html files found under {clean_html_dir}")

    markdown_files = build_alias_map(collect_preferred_files(markdown_dir, {".md"}))
    classification_files = build_alias_map(collect_preferred_files(classification_dir, {".json"}))
    manifest_index = load_manifest_index(manifest_json)
    prefixes = municipality_sortable_prefixes(slug)

    output_db.parent.mkdir(parents=True, exist_ok=True)
    temp_fd, temp_name = tempfile.mkstemp(prefix=f"{output_db.name}.", suffix=".tmp", dir=str(output_db.parent))
    os.close(temp_fd)
    temp_db = Path(temp_name)

    try:
        connection = open_sqlite_connection(temp_db, bulk_load=True)
        init_db(connection)
        cursor = connection.cursor()

        indexed = 0
        classified = 0
        skipped = 0

        for key, html_path in html_files.items():
            record = build_record(
                key,
                html_path,
                markdown_files.get(key) or markdown_files.get(Path(key).name),
                classification_files.get(key) or classification_files.get(Path(key).name),
                manifest_index.get(key) or manifest_index.get(Path(key).name),
                prefixes,
            )
            if record is None:
                skipped += 1
                continue

            cursor.execute(
                """
                INSERT INTO ordinances (
                    filename, title, reading_kana, sortable_kana, primary_class, secondary_tags,
                    necessity_score, fiscal_impact_score, regulatory_burden_score, policy_effectiveness_score,
                    lens_tags, lens_a_stance, lens_b_stance, combined_stance, combined_reason,
                    document_type, responsible_department, reason, enactment_date, analyzed_at,
                    updated_at, source_url, source_file, taxonomy_path, taxonomy_paths,
                    content_text, content_length
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["filename"],
                    record["title"],
                    record["reading_kana"],
                    record["sortable_kana"],
                    record["primary_class"],
                    record["secondary_tags"],
                    record["necessity_score"],
                    record["fiscal_impact_score"],
                    record["regulatory_burden_score"],
                    record["policy_effectiveness_score"],
                    record["lens_tags"],
                    record["lens_a_stance"],
                    record["lens_b_stance"],
                    record["combined_stance"],
                    record["combined_reason"],
                    record["document_type"],
                    record["responsible_department"],
                    record["reason"],
                    record["enactment_date"] or None,
                    record["analyzed_at"],
                    record["updated_at"],
                    record["source_url"],
                    record["source_file"],
                    record["taxonomy_path"],
                    record["taxonomy_paths"],
                    record["content_text"],
                    record["content_length"],
                ),
            )
            row_id = int(cursor.lastrowid)
            cursor.execute(
                """
                INSERT INTO ordinances_terms (
                    id, title_terms, reading_terms, content_terms, department_terms,
                    combined_reason_terms, reason_terms, secondary_terms, lens_terms, taxonomy_terms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    record["title_terms"],
                    record["reading_terms"],
                    record["content_terms"],
                    record["department_terms"],
                    record["combined_reason_terms"],
                    record["reason_terms"],
                    record["secondary_terms"],
                    record["lens_terms"],
                    record["taxonomy_terms"],
                ),
            )
            indexed += 1
            if record["has_classification"]:
                classified += 1

        rebuild_ordinances_fts(connection)
        optimize_ordinances_fts(connection)
        connection.commit()
        checkpoint_wal(connection)
        connection.close()
        temp_db.replace(output_db)
        ensure_output_db_permissions(output_db)
    except Exception:
        try:
            temp_db.unlink(missing_ok=True)
        except Exception:
            pass
        raise

    return {
        "indexed": indexed,
        "classified": classified,
        "unclassified": max(0, indexed - classified),
        "skipped": skipped,
    }


def main() -> int:
    args = parse_args()
    stats = build_index(
        slug=args.slug,
        clean_html_dir=args.clean_html_dir,
        classification_dir=args.classification_dir,
        markdown_dir=args.markdown_dir,
        manifest_json=args.manifest_json,
        output_db=args.output_db,
    )
    print(
        f"[DONE] indexed={stats['indexed']} classified={stats['classified']} "
        f"unclassified={stats['unclassified']} skipped={stats['skipped']} db={args.output_db}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
