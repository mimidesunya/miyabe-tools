"""Scraper freshness metadata shared by minutes and reiki batches.

The batch schedulers use this module to decide whether a municipality can be
skipped for a while after a successful scrape.  Dates are derived from already
saved local artifacts; fetching remote pages just to decide freshness would make
the scheduling pass slow and noisy.
"""

from __future__ import annotations

import gzip
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


TOKYO = timezone(timedelta(hours=9))
FRESHNESS_SKIP_DAYS = 30
CHECK_INTERVAL_HOURS = 24

ERA_BASE_YEAR = {
    "明治": 1867,
    "大正": 1911,
    "昭和": 1925,
    "平成": 1988,
    "令和": 2018,
}

_ZENKAKU_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_WAREKI_DATE_RE = re.compile(r"(明治|大正|昭和|平成|令和)\s*([0-9０-９元]+)年\s*([0-9０-９]+)月\s*([0-9０-９]+)日")
_STATUS_CACHE: dict[str, dict[str, Any]] = {}


def now_tokyo() -> datetime:
    return datetime.now(TOKYO)


def today_tokyo() -> date:
    return now_tokyo().date()


def _ascii_digits(value: str) -> str:
    return str(value).translate(_ZENKAKU_DIGITS)


def _japanese_year(value: str) -> int | None:
    raw = _ascii_digits(value).strip()
    if raw == "元":
        return 1
    if raw.isdigit():
        return int(raw)
    return None


def normalize_date_text(value: Any) -> str:
    text = _ascii_digits(str(value or "")).strip()
    if text == "":
        return ""

    iso = _ISO_DATE_RE.search(text)
    if iso:
        year = int(iso.group(1))
        month = max(1, int(iso.group(2)))
        day = max(1, int(iso.group(3)))
        if month > 12 or day > 31:
            return ""
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return ""

    wareki = _WAREKI_DATE_RE.search(text)
    if wareki:
        year = _japanese_year(wareki.group(2))
        if year is None:
            return ""
        base = ERA_BASE_YEAR.get(wareki.group(1))
        if base is None:
            return ""
        try:
            return date(base + year, int(_ascii_digits(wareki.group(3))), int(_ascii_digits(wareki.group(4)))).isoformat()
        except ValueError:
            return ""

    return ""


def parse_date(value: Any) -> date | None:
    normalized = normalize_date_text(value)
    if normalized == "":
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def parse_datetime_text(value: Any) -> datetime | None:
    text = _ascii_digits(str(value or "")).strip()
    if text == "":
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=TOKYO)
            return parsed.astimezone(TOKYO)
        except ValueError:
            continue
    return None


def read_json_maybe_gzip(path: Path) -> Any:
    candidates = [path]
    if path.suffix.lower() == ".gz":
        candidates.append(path.with_suffix(""))
    else:
        candidates.append(path.with_name(path.name + ".gz"))
    for candidate in candidates:
        try:
            raw = candidate.read_bytes()
            if candidate.suffix.lower() == ".gz":
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8"))
        except Exception:
            continue
    return None


def max_normalized_date(values: list[Any]) -> str:
    dates = [parsed for parsed in (parse_date(value) for value in values) if parsed is not None]
    return max(dates).isoformat() if dates else ""


