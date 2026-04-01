from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

# 一括スクレイパから書き込む background_tasks JSON の共通更新処理。
_UNSET = object()


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


# 各自治体ごとの最小限メタデータを state に登録しておき、途中経過で追記していく。
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
        "progress_updated_at": "",
        "returncode": None,
        "pid": None,
        "progress_current": None,
        "progress_total": None,
        "progress_unit": "",
    }
    # バッチ全体の updated_at は、実際に item 構成が増えたときだけ進める。
    state["updated_at"] = now_text()
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
    progress_current: int | None | object = _UNSET,
    progress_total: int | None | object = _UNSET,
    progress_unit: str | None | object = _UNSET,
) -> None:
    items = state.setdefault("items", {})
    item = items.get(slug)
    if not isinstance(item, dict):
        return

    changed = False
    progress_changed = False

    if status is not None:
        if item.get("status") != status:
            item["status"] = status
            changed = True
    if message is not None:
        if item.get("message") != message:
            item["message"] = message
            changed = True
    if started_at is not None:
        if item.get("started_at") != started_at:
            item["started_at"] = started_at
            changed = True
    if finished_at is not None:
        if item.get("finished_at") != finished_at:
            item["finished_at"] = finished_at
            changed = True
    if returncode is not None or status == "failed":
        if item.get("returncode") != returncode:
            item["returncode"] = returncode
            changed = True
    if pid is not None:
        if item.get("pid") != pid:
            item["pid"] = pid
            changed = True
    if progress_current is not _UNSET:
        next_value = None if progress_current is None else max(0, int(progress_current))
        if item.get("progress_current") != next_value:
            item["progress_current"] = next_value
            changed = True
            progress_changed = True
    if progress_total is not _UNSET:
        next_value = None if progress_total is None else max(0, int(progress_total))
        if item.get("progress_total") != next_value:
            item["progress_total"] = next_value
            changed = True
            progress_changed = True
    if progress_unit is not _UNSET:
        next_value = "" if progress_unit is None else str(progress_unit).strip()
        if item.get("progress_unit") != next_value:
            item["progress_unit"] = next_value
            changed = True
            progress_changed = True

    if progress_changed:
        # UI の「更新」は件数が最後に動いた時刻として扱いたいので、進捗専用タイムスタンプを分ける。
        item["progress_updated_at"] = now_text()
    if changed:
        item["updated_at"] = now_text()
        # バッチ全体の updated_at も、実データが変わったときだけ進める。
        state["updated_at"] = now_text()
    refresh_counts(state)


# items の status を集計し直し、バッチ全体の completed/active/pending を保つ。
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
        if status in {"done", "ok", "failed", "snapshot"}:
            completed_count += 1
        elif status == "running":
            active_count += 1
        elif status == "pending":
            pending_count += 1

    state["total_count"] = total_count
    state["completed_count"] = completed_count
    state["active_count"] = active_count
    state["pending_count"] = pending_count
    # 集計値の再計算だけでは updated_at を動かさない。
    # heartbeat のたびに時刻が進むと、件数が増えていないのに「更新」だけ動いて見えてしまう。


def finish_batch(state: dict[str, Any]) -> None:
    state["running"] = False
    state["active_count"] = 0
    state["finished_at"] = now_text()
    state["updated_at"] = state["finished_at"]
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
