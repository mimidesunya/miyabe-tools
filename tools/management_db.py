"""実行時タスク状態を PostgreSQL へ補助的にミラーする。

スクレイパにとっての正本は data/background_tasks 配下の JSON のままにする。
このモジュールはそれを PostgreSQL に写し、Web UI が多数の JSON を何度も読まずに
タスク状況やトップページ概要を返せるようにする。DB 失敗はすべて非致命扱い。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse, urlunparse


_AVAILABLE: bool | None = None
_MIGRATED = False
_CONN = None
# (task_key -> slug -> item の JSON 文字列)。前回書き込みと同一の行は UPSERT を省く。
_ITEM_CACHE: dict[str, dict[str, str]] = {}


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
    # 共通 env に PHP 専用の DSN オプションが混ざっても、psycopg が読める形だけ残す。
    query = parse_qs(parsed.query)
    kept = []
    for key, values in query.items():
        if key.lower() in {"sslmode", "connect_timeout"}:
            for value in values:
                kept.append(f"{quote(key)}={quote(value)}")
    return urlunparse(parsed._replace(query="&".join(kept)))


def _connect():
    # ローカルのスクレイパ実行では PostgreSQL は任意。
    # None を返すことで、特別な準備なしにファイル保存の status 経路を使える。
    try:
        import psycopg
    except Exception:
        return None
    try:
        return psycopg.connect(psycopg_url(database_url()), connect_timeout=3)
    except Exception:
        return None


def _get_connection():
    # state 書き込みは数秒おきに走るため、接続はプロセス内で使い回す。
    global _CONN
    if _CONN is not None:
        try:
            if not _CONN.closed:
                return _CONN
        except Exception:
            pass
    _CONN = _connect()
    return _CONN


def _reset_connection() -> None:
    global _CONN, _MIGRATED
    if _CONN is not None:
        try:
            _CONN.close()
        except Exception:
            pass
    _CONN = None
    _MIGRATED = False
    _ITEM_CACHE.clear()


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
    # state 書き込みは数秒おきに走るので、DDL はプロセスごとに 1 回だけ流す。
    global _MIGRATED
    if _MIGRATED:
        return
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
    _MIGRATED = True


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
    conn = _get_connection()
    if conn is None:
        return

    items = status.get("items")
    if not isinstance(items, dict):
        items = {}
    # item 行は内容が変わったときだけ書く。heartbeat のたびに全自治体を
    # UPSERT すると、数千行 × 数秒間隔で DB 側が支配的なコストになる。
    normalized_items: dict[str, dict[str, Any]] = {}
    serialized_items: dict[str, str] = {}
    for raw_slug, raw_item in items.items():
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        slug = str(item.get("slug") or raw_slug).strip()
        if slug == "":
            continue
        item["slug"] = slug
        normalized_items[slug] = item
        serialized_items[slug] = json.dumps(item, ensure_ascii=False)

    previous_items = _ITEM_CACHE.get(task_key)
    full_sync = previous_items is None

    try:
        with conn.transaction():
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
            for slug, item in normalized_items.items():
                if not full_sync and previous_items.get(slug) == serialized_items[slug]:
                    continue
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
                        serialized_items[slug],
                    ),
                )
            if full_sync:
                # プロセス初回は DB 側の残骸も同期し直す。
                if serialized_items:
                    conn.execute(
                        "DELETE FROM processing_task_items WHERE task_key = %s AND NOT (slug = ANY(%s))",
                        (task_key, list(serialized_items)),
                    )
                else:
                    conn.execute("DELETE FROM processing_task_items WHERE task_key = %s", (task_key,))
            else:
                removed = [slug for slug in previous_items if slug not in serialized_items]
                if removed:
                    conn.execute(
                        "DELETE FROM processing_task_items WHERE task_key = %s AND slug = ANY(%s)",
                        (task_key, removed),
                    )
    except Exception:
        # 接続が壊れた可能性があるので作り直し、差分キャッシュも破棄して次回フル同期する。
        _reset_connection()
        raise
    _ITEM_CACHE[task_key] = serialized_items
