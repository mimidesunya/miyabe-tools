from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TIMEZONE = "Asia/Tokyo"
DEFAULT_STALE_SECONDS = 15 * 60
STATUS_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def env_text(name: str, default: str) -> str:
    value = str(os.getenv(name, default)).strip()
    return value or default


def env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = str(os.getenv(name, str(default))).strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    return max(minimum, value)


def env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = str(os.getenv(name, str(default))).strip()
    try:
        value = float(raw)
    except ValueError:
        value = default
    return max(minimum, value)


def env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def background_task_path(task_name: str) -> Path:
    return ROOT / "data" / "background_tasks" / f"{task_name}.json"


def retry_marker_path(task_name: str) -> Path:
    return ROOT / "data" / "background_tasks" / "celery_retry_markers" / f"{task_name}.json"


def load_background_task_status(task_name: str) -> dict:
    path = background_task_path(task_name)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_retry_marker(task_name: str) -> dict:
    path = retry_marker_path(task_name)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_status_timestamp(value: object) -> float | None:
    text = str(value or "").strip()
    if text == "":
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    for parser in (datetime.fromisoformat, lambda raw: datetime.strptime(raw, STATUS_TIME_FORMAT)):
        try:
            parsed = parser(normalized)
            return parsed.timestamp()
        except Exception:
            continue
    return None


def latest_status_timestamp(task_name: str) -> float | None:
    payload = load_background_task_status(task_name)
    candidates: list[float] = []
    for key in ("finished_at", "heartbeat_at", "updated_at", "started_at"):
        parsed = parse_status_timestamp(payload.get(key))
        if parsed is not None:
            candidates.append(parsed)
    if not candidates:
        return None
    return max(candidates)


def set_retry_marker(task_name: str, delay_seconds: int) -> None:
    path = retry_marker_path(task_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "task": task_name,
        "next_retry_at": time.time() + max(1, delay_seconds),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clear_retry_marker(task_name: str) -> None:
    path = retry_marker_path(task_name)
    try:
        path.unlink()
    except FileNotFoundError:
        return


def retry_marker_active(task_name: str) -> bool:
    payload = load_retry_marker(task_name)
    next_retry_at = payload.get("next_retry_at")
    try:
        return float(next_retry_at) > time.time()
    except (TypeError, ValueError):
        return False


def task_is_running(task_name: str, *, stale_seconds: int = DEFAULT_STALE_SECONDS) -> bool:
    payload = load_background_task_status(task_name)
    if not bool(payload.get("running")):
        return False
    heartbeat = parse_status_timestamp(payload.get("heartbeat_at") or payload.get("updated_at"))
    if heartbeat is None:
        return True
    return (time.time() - heartbeat) <= max(0, stale_seconds)


def cycle_is_due(
    task_name: str,
    schedule_seconds: int,
    *,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
) -> bool:
    if retry_marker_active(task_name):
        return False
    if task_is_running(task_name, stale_seconds=stale_seconds):
        return False
    latest = latest_status_timestamp(task_name)
    if latest is None:
        return True
    return (time.time() - latest) >= max(1, schedule_seconds)


def command_text(command: list[str]) -> str:
    return " ".join(command)
