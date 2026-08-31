#!/usr/bin/env python3
"""会議録候補から安定した保存計画を作る。

個別スクレイパは会議録候補の発見に集中し、このモジュールが保存ディレクトリ、
ファイル名、再開キー、既存ファイルの扱いを決める。すべての会議録スクレイパが
同じ保存規則を使えるようにするための層。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, is_dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable

import gijiroku_storage

try:
    import minutes_kind
except ModuleNotFoundError:  # pragma: no cover
    from tools.gijiroku import minutes_kind


FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
ERA_BASE_YEAR = {"明治": 1867, "大正": 1911, "昭和": 1925, "平成": 1988, "令和": 2018}
ERA_DATE_RE = re.compile(
    r"(明治|大正|昭和|平成|令和)\s*([元\d０-９]+)年"
    r"(?:\s*([0-9０-９]{1,2})月\s*([0-9０-９]{1,2})日)?"
)
MONTH_DAY_RE = re.compile(r"([0-9０-９]{1,2})月\s*([0-9０-９]{1,2})日")
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


def equivalent_year_key(name: str) -> str:
    """見た目が違っても同じ年を指すラベルを、同じ鍵に寄せる。"""
    return unicodedata.normalize("NFKC", str(name or "")).replace("元年", "1年")


def normalize_year_dir(year_label: str | None) -> str:
    label = sanitize_filename((year_label or "unknown").strip(), "unknown")
    return label or "unknown"


def existing_year_dir(downloads_dir: Path, year_dir_name: str) -> str:
    """同じ年を指す保存先が既にあるなら、その名前を使う。

    取得元は同じ年を「令和5年」「令和５年」「令和元年」と書き分ける。
    見た目の値をそのまま保存先にすると、同じ年が別の場所に割れる
    （西尾市・甲府市・広島県などで実際に起きて、古い方が孤児になった）。

    ただし、いま使っている名前を正規化した名前へ**変えてはいけない**。
    全角の年だけを使っている自治体が 890 あり、名前を変えると
    99,549 ファイルが一度に孤児になる。だから、新しく作るときだけ
    寄せて、既にある保存先はそのまま使う。
    """
    key = equivalent_year_key(year_dir_name)
    try:
        if (downloads_dir / year_dir_name).is_dir():
            return year_dir_name
        for existing in downloads_dir.iterdir():
            if existing.is_dir() and equivalent_year_key(existing.name) == key:
                return existing.name
    except OSError:
        pass
    return year_dir_name


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
    """安定ソートに使える ISO 風の日付文字列を返す。不明なら空文字。"""
    held_on = str(item_value(item, "held_on", "") or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", held_on):
        return held_on

    text = " ".join(
        str(item_value(item, key, "") or "")
        for key in ("year_label", "meeting_group", "title", "page_title")
    )

    era_year_fallback: int | None = None
    for match in ERA_DATE_RE.finditer(text):
        year = era_year_to_gregorian(match.group(1), match.group(2))
        if year is not None:
            month = japanese_year_to_int(match.group(3) or "") if match.group(3) else None
            day = japanese_year_to_int(match.group(4) or "") if match.group(4) else None
            if month and day:
                return _iso_date(year, month, day)
            if era_year_fallback is None:
                era_year_fallback = year

    match = ISO_DATE_RE.search(to_ascii_digits(text))
    if match:
        return _iso_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    if era_year_fallback is not None:
        month_day_match = MONTH_DAY_RE.search(text)
        if month_day_match:
            month = japanese_year_to_int(month_day_match.group(1))
            day = japanese_year_to_int(month_day_match.group(2))
            if month and day:
                return _iso_date(era_year_fallback, month, day)
        return f"{era_year_fallback:04d}-00-00"

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
    # 計画作成は副作用なしに保つ。
    # 呼び出し側が plan を見て通信が必要な項目を判断し、再開・優先度計算用の要約だけ保存する。
    # ここでは成果物ファイルを書き込まない。
    plans: list[dict[str, Any]] = []
    seen_output_stems: dict[tuple[str, str], int] = {}
    # 保存先の探索は会議ごとに走るので、年ラベルごとに 1 回だけにする。
    year_dir_cache: dict[str, str] = {}

    for original_idx, item in enumerate(items, start=1):
        year_dir_name = normalize_year_dir(str(item_value(item, "year_label", "") or ""))
        if year_dir_name not in year_dir_cache:
            year_dir_cache[year_dir_name] = existing_year_dir(downloads_dir, year_dir_name)
        year_dir_name = year_dir_cache[year_dir_name]
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


def retitle_plan(plan: dict[str, Any], new_title: str, *, output_key: str = "text_base") -> Any:
    """リンク文言が題名として使えないとき、本文から読んだ題名で保存名を付け直す。

    公開検索の題名は保存ファイル名から作られる。リンクが「開議」や「18日」の
    ままだと、本文が名乗っている会議名と食い違う。
    """
    item = plan["item"]
    new_title = str(new_title or "").strip()
    if not new_title:
        return item
    old_title = str(item_value(item, "title", "") or "")
    if new_title == old_title:
        return item
    if is_dataclass(item):
        try:
            item = replace(item, title=new_title)
        except TypeError:
            pass
    elif isinstance(item, dict):
        item = {**item, "title": new_title}
    else:
        try:
            item.title = new_title
        except Exception:
            pass
    plan["item"] = item
    raw_stem = sanitize_filename(new_title, "meeting")
    plan["stem"] = gijiroku_storage.disambiguated_stem(raw_stem, plan["resume_key"], 0)
    if output_key:
        attach_text_output(plan, key=output_key)
    return item


def persist_adopted_minutes(
    plan: dict[str, Any],
    extracted: str,
    *,
    compose: Callable[..., str],
    existing_output: Path | None,
    output_key: str = "text_base",
) -> dict[str, Any]:
    """本文を見て会議録か判定し、題名と開催日を直して保存する。"""
    item = plan["item"]
    adoption = minutes_kind.adopt_minutes_document(
        str(item_value(item, "title", "") or ""),
        extracted,
        url=str(item_value(item, "url", "") or ""),
        year_label=str(item_value(item, "year_label", "") or ""),
        source_year=item_value(item, "source_year", None),
    )
    if not adoption.accepted:
        if existing_output is not None:
            gijiroku_storage.quarantine_invalid_file(
                Path(existing_output), reason=adoption.reason or "not_minutes"
            )
        return {
            "status": "skipped_not_minutes",
            "output_path": "",
            "item": item,
            "adoption": adoption,
            "reason": adoption.reason,
        }
    existing_text = extracted if existing_output is not None else ""
    already_good = (
        existing_output is not None
        and adoption.title == str(item_value(item, "title", "") or "")
        and (
            not adoption.held_on
            or f"Held-On: {adoption.held_on}" in existing_text
        )
    )
    if already_good:
        return {
            "status": "skipped_existing",
            "output_path": str(existing_output),
            "item": item,
            "adoption": adoption,
            "reason": None,
        }
    old_existing = existing_output
    item = retitle_plan(plan, adoption.title, output_key=output_key)
    composed = compose(item, extracted, held_on=adoption.held_on)
    dest = gijiroku_storage.write_text(plan[output_key], composed, compress=True)
    if old_existing is not None:
        try:
            old_path = Path(old_existing).resolve()
            new_path = Path(dest).resolve()
            if old_path != new_path and old_path.exists():
                gijiroku_storage.quarantine_invalid_file(old_path, reason="retitle")
        except Exception:
            pass
    return {
        "status": "saved_text",
        "output_path": str(dest),
        "item": item,
        "adoption": adoption,
        "reason": None,
    }


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
    # scrape_state.json は子スクレイパと親バッチ runner の橋渡し。
    # 親が provider 固有ファイルを読まずに進捗を出せるよう、計画の要点だけを保存する。
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
