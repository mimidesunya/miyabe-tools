from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TIMEZONE = "Asia/Tokyo"
DEFAULT_STALE_SECONDS = 15 * 60
STATUS_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
GIJIROKU_SUPPORTED_SYSTEMS = {
    "gijiroku.com",
    "voices",
    "kaigiroku.net",
    "dbsr",
    "db-search",
    "kaigiroku-indexphp",
    "kensakusystem",
    "kami-city-pdf",
    "site-gikai-pdf",
    "static-kaigiroku-dir",
}
REIKI_SUPPORTED_SYSTEMS = {"d1-law", "taikei", "g-reiki"}


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
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
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


def task_is_stale_running(task_name: str, *, stale_seconds: int = DEFAULT_STALE_SECONDS) -> bool:
    payload = load_background_task_status(task_name)
    if not bool(payload.get("running")):
        return False
    heartbeat = parse_status_timestamp(payload.get("heartbeat_at") or payload.get("updated_at"))
    if heartbeat is None:
        return True
    return (time.time() - heartbeat) > max(0, stale_seconds)


def _item_progress(item: object) -> tuple[int, int]:
    if not isinstance(item, dict):
        return 0, 0
    try:
        current = max(0, int(item.get("progress_current") or 0))
        total = max(0, int(item.get("progress_total") or 0))
    except Exception:
        return 0, 0
    return current, total


def _ensure_tool_import_paths() -> None:
    for path in (ROOT / "tools", ROOT / "tools" / "gijiroku", ROOT / "tools" / "reiki"):
        text = str(path)
        if text not in sys.path:
            sys.path.append(text)


def _known_status_items(task_name: str) -> dict[str, dict]:
    known: dict[str, dict] = {}
    for status_name in (task_name, f"{task_name}_snapshot"):
        payload = load_background_task_status(status_name)
        items = payload.get("items")
        if not isinstance(items, dict):
            continue
        for slug, item in items.items():
            if isinstance(item, dict):
                known[str(slug)] = item
    return known


def _iter_supported_target_slugs(task_name: str) -> list[str]:
    _ensure_tool_import_paths()
    try:
        if task_name == "gijiroku":
            from tools.gijiroku import gijiroku_targets

            slugs: list[str] = []
            for target in gijiroku_targets.iter_gijiroku_targets():
                system_type = str(target.get("system_type") or "").strip()
                system_family = gijiroku_targets.canonical_minutes_system_type(system_type)
                if system_type in GIJIROKU_SUPPORTED_SYSTEMS or system_family in GIJIROKU_SUPPORTED_SYSTEMS:
                    slug = str(target.get("slug") or "").strip()
                    if slug:
                        slugs.append(slug)
            return slugs
        if task_name == "reiki":
            from tools.reiki import reiki_targets

            slugs = []
            for target in reiki_targets.iter_reiki_targets():
                if str(target.get("system_type") or "").strip() not in REIKI_SUPPORTED_SYSTEMS:
                    continue
                slug = str(target.get("slug") or "").strip()
                if slug:
                    slugs.append(slug)
            return slugs
    except Exception:
        return []
    return []


def task_has_remaining_work(task_name: str) -> bool:
    known_items = _known_status_items(task_name)
    target_slugs = _iter_supported_target_slugs(task_name)
    if target_slugs:
        for slug in target_slugs:
            item = known_items.get(slug)
            if item is None:
                return True
            if str(item.get("status") or "").strip() == "failed":
                return True
            current, total = _item_progress(item)
            if total <= 0 or current < total:
                return True
        return False

    for item in known_items.values():
        if str(item.get("status") or "").strip() == "failed":
            return True
        current, total = _item_progress(item)
        if total > 0 and current < total:
            return True
    return False


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
    due_seconds = schedule_seconds
    if task_has_remaining_work(task_name):
        due_seconds = env_int("SCRAPER_INCOMPLETE_SCHEDULE_SECONDS", 10 * 60, minimum=60)
    return (time.time() - latest) >= max(1, due_seconds)


def command_text(command: list[str]) -> str:
    return " ".join(command)
