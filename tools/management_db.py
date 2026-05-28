"""Best-effort PostgreSQL mirror for runtime task status.

The JSON files under data/background_tasks remain the source of truth for the
scraper processes.  This module mirrors them into PostgreSQL so the web UI can
serve task status and homepage summaries without repeatedly reading many JSON
files.  All callers treat database failures as non-fatal.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse, urlunparse


_AVAILABLE: bool | None = None


def database_url() -> str:
    return (
        os.environ.get("MANAGEMENT_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or "postgresql://miyabe:miyabe@postgres:5432/miyabe_management"
    ).strip()


def psycopg_url(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme in {"pgsql", "postgres"}:
        parsed = parsed._replace(scheme="postgresql")
    if parsed.scheme == "":
        return url
    # Drop PHP-only DSN query options if they ever appear in the shared env var.
    query = parse_qs(parsed.query)
    kept = []
    for key, values in query.items():
        if key.lower() in {"sslmode", "connect_timeout"}:
            for value in values:
                kept.append(f"{quote(key)}={quote(value)}")
    return urlunparse(parsed._replace(query="&".join(kept)))


def _connect():
    # PostgreSQL is optional for local scraper runs; returning None keeps the
    # filesystem-backed status path usable without special setup.
    try:
        import psycopg
    except Exception:
        return None
    try:
        return psycopg.connect(psycopg_url(database_url()), connect_timeout=3)
    except Exception:
        return None


def available() -> bool:
    global _AVAILABLE
    if _AVAILABLE is not None:
        return _AVAILABLE
    conn = _connect()
    if conn is None:
        _AVAILABLE = False
        return False
    try:
        conn.close()
    except Exception:
        pass
    _AVAILABLE = True
    return True


def migrate(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS management_task_statuses (
            task_key text PRIMARY KEY,
            running boolean NOT NULL DEFAULT false,
            heartbeat_at text NOT NULL DEFAULT '',
            updated_at_text text NOT NULL DEFAULT '',
            source_mtime double precision NOT NULL DEFAULT 0,
            status_json jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS processing_task_items (
            task_key text NOT NULL,
            slug text NOT NULL,
            code text NOT NULL DEFAULT '',
            name text NOT NULL DEFAULT '',
            full_name text NOT NULL DEFAULT '',
            feature_key text NOT NULL DEFAULT '',
            task_area text NOT NULL DEFAULT 'scrape',
            status text NOT NULL DEFAULT '',
            message text NOT NULL DEFAULT '',
            host text NOT NULL DEFAULT '',
            system_type text NOT NULL DEFAULT '',
            source_url text NOT NULL DEFAULT '',
            started_at_text text NOT NULL DEFAULT '',
            finished_at_text text NOT NULL DEFAULT '',
            updated_at_text text NOT NULL DEFAULT '',
            progress_updated_at_text text NOT NULL DEFAULT '',
            returncode integer,
            pid integer,
            progress_current integer,
            progress_total integer,
            progress_unit text NOT NULL DEFAULT '',
            freshness_date date,
            freshness_basis text NOT NULL DEFAULT '',
            last_checked_at_text text NOT NULL DEFAULT '',
            warning_count integer NOT NULL DEFAULT 0,
            warning_lines jsonb NOT NULL DEFAULT '[]'::jsonb,
            item_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (task_key, slug)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_processing_task_items_task_status "
        "ON processing_task_items (task_key, status, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_processing_task_items_slug "
        "ON processing_task_items (slug)"
    )
    conn.execute("ALTER TABLE processing_task_items ADD COLUMN IF NOT EXISTS freshness_date date")
    conn.execute("ALTER TABLE processing_task_items ADD COLUMN IF NOT EXISTS freshness_basis text NOT NULL DEFAULT ''")
    conn.execute("ALTER TABLE processing_task_items ADD COLUMN IF NOT EXISTS last_checked_at_text text NOT NULL DEFAULT ''")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_processing_task_items_freshness "
        "ON processing_task_items (task_key, freshness_date, last_checked_at_text)"
    )


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _feature_key(task_key: str) -> str:
    if task_key.startswith("reiki"):
        return "reiki"
    if task_key.startswith("gijiroku"):
        return "gijiroku"
    return ""


def _task_area(item: dict[str, Any]) -> str:
    message = str(item.get("message", ""))
    index_status = str(item.get("index_status", "")).strip()
    return "index" if "インデックス" in message or index_status else "scrape"


def store_task_status(task_key: str, status: dict[str, Any], source_path: Path | None = None) -> None:
    task_key = str(task_key).strip()
    if task_key == "" or not isinstance(status, dict):
        return
    conn = _connect()
    if conn is None:
        return
    try:
        with conn:
            migrate(conn)
            source_mtime = 0.0
            if source_path is not None:
                try:
                    source_mtime = float(source_path.stat().st_mtime)
                except Exception:
                    source_mtime = 0.0
            conn.execute(
                """
                INSERT INTO management_task_statuses (
                    task_key, running, heartbeat_at, updated_at_text,
                    source_mtime, status_json, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, now())
                ON CONFLICT (task_key) DO UPDATE SET
                    running = EXCLUDED.running,
                    heartbeat_at = EXCLUDED.heartbeat_at,
                    updated_at_text = EXCLUDED.updated_at_text,
                    source_mtime = EXCLUDED.source_mtime,
                    status_json = EXCLUDED.status_json,
                    updated_at = now()
                """,
                (
                    task_key,
                    bool(status.get("running", False)),
                    str(status.get("heartbeat_at", "")),
                    str(status.get("updated_at", "")),
                    source_mtime,
                    json.dumps(status, ensure_ascii=False),
                ),
            )
            items = status.get("items")
            if not isinstance(items, dict):
                items = {}
            seen_slugs: list[str] = []
            for raw_slug, raw_item in items.items():
                if not isinstance(raw_item, dict):
                    continue
                item = dict(raw_item)
                slug = str(item.get("slug") or raw_slug).strip()
                if slug == "":
                    continue
                item["slug"] = slug
                seen_slugs.append(slug)
                warning_lines = item.get("warning_lines")
                if not isinstance(warning_lines, list):
                    warning_lines = []
                conn.execute(
                    """
                    INSERT INTO processing_task_items (
                        task_key, slug, code, name, full_name, feature_key, task_area,
                        status, message, host, system_type, source_url,
                        started_at_text, finished_at_text, updated_at_text,
                        progress_updated_at_text, returncode, pid, progress_current,
                        progress_total, progress_unit, warning_count, warning_lines,
                        freshness_date, freshness_basis, last_checked_at_text,
                        item_json, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s::jsonb,
                        NULLIF(%s, '')::date, %s, %s,
                        %s::jsonb,
                        now()
                    )
                    ON CONFLICT (task_key, slug) DO UPDATE SET
                        code = EXCLUDED.code,
                        name = EXCLUDED.name,
                        full_name = EXCLUDED.full_name,
                        feature_key = EXCLUDED.feature_key,
                        task_area = EXCLUDED.task_area,
                        status = EXCLUDED.status,
                        message = EXCLUDED.message,
                        host = EXCLUDED.host,
                        system_type = EXCLUDED.system_type,
                        source_url = EXCLUDED.source_url,
                        started_at_text = EXCLUDED.started_at_text,
                        finished_at_text = EXCLUDED.finished_at_text,
                        updated_at_text = EXCLUDED.updated_at_text,
                        progress_updated_at_text = EXCLUDED.progress_updated_at_text,
                        returncode = EXCLUDED.returncode,
                        pid = EXCLUDED.pid,
                        progress_current = EXCLUDED.progress_current,
                        progress_total = EXCLUDED.progress_total,
                        progress_unit = EXCLUDED.progress_unit,
                        freshness_date = EXCLUDED.freshness_date,
                        freshness_basis = EXCLUDED.freshness_basis,
                        last_checked_at_text = EXCLUDED.last_checked_at_text,
                        warning_count = EXCLUDED.warning_count,
                        warning_lines = EXCLUDED.warning_lines,
                        item_json = EXCLUDED.item_json,
                        updated_at = now()
                    """,
                    (
                        task_key,
                        slug,
                        str(item.get("code", "")),
                        str(item.get("name", "")),
                        str(item.get("full_name", "")),
                        _feature_key(task_key),
                        _task_area(item),
                        str(item.get("status", "")),
                        str(item.get("message", "")),
                        str(item.get("host", "")),
                        str(item.get("system_type", "")),
                        str(item.get("source_url", "")),
                        str(item.get("started_at", "")),
                        str(item.get("finished_at", "")),
                        str(item.get("updated_at", "")),
                        str(item.get("progress_updated_at", "")),
                        _optional_int(item.get("returncode")),
                        _optional_int(item.get("pid")),
                        _optional_int(item.get("progress_current")),
                        _optional_int(item.get("progress_total")),
                        str(item.get("progress_unit", "")),
                        max(0, int(item.get("warning_count") or 0)),
                        json.dumps(warning_lines, ensure_ascii=False),
                        str(item.get("freshness_date", "")),
                        str(item.get("freshness_basis", "")),
                        str(item.get("last_checked_at", "")),
                        json.dumps(item, ensure_ascii=False),
                    ),
                )
            if seen_slugs:
                conn.execute(
                    "DELETE FROM processing_task_items WHERE task_key = %s AND NOT (slug = ANY(%s))",
                    (task_key, seen_slugs),
                )
            else:
                conn.execute("DELETE FROM processing_task_items WHERE task_key = %s", (task_key,))
    finally:
        conn.close()
