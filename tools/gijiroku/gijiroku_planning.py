#!/usr/bin/env python3
from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable

import gijiroku_storage


FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
ERA_BASE_YEAR = {"明治": 1867, "大正": 1911, "昭和": 1925, "平成": 1988, "令和": 2018}
ERA_DATE_RE = re.compile(
    r"(明治|大正|昭和|平成|令和)\s*([元\d０-９]+)年"
    r"(?:\s*([0-9０-９]{1,2})月\s*([0-9０-９]{1,2})日)?"
)
ISO_DATE_RE = re.compile(r"\b(19\d{2}|20\d{2})[-/.年]\s*([01]?\d)[-/.月]\s*([0-3]?\d)日?\b")
WESTERN_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
MAX_FILENAME_BYTES = 180


def truncate_utf8_bytes(value: str, max_bytes: int = MAX_FILENAME_BYTES) -> str:
    value = str(value or "")
    if len(value.encode("utf-8", errors="ignore")) <= max_bytes:
        return value
    output: list[str] = []
    used = 0
    for char in value:
        size = len(char.encode("utf-8", errors="ignore"))
        if used + size > max_bytes:
            break
        output.append(char)
        used += size
    return "".join(output).rstrip(" .") or value[:1]


def sanitize_filename(text: str, fallback: str = "meeting") -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\t\r\n]+", "_", str(text or "")).strip(" .")
    if not cleaned:
        return fallback
    return truncate_utf8_bytes(cleaned)


def normalize_year_dir(year_label: str | None) -> str:
    label = sanitize_filename((year_label or "unknown").strip(), "unknown")
    return label or "unknown"


def normalize_meeting_group_dir(meeting_group: str | None) -> str:
    if not meeting_group:
        return ""
    return sanitize_filename(meeting_group, "meeting")


def item_payload(item: Any) -> dict[str, Any]:
    if is_dataclass(item):
        payload = asdict(item)
    elif isinstance(item, dict):
        payload = dict(item)
    else:
        payload = {
            key: getattr(item, key)
            for key in dir(item)
            if not key.startswith("_") and not callable(getattr(item, key))
        }
    return payload


def item_value(item: Any, name: str, default: Any = "") -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def to_ascii_digits(value: str) -> str:
    return str(value).translate(FULLWIDTH_DIGITS)


def japanese_year_to_int(value: str) -> int | None:
    raw = to_ascii_digits(value).strip()
    if raw == "元":
        return 1
    if raw.isdigit():
        return int(raw)
    return None


def era_year_to_gregorian(era: str, year_text: str) -> int | None:
    year = japanese_year_to_int(year_text)
    if year is None or era not in ERA_BASE_YEAR:
        return None
    return ERA_BASE_YEAR[era] + year


def _iso_date(year: int, month: int, day: int) -> str:
    return f"{year:04d}-{month:02d}-{day:02d}"


