from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# 一括スクレイパから書き込む background_tasks JSON の共通更新処理。
_UNSET = object()
TOKYO = timezone(timedelta(hours=9))
_STATUS_ROOT_OVERRIDE: Path | None = None


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def status_root() -> Path:
    if _STATUS_ROOT_OVERRIDE is not None:
        return _STATUS_ROOT_OVERRIDE
    return project_root() / "data" / "background_tasks"


def configure_status_root(path: Path | str | None) -> None:
    global _STATUS_ROOT_OVERRIDE
    _STATUS_ROOT_OVERRIDE = Path(path).resolve() if path is not None else None


def status_path(task_name: str) -> Path:
    return status_root() / f"{task_name}.json"


def runtime_cache_paths(include_homepage_payload: bool = False) -> list[Path]:
    root = status_root()
    paths = [
        root / "municipality_catalog_cache.json",
    ]
    if include_homepage_payload:
        paths.insert(1, root / "home_api_payload.json")
    return paths


def now_text() -> str:
    return datetime.now(TOKYO).strftime("%Y-%m-%d %H:%M:%S")


def format_timestamp_text(timestamp: float | None) -> str:
    if timestamp is None or timestamp <= 0:
        return now_text()
    return datetime.fromtimestamp(timestamp, TOKYO).strftime("%Y-%m-%d %H:%M:%S")


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
        "heartbeat_at": now_text(),
        "updated_at": now_text(),
        "total_count": total_count,
        "completed_count": 0,
        "active_count": 0,
        "pending_count": total_count,
        "summary_csv": rel_path(summary_path),
        "logs_dir": rel_path(log_dir),
        "running_label": "",
        "worker_capacity": None,
        "worker_active_count": 0,
        "worker_idle_count": None,
        "index_capacity": None,
        "index_active_count": 0,
        "index_idle_count": None,
        "index_queue_count": 0,
        "per_host_capacity": None,
        "items": {},
    }


def _normalize_optional_nonnegative_int(value: int | None | object) -> int | None | object:
    if value is _UNSET:
        return _UNSET
    if value is None:
        return None
    return max(0, int(value))


def update_runtime_metrics(
    state: dict[str, Any],
    *,
    running_label: str | None | object = _UNSET,
    worker_capacity: int | None | object = _UNSET,
    worker_active_count: int | None | object = _UNSET,
    index_capacity: int | None | object = _UNSET,
    index_active_count: int | None | object = _UNSET,
    index_queue_count: int | None | object = _UNSET,
    per_host_capacity: int | None | object = _UNSET,
) -> None:
    if running_label is not _UNSET:
        state["running_label"] = "" if running_label is None else str(running_label).strip()

    assignments = {
        "worker_capacity": _normalize_optional_nonnegative_int(worker_capacity),
        "worker_active_count": _normalize_optional_nonnegative_int(worker_active_count),
        "index_capacity": _normalize_optional_nonnegative_int(index_capacity),
        "index_active_count": _normalize_optional_nonnegative_int(index_active_count),
        "index_queue_count": _normalize_optional_nonnegative_int(index_queue_count),
        "per_host_capacity": _normalize_optional_nonnegative_int(per_host_capacity),
    }
    for key, value in assignments.items():
        if value is _UNSET:
            continue
        state[key] = value

    if worker_capacity is not _UNSET or worker_active_count is not _UNSET:
        capacity = state.get("worker_capacity")
        active = state.get("worker_active_count")
        if isinstance(capacity, int) and isinstance(active, int):
            state["worker_idle_count"] = max(0, capacity - active)
        else:
            state["worker_idle_count"] = None

    if index_capacity is not _UNSET or index_active_count is not _UNSET:
        capacity = state.get("index_capacity")
        active = state.get("index_active_count")
        if isinstance(capacity, int) and isinstance(active, int):
            state["index_idle_count"] = max(0, capacity - active)
        else:
            state["index_idle_count"] = None


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
    extra_fields: dict[str, Any] | None = None,
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
    if extra_fields is not None:
        for key, value in extra_fields.items():
            if not isinstance(key, str) or key.strip() == "":
                continue
            if item.get(key) != value:
                item[key] = value
                changed = True

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
    state["heartbeat_at"] = state["finished_at"]
    state["updated_at"] = state["finished_at"]
    refresh_counts(state)


def touch_heartbeat(state: dict[str, Any]) -> None:
    # stale 判定は「最後に JSON を正常に書けた時刻」で見たいので、
    # 実データ件数とは別に heartbeat を持つ。
    state["heartbeat_at"] = now_text()


def write_state(task_name: str, state: dict[str, Any]) -> Path:
    root = status_root()
    root.mkdir(parents=True, exist_ok=True)
    path = status_path(task_name)
    temp_path = path.with_suffix(".json.tmp")
    touch_heartbeat(state)
    payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    try:
        temp_path.write_text(payload, encoding="utf-8")
        os.replace(temp_path, path)
    except Exception as exc:
        # 進捗 JSON は UI 補助なので、ここで本体バッチまで止めない。
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass
        print(
            f"[WARN] background task state write failed task={task_name} path={path} "
            f"[{type(exc).__name__}] {exc}",
            file=sys.stderr,
            flush=True,
        )
    return path


def invalidate_runtime_caches(*, include_homepage_payload: bool = False) -> None:
    for path in runtime_cache_paths(include_homepage_payload=include_homepage_payload):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
