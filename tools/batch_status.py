from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def status_root() -> Path:
    return project_root() / "data" / "background_tasks"


def status_path(task_name: str) -> Path:
    return status_root() / f"{task_name}.json"


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def rel_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(project_root()).as_posix()
    except Exception:
        return str(path)


def build_state(task_name: str, run_id: str, total_count: int, summary_path: Path, log_dir: Path) -> dict[str, Any]:
    return {
        "task": task_name,
        "run_id": run_id,
        "running": True,
        "started_at": now_text(),
        "finished_at": "",
        "updated_at": now_text(),
        "total_count": total_count,
        "completed_count": 0,
        "active_count": 0,
        "pending_count": total_count,
        "summary_csv": rel_path(summary_path),
        "logs_dir": rel_path(log_dir),
        "items": {},
    }


def register_target(state: dict[str, Any], target: dict[str, Any], host: str) -> None:
    slug = str(target.get("slug", "")).strip()
    if slug == "":
        return
    items = state.setdefault("items", {})
    items[slug] = {
        "slug": slug,
        "code": str(target.get("code", "")).strip(),
        "name": str(target.get("name", "")).strip(),
        "full_name": str(target.get("full_name", "")).strip(),
        "system_type": str(target.get("system_type", "")).strip(),
        "host": host,
        "source_url": str(target.get("source_url", "")).strip(),
        "status": "pending",
        "message": "待機中",
        "started_at": "",
        "finished_at": "",
        "updated_at": now_text(),
        "returncode": None,
        "pid": None,
    }
    refresh_counts(state)


def update_item(
    state: dict[str, Any],
    slug: str,
    *,
    status: str | None = None,
    message: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    returncode: int | None = None,
    pid: int | None = None,
) -> None:
    items = state.setdefault("items", {})
    item = items.get(slug)
    if not isinstance(item, dict):
        return

    if status is not None:
        item["status"] = status
    if message is not None:
        item["message"] = message
    if started_at is not None:
        item["started_at"] = started_at
    if finished_at is not None:
        item["finished_at"] = finished_at
    if returncode is not None or status == "failed":
        item["returncode"] = returncode
    if pid is not None:
        item["pid"] = pid
    item["updated_at"] = now_text()
    refresh_counts(state)


def refresh_counts(state: dict[str, Any]) -> None:
    items = state.get("items", {})
    if not isinstance(items, dict):
        items = {}
    total_count = len(items)
    completed_count = 0
    active_count = 0
    pending_count = 0
    for item in items.values():
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "")).strip()
        if status in {"done", "ok", "failed"}:
            completed_count += 1
        elif status == "running":
            active_count += 1
        elif status == "pending":
            pending_count += 1

    state["total_count"] = total_count
    state["completed_count"] = completed_count
    state["active_count"] = active_count
    state["pending_count"] = pending_count
    state["updated_at"] = now_text()


def finish_batch(state: dict[str, Any]) -> None:
    state["running"] = False
    state["active_count"] = 0
    state["finished_at"] = now_text()
    refresh_counts(state)


def write_state(task_name: str, state: dict[str, Any]) -> Path:
    root = status_root()
    root.mkdir(parents=True, exist_ok=True)
    path = status_path(task_name)
    temp_path = path.with_suffix(".json.tmp")
    payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    temp_path.write_text(payload, encoding="utf-8")
    os.replace(temp_path, path)
    return path
