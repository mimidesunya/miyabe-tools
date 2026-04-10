#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

sys.path.append(str(Path(__file__).parent))
import build_minutes_index as minutes_index_builder
import gijiroku_storage
import gijiroku_targets


DEFAULT_WAIT_MS = 10_000
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)
POWER_USER_VALUE = "false"


@dataclass
class MeetingItem:
    title: str
    url: str
    year_label: str
    meeting_group: str | None = None
    tenant_id: int | None = None
    council_id: int | None = None
    schedule_id: int | None = None


def now_ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def emit_progress(
    current: int,
    total: int,
    state_path: Path | None = None,
    state: dict | None = None,
) -> None:
    print(f"[PROGRESS] unit=meeting current={max(0, current)} total={max(0, total)}", flush=True)
    if state_path is not None:
        if state is not None:
            state["progress_current"] = max(0, int(current))
            state["progress_total"] = max(0, int(total))
            state["progress_unit"] = "meeting"
            gijiroku_storage.save_state(state_path, state)
        else:
            gijiroku_storage.update_progress_state(state_path, current=current, total=total, unit="meeting")


def sanitize_filename(text: str, fallback: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\t\r\n]+", "_", text).strip(" .")
    if not cleaned:
        return fallback
    return cleaned[:180]


def normalize_year_dir(year_label: str) -> str:
    label = sanitize_filename((year_label or "unknown").strip(), "unknown")
    if not label:
        return "unknown"
    return label


def normalize_space(value: str) -> str:
    return re.sub(r"[ \t\u3000]+", " ", value).strip()


def normalize_meeting_group_dir(meeting_group: str | None) -> str:
    if not meeting_group:
        return ""
    return sanitize_filename(meeting_group, "meeting")