def infer_sort_date(item: Any) -> str:
    """Return an ISO-ish date string usable for stable ordering, or empty if unknown."""
    held_on = str(item_value(item, "held_on", "") or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", held_on):
        return held_on

    text = " ".join(
        str(item_value(item, key, "") or "")
        for key in ("year_label", "meeting_group", "title", "page_title")
    )

    match = ERA_DATE_RE.search(text)
    if match:
        year = era_year_to_gregorian(match.group(1), match.group(2))
        if year is not None:
            month = japanese_year_to_int(match.group(3) or "") if match.group(3) else None
            day = japanese_year_to_int(match.group(4) or "") if match.group(4) else None
            if month and day:
                return _iso_date(year, month, day)
            return f"{year:04d}-00-00"

    match = ISO_DATE_RE.search(to_ascii_digits(text))
    if match:
        return _iso_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    source_year = item_value(item, "source_year", None)
    if isinstance(source_year, int) and source_year > 0:
        return f"{source_year:04d}-00-00"

    match = WESTERN_YEAR_RE.search(to_ascii_digits(text))
    if match:
        return f"{int(match.group(1)):04d}-00-00"

    return ""


def build_base_plans(
    items: Iterable[Any],
    downloads_dir: Path,
    *,
    use_group_dir: bool = True,
    fallback_stem: str = "meeting",
    mkdir: bool = True,
) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    seen_output_stems: dict[tuple[str, str], int] = {}

    for original_idx, item in enumerate(items, start=1):
        year_dir_name = normalize_year_dir(str(item_value(item, "year_label", "") or ""))
        meeting_group_dir = (
            normalize_meeting_group_dir(item_value(item, "meeting_group", None)) if use_group_dir else ""
        )
        meeting_download_dir = downloads_dir / year_dir_name
        if meeting_group_dir:
            meeting_download_dir = meeting_download_dir / meeting_group_dir
        if mkdir:
            meeting_download_dir.mkdir(parents=True, exist_ok=True)

        raw_stem = sanitize_filename(str(item_value(item, "title", "") or ""), fallback_stem)
        payload = item_payload(item)
        resume_key = gijiroku_storage.item_signature(payload)
        stem_scope = (str(meeting_download_dir.relative_to(downloads_dir)), raw_stem)
        occurrence_index = seen_output_stems.get(stem_scope, 0)
        seen_output_stems[stem_scope] = occurrence_index + 1
        stem = gijiroku_storage.disambiguated_stem(raw_stem, resume_key, occurrence_index)

        plans.append(
            {
                "item": item,
                "original_idx": original_idx,
                "year_dir_name": year_dir_name,
                "year_dir": year_dir_name,
                "meeting_group_dir": meeting_group_dir,
                "meeting_download_dir": meeting_download_dir,
                "stem": stem,
                "resume_key": resume_key,
                "sort_date": infer_sort_date(item),
            }
        )

    return plans


def attach_text_output(plan: dict[str, Any], *, key: str = "dest_base") -> dict[str, Any]:
    dest_base = plan["meeting_download_dir"] / (plan["stem"] + ".txt")
    plan[key] = dest_base
    plan["existing_output"] = gijiroku_storage.existing_output(dest_base)
    plan["needs_work"] = plan["existing_output"] is None
    return plan


def attach_named_outputs(plan: dict[str, Any]) -> dict[str, Any]:
    existing_outputs = gijiroku_storage.existing_named_outputs(plan["meeting_download_dir"], plan["stem"])
    plan["existing_outputs"] = existing_outputs
    plan["needs_work"] = not existing_outputs
    return plan


def resume_sort_key(entry: dict[str, Any]) -> tuple[int, int, str, int]:
    if not entry.get("needs_work"):
        return (1, 1, "", int(entry.get("original_idx", 0)))
    sort_date = str(entry.get("sort_date") or "")
    if not sort_date:
        return (0, 1, "", int(entry.get("original_idx", 0)))
    return (0, 0, sort_date, int(entry.get("original_idx", 0)))


def select_work_items(
    planned_items: list[dict[str, Any]],
    *,
    no_resume: bool = False,
    previous_missing_count: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    missing_count = sum(1 for entry in planned_items if entry.get("needs_work"))
    if no_resume:
        ordered = sorted(planned_items, key=lambda entry: int(entry.get("original_idx", 0)))
        return ordered, ordered, missing_count
    if previous_missing_count == 0 and missing_count > 0:
        missing = [entry for entry in planned_items if entry.get("needs_work")]
        existing = [entry for entry in planned_items if not entry.get("needs_work")]
        missing_ordered = sorted(
            missing,
            key=lambda entry: (
                str(entry.get("sort_date") or ""),
                -int(entry.get("original_idx", 0)),
            ),
            reverse=True,
        )
        ordered = missing_ordered + sorted(existing, key=lambda entry: int(entry.get("original_idx", 0)))
        return ordered, missing_ordered, missing_count
    ordered = sorted(planned_items, key=resume_sort_key)
    work_items = [entry for entry in ordered if entry.get("needs_work")]
    return ordered, work_items, missing_count


def previous_missing_count(state: dict[str, Any]) -> int | None:
    summary = state.get("plan_summary")
    if not isinstance(summary, dict):
        return None
    try:
        return int(summary.get("missing_total"))
    except Exception:
        return None


def work_mode_label(missing_count: int, previous_missing: int | None) -> str:
    if missing_count <= 0:
        return "up_to_date"
    if previous_missing == 0:
        return "update_check"
    return "resume"


def describe_date_range(planned_items: Iterable[dict[str, Any]]) -> str:
    dates = sorted(
        str(entry.get("sort_date") or "")
        for entry in planned_items
        if str(entry.get("sort_date") or "")
    )
    if not dates:
        return ""
    return f"{dates[0]}..{dates[-1]}"


def summarize_plans(planned_items: Iterable[dict[str, Any]], missing_count: int) -> dict[str, Any]:
    plans = list(planned_items)
    dates = sorted(str(entry.get("sort_date") or "") for entry in plans if str(entry.get("sort_date") or ""))
    day_dates = [date for date in dates if not date.endswith("-00-00")]
    year_dates = [date for date in dates if date.endswith("-00-00")]
    source_order = source_date_order(plans)
    return {
        "discovered_total": len(plans),
        "existing_total": max(0, len(plans) - int(missing_count)),
        "missing_total": max(0, int(missing_count)),
        "known_date_total": len(dates),
        "known_day_date_total": len(day_dates),
        "known_year_date_total": len(year_dates),
        "known_date_ratio": (len(dates) / len(plans)) if plans else 0,
        "date_precision": date_precision_label(day_dates, year_dates, len(plans)),
        "date_min": dates[0] if dates else "",
        "date_max": dates[-1] if dates else "",
        "source_date_order": source_order,
        "source_order_trustworthy": source_order in {"ascending", "descending"} and len(dates) == len(plans),
    }


def save_plan_summary(
    state_path: Path,
    state: dict[str, Any],
    planned_items: Iterable[dict[str, Any]],
    missing_count: int,
    previous_missing: int | None = None,
) -> None:
    state["plan_summary"] = summarize_plans(planned_items, missing_count)
    state["plan_summary"]["work_mode"] = work_mode_label(missing_count, previous_missing)
    gijiroku_storage.save_state(state_path, state)


def date_precision_label(day_dates: list[str], year_dates: list[str], total: int) -> str:
    if total <= 0:
        return "none"
    if len(day_dates) == total:
        return "day"
    if len(day_dates) + len(year_dates) == total:
        return "mixed" if day_dates and year_dates else "year"
    if day_dates:
        return "partial_day"
    if year_dates:
        return "partial_year"
    return "none"


def source_date_order(planned_items: Iterable[dict[str, Any]]) -> str:
    dates = [str(entry.get("sort_date") or "") for entry in planned_items if str(entry.get("sort_date") or "")]
    if len(dates) < 2:
        return "unknown"
    ascending = all(left <= right for left, right in zip(dates, dates[1:]))
    descending = all(left >= right for left, right in zip(dates, dates[1:]))
    if ascending and descending:
        return "flat"
    if ascending:
        return "ascending"
    if descending:
        return "descending"
    return "mixed"