def status_item(task_name: str, slug: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "data" / "background_tasks" / f"{task_name}.json"
    if task_name in _STATUS_CACHE:
        payload = _STATUS_CACHE[task_name]
    else:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        _STATUS_CACHE[task_name] = payload
    item = payload.get("items", {}).get(slug) if isinstance(payload, dict) else None
    return item if isinstance(item, dict) else {}


def existing_last_checked_at(task_name: str, slug: str) -> str:
    # Prefer the live task state over the snapshot because it records the most
    # recent contact attempt, even if that attempt did not finish successfully.
    for candidate_task in (task_name, f"{task_name}_snapshot"):
        item = status_item(candidate_task, slug)
        value = str(item.get("last_checked_at") or "").strip()
        if value:
            return value
        if str(item.get("status") or "").strip() in {"done", "ok", "snapshot"}:
            value = str(item.get("finished_at") or item.get("updated_at") or "").strip()
            if value:
                return value
    return ""


def gijiroku_target_freshness(target: dict[str, Any]) -> dict[str, str]:
    state_path = Path(str(target.get("work_dir") or "")) / "scrape_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    summary = state.get("plan_summary") if isinstance(state, dict) else None
    if isinstance(summary, dict):
        date_max = normalize_date_text(summary.get("date_max"))
        if date_max:
            return {"freshness_date": date_max, "freshness_basis": "latest_document"}

    rows = read_json_maybe_gzip(Path(str(target.get("index_json_path") or "")))
    if isinstance(rows, list):
        values: list[Any] = []
        try:
            import gijiroku_planning
        except Exception:
            gijiroku_planning = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            values.append(row.get("held_on"))
            values.append(row.get("sort_date"))
            if gijiroku_planning is not None:
                try:
                    values.append(gijiroku_planning.infer_sort_date(row))
                except Exception:
                    pass
        date_max = max_normalized_date(values)
        if date_max:
            return {"freshness_date": date_max, "freshness_basis": "latest_document"}

    return {"freshness_date": "", "freshness_basis": ""}


def reiki_target_freshness(target: dict[str, Any]) -> dict[str, str]:
    manifest_path = Path(str(target.get("work_root") or "")) / "source_manifest.json"
    records = read_json_maybe_gzip(manifest_path)
    if not isinstance(records, list):
        return {"freshness_date": "", "freshness_basis": ""}

    catalog_values: list[Any] = []
    document_values: list[Any] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        catalog_values.append(record.get("catalog_content_current"))
        document_values.extend([
            record.get("enactment_date"),
            record.get("promulgated_on"),
            record.get("date"),
            record.get("updated_at"),
        ])

    catalog_date = max_normalized_date(catalog_values)
    if catalog_date:
        return {"freshness_date": catalog_date, "freshness_basis": "content_current"}

    document_date = max_normalized_date(document_values)
    if document_date:
        return {"freshness_date": document_date, "freshness_basis": "latest_document"}

    return {"freshness_date": "", "freshness_basis": ""}


def target_freshness(task_name: str, target: dict[str, Any]) -> dict[str, str]:
    slug = str(target.get("slug") or "").strip()
    for candidate_task in (task_name, f"{task_name}_snapshot"):
        item = status_item(candidate_task, slug)
        cached_date = str(item.get("freshness_date") or "").strip()
        if cached_date:
            return {
                "freshness_date": cached_date,
                "freshness_basis": str(item.get("freshness_basis") or "").strip(),
                "last_checked_at": existing_last_checked_at(task_name, slug),
            }

    if task_name == "gijiroku":
        info = gijiroku_target_freshness(target)
    elif task_name == "reiki":
        info = reiki_target_freshness(target)
    else:
        info = {"freshness_date": "", "freshness_basis": ""}
    info["last_checked_at"] = existing_last_checked_at(task_name, slug)
    return info


def attach_target_freshness(task_name: str, target: dict[str, Any]) -> dict[str, Any]:
    target.update(target_freshness(task_name, target))
    return target


def item_freshness(task_name: str, target: dict[str, Any]) -> dict[str, str]:
    slug = str(target.get("slug") or "").strip()
    item = status_item(task_name, slug)
    snapshot = status_item(f"{task_name}_snapshot", slug)
    return {
        "freshness_date": str(target.get("freshness_date") or item.get("freshness_date") or snapshot.get("freshness_date") or "").strip(),
        "freshness_basis": str(target.get("freshness_basis") or item.get("freshness_basis") or snapshot.get("freshness_basis") or "").strip(),
        "last_checked_at": str(target.get("last_checked_at") or item.get("last_checked_at") or snapshot.get("last_checked_at") or "").strip(),
    }


def update_check_skip_reason(task_name: str, target: dict[str, Any]) -> str:
    info = item_freshness(task_name, target)
    freshness = parse_date(info.get("freshness_date"))
    if freshness is not None and freshness >= today_tokyo() - timedelta(days=FRESHNESS_SKIP_DAYS):
        return "fresh"

    checked_at = parse_datetime_text(info.get("last_checked_at"))
    if checked_at is not None and now_tokyo() - checked_at < timedelta(hours=CHECK_INTERVAL_HOURS):
        return "recently_checked"

    return ""
