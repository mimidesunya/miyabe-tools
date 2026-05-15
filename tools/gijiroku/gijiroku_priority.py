#!/usr/bin/env python3
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import gijiroku_storage


MINUTES_SUFFIXES = {".txt", ".html", ".htm"}


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


def count_downloaded_minutes(downloads_dir: Path) -> int:
    if not downloads_dir.exists():
        return 0
    seen: set[str] = set()
    for file_path in downloads_dir.rglob("*"):
        if not file_path.is_file():
            continue
        if gijiroku_storage.logical_suffix(file_path) not in MINUTES_SUFFIXES:
            continue
        seen.add(gijiroku_storage.source_key(file_path, downloads_dir))
    return len(seen)


def load_scrape_progress(state_path: Path) -> tuple[int, int]:
    try:
        payload = gijiroku_storage.load_state(state_path)
    except Exception:
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
    downloads_dir = Path(target["downloads_dir"])
    index_json_path = Path(target["index_json_path"])
    state_path = Path(target["work_dir"]) / "scrape_state.json"

    downloaded_count = count_downloaded_minutes(downloads_dir)
    index_total = load_json_array_count(index_json_path)
    state_current, state_total = load_scrape_progress(state_path)

    current_count = max(downloaded_count, state_current)
    total_count = max(index_total, state_total, current_count)
    ratio = (current_count / total_count) if total_count > 0 else 0.0

    if total_count > 0 and current_count > 0 and current_count < total_count:
        priority_group = 1
        priority_label = "near_complete"
    elif total_count <= 0:
        priority_group = 2
        priority_label = "not_started"
    elif current_count >= total_count:
        priority_group = 3
        priority_label = "scraped"
    else:
        priority_group = 2
        priority_label = "not_started"

    return {
        "priority_group": priority_group,
        "priority_label": priority_label,
        "progress_ratio": ratio,
        "current_count": current_count,
        "total_count": total_count,
        "downloaded_count": downloaded_count,
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
