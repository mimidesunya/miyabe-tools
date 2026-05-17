#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


STOP_RETURN_CODES = {-15, -2, 130, 143}
_TASK_STATUS_CACHE: dict[str, dict[str, Any]] = {}


def task_status(task_name: str) -> dict[str, Any]:
    if task_name in _TASK_STATUS_CACHE:
        return _TASK_STATUS_CACHE[task_name]
    path = Path(__file__).resolve().parents[2] / "data" / "background_tasks" / f"{task_name}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    _TASK_STATUS_CACHE[task_name] = payload
    return payload


def task_item(task_name: str, slug: str) -> dict[str, Any]:
    payload = task_status(task_name)
    item = payload.get("items", {}).get(slug)
    return item if isinstance(item, dict) else {}


def previous_item_failed_with_error(slug: str) -> bool:
    item = task_item("gijiroku", slug)
    if str(item.get("status", "")).strip() != "failed":
        return False
    message = str(item.get("message", "")).strip()
    if message.startswith("停止"):
        return False
    try:
        returncode = int(item.get("returncode"))
    except Exception:
        return True
    return returncode not in STOP_RETURN_CODES


def item_progress(item: dict[str, Any]) -> tuple[int, int]:
    try:
        current = max(0, int(item.get("progress_current")))
        total = max(0, int(item.get("progress_total")))
    except Exception:
        return 0, 0
    return current, total


def priority_progress(slug: str) -> tuple[int, int]:
    candidates = [
        item_progress(task_item("gijiroku", slug)),
        item_progress(task_item("gijiroku_snapshot", slug)),
    ]
    return max(candidates, key=lambda value: (value[1] > 0, value[0] < value[1], value[0], value[1]))


def target_priority_info(target: dict[str, Any]) -> dict[str, Any]:
    slug = str(target.get("slug", "")).strip()
    current_count, total_count = priority_progress(slug)
    ratio = (current_count / total_count) if total_count > 0 else 0.0

    previously_failed = previous_item_failed_with_error(slug)
    if previously_failed:
        priority_group = 4
        priority_label = "previous_failed"
    elif total_count > 0 and current_count > 0 and current_count < total_count:
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
        "downloaded_count": current_count,
        "previously_failed": previously_failed,
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
