#!/usr/bin/env python3
from __future__ import annotations

import gzip
import json
import sqlite3
from pathlib import Path
from typing import Any

import reiki_io


HTML_SUFFIXES = {".html", ".htm"}


def file_or_gzip_path(path: Path) -> Path | None:
    candidates = [path]
    if path.suffix.lower() == ".gz":
        candidates.append(path.with_suffix(""))
    else:
        candidates.append(path.with_name(path.name + ".gz"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_json_array_count(path: Path) -> int:
    candidate = file_or_gzip_path(path)
    if candidate is None:
        return 0
    try:
        raw = candidate.read_bytes()
        if candidate.suffix.lower() == ".gz":
            raw = gzip.decompress(raw)
        loaded = json.loads(raw.decode("utf-8"))
    except Exception:
        return 0
    return len(loaded) if isinstance(loaded, list) else 0


def sqlite_row_count(path: Path, table: str) -> int:
    if not path.exists():
        return 0
    try:
        connection = sqlite3.connect(path)
        try:
            row = connection.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()
        finally:
            connection.close()
    except Exception:
        return 0
    return max(0, int(row[0] if row else 0))


def count_html_files(root: Path) -> int:
    if not root.exists():
        return 0
    seen: set[str] = set()
    for file_path in root.rglob("*"):
        if not file_path.is_file():
            continue
        logical = reiki_io.logical_path(file_path)
        if logical.suffix.lower() not in HTML_SUFFIXES:
            continue
        seen.add(logical.relative_to(root).as_posix())
    return len(seen)


def load_scrape_progress(state_path: Path) -> tuple[int, int]:
    try:
        payload = reiki_io.load_json(state_path, {})
    except Exception:
        return 0, 0
    if not isinstance(payload, dict):
        return 0, 0

    current_raw = payload.get("progress_current")
    total_raw = payload.get("progress_total")
    try:
        current = max(0, int(current_raw))
        total = max(0, int(total_raw))
    except Exception:
        return 0, 0
    return current, total


def target_priority_info(target: dict[str, Any]) -> dict[str, Any]:
    work_root = Path(target["work_root"])
    source_dir = Path(target["source_dir"])
    html_dir = Path(target["html_dir"])
    db_path = Path(target["db_path"])
    manifest_path = work_root / "source_manifest.json.gz"
    state_path = work_root / "scrape_state.json"

    manifest_count = load_json_array_count(manifest_path)
    source_count = count_html_files(source_dir)
    clean_html_count = count_html_files(html_dir)
    indexed_count = sqlite_row_count(db_path, "ordinances")
    state_current, state_total = load_scrape_progress(state_path)

    current_count = max(source_count, clean_html_count, indexed_count, state_current)
    total_count = max(manifest_count, state_total, current_count)
    ratio = (current_count / total_count) if total_count > 0 else 0.0

    if total_count > 0 and current_count >= total_count and indexed_count < total_count:
        priority_group = 0
        priority_label = "needs_publish"
    elif total_count > 0 and current_count > 0 and current_count < total_count:
        priority_group = 1
        priority_label = "near_complete"
    elif total_count <= 0:
        priority_group = 2
        priority_label = "not_started"
    elif indexed_count >= total_count:
        priority_group = 3
        priority_label = "published"
    else:
        priority_group = 2
        priority_label = "not_started"

    return {
        "priority_group": priority_group,
        "priority_label": priority_label,
        "progress_ratio": ratio,
        "current_count": current_count,
        "total_count": total_count,
        "clean_html_count": clean_html_count,
        "indexed_count": indexed_count,
    }


def priority_sort_key(target: dict[str, Any]) -> tuple[Any, ...]:
    info = target_priority_info(target)
    return (
        int(info["priority_group"]),
        -float(info["progress_ratio"]),
        -int(info["current_count"]),
        str(target.get("name", "")),
        str(target.get("slug", "")),
    )


def sort_targets_by_priority(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(targets, key=priority_sort_key)