def html_to_text(raw_html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", "", raw_html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|tr|table|h[1-6]|pre)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def to_ascii_digits(value: str) -> str:
    return value.translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def schedule_date_label(title: str, year_label: str) -> str | None:
    year_match = re.search(r"(昭和|平成|令和)\s*([元\d０-９]+)年", year_label)
    day_match = re.search(r"([\d０-９]{1,2})月([\d０-９]{1,2})日", title)
    if not year_match or not day_match:
        return None
    year_text = to_ascii_digits(year_match.group(2))
    month_text = to_ascii_digits(day_match.group(1))
    day_text = to_ascii_digits(day_match.group(2))
    return f"{year_match.group(1)}{year_text}年{month_text}月{day_text}日"


def source_api_root(source_url: str) -> str:
    parts = urlsplit(source_url)
    return urlunsplit((parts.scheme or "https", parts.netloc, "/dnp/search/", "", ""))


def tenant_base_url(source_url: str) -> str:
    parts = urlsplit(source_url)
    path = parts.path or "/"
    if path.endswith("/"):
        base_path = path
    else:
        base_path = path.rsplit("/", 1)[0] + "/"
    return urlunsplit((parts.scheme or "https", parts.netloc, base_path, "", ""))


def build_schedule_url(source_url: str, tenant_id: int, council_id: int, schedule_id: int) -> str:
    base_url = tenant_base_url(source_url)
    query = urlencode({"tenant_id": tenant_id, "council_id": council_id, "schedule_id": schedule_id})
    return urljoin(base_url, f"MinuteView.html?{query}")


def safe_json_loads(text: str) -> dict:
    loaded = json.loads(text)
    return loaded if isinstance(loaded, dict) else {}


def load_tenant_id(page, source_url: str, timeout_ms: int) -> int:
    page.goto(source_url, wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=5_000)
    except Exception:
        pass

    serialized = page.evaluate("JSON.stringify((window.dnp && dnp.params) || {})")
    params = safe_json_loads(serialized)
    tenant_id = params.get("tenant_id")
    if tenant_id is None:
        raise RuntimeError("tenant_id を取得できませんでした。")
    return int(tenant_id)


def api_post(request_context, api_root: str, path: str, payload: dict[str, object], timeout_ms: int, referer: str) -> dict:
    body = {key: str(value) for key, value in payload.items() if value is not None}
    last_error: Exception | None = None

    for attempt in range(3):
        try:
            response = request_context.post(
                urljoin(api_root, path),
                form=body,
                headers={"referer": referer},
                timeout=timeout_ms,
            )
            if not response.ok:
                raise RuntimeError(f"{path} returned HTTP {response.status}")
            return safe_json_loads(response.text())
        except Exception as exc:
            last_error = exc
            time.sleep(1.0 + attempt)

    raise RuntimeError(f"{path} の取得に失敗しました: {last_error}")


def deepest_council_group(council_type: dict) -> str | None:
    names = [
        normalize_space(str(council_type.get(key, "") or ""))
        for key in (
            "council_type_name5",
            "council_type_name4",
            "council_type_name3",
            "council_type_name2",
        )
    ]
    for name in names:
        if name and name not in {"全会議", "資料"}:
            return name
    return None


def fetch_schedule_items_for_council(page, api_root: str, source_url: str, timeout_ms: int, tenant_id: int, council_id: int, council_title: str, year_label: str) -> list[MeetingItem]:
    schedule_data = api_post(
        page.request,
        api_root,
        "minutes/get_schedule_all",
        {
            "tenant_id": tenant_id,
            "power_user": POWER_USER_VALUE,
            "council_id": council_id,
        },
        timeout_ms,
        referer=build_schedule_url(source_url, tenant_id, council_id, 1),
    )
    rows = schedule_data.get("schedules_and_materials", [])
    if not isinstance(rows, list):
        rows = []

    items: list[MeetingItem] = []
    for row in sorted(
        [entry for entry in rows if isinstance(entry, dict)],
        key=lambda entry: (int(entry.get("page_no") or 0), int(entry.get("schedule_id") or 0)),
    ):
        schedule_id = int(row.get("schedule_id") or 0)
        if schedule_id <= 0:
            continue
        title = normalize_space(str(row.get("name", "")).strip())
        if title == "":
            title = f"schedule-{schedule_id}"
        items.append(
            MeetingItem(
                title=title,
                url=build_schedule_url(source_url, tenant_id, council_id, schedule_id),
                year_label=year_label,
                meeting_group=council_title,
                tenant_id=tenant_id,
                council_id=council_id,
                schedule_id=schedule_id,
            )
        )
    return items


def discover_meeting_items(page, target: dict, timeout_ms: int, max_years: int) -> tuple[int, list[MeetingItem]]:
    source_url = str(target["source_url"])
    tenant_id = load_tenant_id(page, source_url, timeout_ms)
    api_root = source_api_root(source_url)

    year_data = api_post(
        page.request,
        api_root,
        "councils/get_view_years",
        {"tenant_id": tenant_id, "power_user": POWER_USER_VALUE},
        timeout_ms,
        referer=source_url,
    )
    year_rows = year_data.get("view_years", [])
    if not isinstance(year_rows, list):
        year_rows = []
    if max_years > 0:
        year_rows = year_rows[:max_years]

    meetings: list[MeetingItem] = []
    seen_schedule_keys: set[tuple[int, int]] = set()

    for year_row in year_rows:
        if not isinstance(year_row, dict):
            continue

        view_year = str(year_row.get("view_year", "")).strip()
        year_label = normalize_space(str(year_row.get("japanese_year", "")).strip()) or view_year or "不明"

        council_data = api_post(
            page.request,
            api_root,
            "councils/index",
            {
                "tenant_id": tenant_id,
                "power_user": POWER_USER_VALUE,
                "view_years": view_year,
            },
            timeout_ms,
            referer=source_url,
        )

        roots = council_data.get("councils", [])
        if not isinstance(roots, list):
            continue

        for root in roots:
            if not isinstance(root, dict):
                continue
            view_year_entries = root.get("view_years", [])
            if not isinstance(view_year_entries, list):
                continue
            for view_entry in view_year_entries:
                if not isinstance(view_entry, dict):
                    continue
                if view_year and str(view_entry.get("view_year", "")).strip() not in {"", view_year}:
                    continue
                council_types = view_entry.get("council_type", [])
                if not isinstance(council_types, list):
                    continue
                for council_type in council_types:
                    if not isinstance(council_type, dict):
                        continue
                    council_type_path = str(council_type.get("council_type_path", "")).strip()
                    if not council_type_path.startswith("/0/1/"):
                        continue
                    meeting_group = deepest_council_group(council_type)
                    councils = council_type.get("councils", [])
                    if not isinstance(councils, list):
                        continue
                    for council in councils:
                        if not isinstance(council, dict):
                            continue
                        council_id = int(council.get("council_id") or 0)
                        if council_id <= 0:
                            continue
                        council_title = normalize_space(str(council.get("name", "")).strip())
                        if council_title == "":
                            council_title = meeting_group or f"council-{council_id}"
                        for item in fetch_schedule_items_for_council(
                            page,
                            api_root,
                            source_url,
                            timeout_ms,
                            tenant_id,
                            council_id,
                            council_title,
                            year_label,
                        ):
                            if item.schedule_id is None:
                                continue
                            schedule_key = (council_id, item.schedule_id)
                            if schedule_key in seen_schedule_keys:
                                continue
                            seen_schedule_keys.add(schedule_key)
                            meetings.append(item)

    return tenant_id, meetings


def fragment_to_text(title: str, body_html: str, page_no: int | None) -> str:
    lines: list[str] = []
    clean_title = normalize_space(title)
    if clean_title:
        if page_no is not None:
            lines.append(f"{clean_title} [p.{page_no}]")
        else:
            lines.append(clean_title)
        lines.append("-" * min(max(len(clean_title), 8), 40))
    body_text = html_to_text(body_html)
    if body_text:
        lines.append(body_text)
    return "\n".join(lines).strip()


def fetch_council_index_text(page, api_root: str, item: MeetingItem, timeout_ms: int) -> str:
    if item.tenant_id is None or item.council_id is None:
        return ""
    data = api_post(
        page.request,
        api_root,
        "minutes/get_index",
        {
            "tenant_id": item.tenant_id,
            "power_user": POWER_USER_VALUE,
            "council_id": item.council_id,
        },
        timeout_ms,
        referer=item.url,
    )
    council_index = data.get("councilIndex", {})
    if not isinstance(council_index, dict):
        return ""
    return html_to_text(str(council_index.get("council_index", "") or ""))


def fetch_schedule_minutes(page, api_root: str, item: MeetingItem, timeout_ms: int) -> tuple[int, str]:
    if item.tenant_id is None or item.council_id is None or item.schedule_id is None:
        return 0, ""

    if "目次" in item.title:
        index_text = fetch_council_index_text(page, api_root, item, timeout_ms)
        if index_text:
            return 1, index_text

    minute_data = api_post(
        page.request,
        api_root,
        "minutes/get_minute",
        {
            "tenant_id": item.tenant_id,
            "power_user": POWER_USER_VALUE,
            "council_id": item.council_id,
            "schedule_id": item.schedule_id,
        },
        timeout_ms,
        referer=item.url,
    )
    tenant_minutes = minute_data.get("tenant_minutes", [])
    if not isinstance(tenant_minutes, list):
        tenant_minutes = []

    fragment_sections: list[str] = []
    fragment_count = 0
    for minute in tenant_minutes:
        if not isinstance(minute, dict):
            continue
        text = fragment_to_text(
            str(minute.get("title", "") or ""),
            str(minute.get("body", "") or ""),
            int(minute.get("page_no")) if minute.get("page_no") is not None else None,
        )
        if text:
            fragment_sections.append(text)
            fragment_count += 1

    if not fragment_sections:
        fallback_data = api_post(
            page.request,
            api_root,
            "minutes/get_schedule",
            {
                "tenant_id": item.tenant_id,
                "power_user": POWER_USER_VALUE,
                "council_id": item.council_id,
                "schedule_id": item.schedule_id,
            },
            timeout_ms,
            referer=item.url,
        )
        fallback_rows = fallback_data.get("council_schedules", [])
        if isinstance(fallback_rows, list):
            for row in fallback_rows:
                if not isinstance(row, dict):
                    continue
                if int(row.get("schedule_id") or 0) != item.schedule_id:
                    continue
                fallback_text = html_to_text(str(row.get("member_list", "") or ""))
                if fallback_text:
                    fragment_sections.append(fallback_text)
                    fragment_count = 1
                    break

    if not fragment_sections:
        return 0, ""

    return fragment_count, "\n\n".join(fragment_sections).strip()


def build_meeting_text(item: MeetingItem, section_text: str) -> str:
    header_lines = [item.title]
    if item.meeting_group:
        header_lines.append(item.meeting_group)
    header_lines.append(item.year_label)
    held_on_label = schedule_date_label(item.title, item.year_label)
    if held_on_label:
        header_lines.append(f"開催日: {held_on_label}")
    header_lines.append(f"Source URL: {item.url}")
    header_lines.append("")
    if section_text:
        header_lines.append(section_text)
    return "\n".join(header_lines).strip() + "\n"


def save_debug_json(path: Path, data: dict) -> None:
    gijiroku_storage.write_json(path, data, compress=True)


def build_parser() -> argparse.ArgumentParser:
    default_slug = gijiroku_targets.default_slug_for_system("kaigiroku.net")
    parser = argparse.ArgumentParser(
        description="kaigiroku.net 系の議会会議録 API を使って、会議録本文を保存します。"
    )
    parser.add_argument(
        "--slug",
        default=default_slug,
        help="自治体slug。data/municipalities から対象を解決します。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="取得データの保存先ディレクトリ（未指定時は slug 規約から自動決定）",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="ブラウザを表示して実行（デフォルトはヘッドレス）",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=1.5,
        help="各会議アクセス間の待機秒数（サーバー負荷軽減）",
    )
    parser.add_argument(
        "--max-meetings",
        type=int,
        default=0,
        help="処理件数上限（0は無制限）",
    )
    parser.add_argument(
        "--max-years",
        type=int,
        default=0,
        help="取得対象年数上限（0は無制限。新しい年から順に処理）",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=DEFAULT_WAIT_MS,
        help="Playwright/API 操作タイムアウト（ミリ秒）",
    )
    parser.add_argument(
        "--ack-robots",
        action="store_true",
        help="robots.txt・利用規約・許諾確認済みとして実行する",
    )
    parser.add_argument(
        "--save-debug-json",
        action="store_true",
        help="失敗時や調査用に API レスポンス断片を保存する",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="既存の保存結果を無視して最初から取り直す",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    target = gijiroku_targets.load_gijiroku_target(args.slug, expected_system="kaigiroku.net")

    if not args.ack_robots:
        print("[ERROR] robots.txt / 利用規約確認のため --ack-robots を指定してください。")
        print(f"        robots.txt: {target['robots_txt_url']}")
        return 2

    output_dir: Path = (args.output_dir or target["data_dir"]).resolve()
    work_dir: Path = (args.output_dir or target["work_dir"]).resolve()
    downloads_dir = (
        (output_dir / "downloads").resolve()
        if args.output_dir is not None
        else Path(target["downloads_dir"]).resolve()
    )
    index_json = (
        (output_dir / "meetings_index.json").resolve()
        if args.output_dir is not None
        else Path(target["index_json_path"]).resolve()
    )
    output_db = (
        (output_dir / "minutes.sqlite").resolve()
        if args.output_dir is not None
        else Path(target["db_path"]).resolve()
    )
    debug_dir = work_dir / "pages"
    result_csv = work_dir / f"run_result_{now_ts()}.csv"
    state_path = work_dir / "scrape_state.json"
    state = gijiroku_storage.load_state(state_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)
    if args.save_debug_json:
        debug_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] Target: {target['name']} ({target['slug']}, {target['system_type']})")
    print(f"[INFO] Source URL: {target['source_url']}")
    print(f"[INFO] Base URL: {target['base_url']}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headful)
        context = browser.new_context(accept_downloads=False, locale="ja-JP", user_agent=DEFAULT_USER_AGENT)
        page = context.new_page()
        page.set_default_timeout(args.timeout_ms)

        print("[INFO] 会議一覧を収集中...")
        tenant_id, meeting_items = discover_meeting_items(page, target, args.timeout_ms, args.max_years)
        print(f"[INFO] tenant_id={tenant_id}")
        print(f"[INFO] 会議候補 {len(meeting_items)} 件")

        if args.max_meetings > 0:
            meeting_items = meeting_items[: args.max_meetings]

        index_json.parent.mkdir(parents=True, exist_ok=True)
        index_json.write_text(
            json.dumps([asdict(item) for item in meeting_items], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        minutes_meta_map = minutes_index_builder.parse_source_meta(index_json)
        indexing_enabled = minutes_index_builder.prepare_incremental_index(
            output_db,
            logger=lambda message: print(message, flush=True),
            context=str(target["slug"]),
        )
        emit_progress(0, len(meeting_items), state_path, state)

        api_root = source_api_root(str(target["source_url"]))

        with result_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["title", "year", "url", "status", "output", "error", "schedules", "fragments"],
            )
            writer.writeheader()

            for idx, item in enumerate(meeting_items, start=1):
                print(f"[{idx}/{len(meeting_items)}] {item.year_label} {item.title}")
                status = ""
                output_path = ""
                error_msg = ""
                schedule_count = 0
                fragment_count = 0
                year_dir_name = normalize_year_dir(item.year_label)
                meeting_group_dir = normalize_meeting_group_dir(item.meeting_group)
                meeting_download_dir = downloads_dir / year_dir_name
                if meeting_group_dir:
                    meeting_download_dir = meeting_download_dir / meeting_group_dir
                meeting_download_dir.mkdir(parents=True, exist_ok=True)
                stem = sanitize_filename(item.title, "meeting")
                resume_key = gijiroku_storage.item_signature(asdict(item))
                existing_outputs = gijiroku_storage.existing_named_outputs(meeting_download_dir, stem)
                if not args.no_resume and existing_outputs:
                    output_path = str(existing_outputs[0])
                    status = "skipped_existing"
                    state["items"][resume_key] = {
                        "title": item.title,
                        "year_label": item.year_label,
                        "url": item.url,
                        "status": "saved",
                        "output_rel_path": str(existing_outputs[0].relative_to(downloads_dir)),
                        "updated_at": now_ts(),
                    }
                    gijiroku_storage.save_state(state_path, state)
                    index_result = minutes_index_builder.best_effort_upsert_source_file(
                        output_db,
                        downloads_dir,
                        existing_outputs[0],
                        meta_map=minutes_meta_map,
                        logger=lambda message: print(message, flush=True),
                        context=f"{target['slug']} {item.title}",
                    )
                    if index_result == "error":
                        indexing_enabled = False
                    writer.writerow(
                        {
                            "title": item.title,
                            "year": item.year_label,
                            "url": item.url,
                            "status": status,
                            "output": output_path,
                            "error": "",
                            "schedules": 0,
                            "fragments": 0,
                        }
                    )
                    handle.flush()
                    emit_progress(idx, len(meeting_items), state_path, state)
                    continue

                try:
                    if item.tenant_id is None or item.council_id is None or item.schedule_id is None:
                        raise RuntimeError("meeting item に tenant_id / council_id / schedule_id がありません。")

                    schedule_count = 1
                    fragment_count, section_text = fetch_schedule_minutes(page, api_root, item, args.timeout_ms)

                    if not section_text:
                        status = "not_found"
                    else:
                        meeting_text = build_meeting_text(item, section_text)
                        dest = gijiroku_storage.write_text(
                            meeting_download_dir / (sanitize_filename(item.title, "meeting") + ".txt"),
                            meeting_text,
                            compress=True,
                        )
                        output_path = str(dest)
                        status = "saved_text"
                except PlaywrightTimeoutError as exc:
                    status = "timeout"
                    error_msg = str(exc)
                except Exception as exc:
                    status = "error"
                    error_msg = str(exc)
                    if args.save_debug_json:
                        debug_path = debug_dir / year_dir_name
                        if meeting_group_dir:
                            debug_path = debug_path / meeting_group_dir
                        debug_path = debug_path / (sanitize_filename(item.title, "meeting") + ".json")
                        save_debug_json(
                            debug_path,
                            {
                                "title": item.title,
                                "year_label": item.year_label,
                                "url": item.url,
                                "tenant_id": item.tenant_id,
                                "council_id": item.council_id,
                                "error": error_msg,
                            },
                        )

                state["items"][resume_key] = {
                    "title": item.title,
                    "year_label": item.year_label,
                    "url": item.url,
                    "status": status,
                    "output_rel_path": str(Path(output_path).relative_to(downloads_dir)) if output_path else "",
                    "updated_at": now_ts(),
                }
                gijiroku_storage.save_state(state_path, state)
                if indexing_enabled and output_path:
                    indexed_output = Path(output_path)
                    if gijiroku_storage.logical_suffix(indexed_output) in {".txt", ".html", ".htm"}:
                        index_result = minutes_index_builder.best_effort_upsert_source_file(
                            output_db,
                            downloads_dir,
                            indexed_output,
                            meta_map=minutes_meta_map,
                            logger=lambda message: print(message, flush=True),
                            context=f"{target['slug']} {item.title}",
                        )
                        if index_result == "error":
                            indexing_enabled = False

                writer.writerow(
                    {
                        "title": item.title,
                        "year": item.year_label,
                        "url": item.url,
                        "status": status,
                        "output": output_path,
                        "error": error_msg,
                        "schedules": schedule_count,
                        "fragments": fragment_count,
                    }
                )
                handle.flush()
                emit_progress(idx, len(meeting_items), state_path, state)
                if args.delay_seconds > 0 and idx < len(meeting_items):
                    time.sleep(args.delay_seconds)

        if indexing_enabled:
            minutes_index_builder.finalize_incremental_index(
                output_db,
                logger=lambda message: print(message, flush=True),
                context=str(target["slug"]),
            )
        browser.close()

    print(f"[DONE] Saved index: {index_json}")
    print(f"[DONE] Result log : {result_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
