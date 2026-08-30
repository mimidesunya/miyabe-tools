#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""AmiVoice 系の議会会議録を低頻度の HTTP 取得で保存する。"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

SCRAPER_DIR = Path(__file__).resolve().parent
MODULE_DIR = SCRAPER_DIR.parent
sys.path.append(str(MODULE_DIR))
sys.path.append(str(SCRAPER_DIR))
import gijiroku_planning
import gijiroku_storage
import gijiroku_targets


USER_AGENT = "miyabe-tools/1.0 (+public municipal minutes indexer)"


@dataclass
class PeriodItem:
    title: str
    url: str
    held_on: str | None = None


@dataclass
class MeetingItem:
    title: str
    url: str
    year_label: str
    meeting_group: str | None = None
    held_on: str | None = None
    fetch_url: str = ""


def now_ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def iso_date(value: str) -> str | None:
    match = re.search(r"(19\d{2}|20\d{2})[/-]([01]?\d)[/-]([0-3]?\d)", value)
    if not match:
        return None
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def year_label(value: str, held_on: str | None = None) -> str:
    match = re.search(r"(明治|大正|昭和|平成|令和)\s*[元\d０-９]+年", value)
    if match:
        return normalize_space(match.group(0))
    if held_on:
        return held_on[:4] + "年"
    return "unknown"


def decode_response(response: requests.Response) -> str:
    encoding = response.encoding
    if not encoding or encoding.lower() in {"iso-8859-1", "ascii"}:
        encoding = response.apparent_encoding or "utf-8"
    return response.content.decode(encoding, errors="replace")


def parse_period_list(raw_html: str, page_url: str) -> tuple[list[PeriodItem], int | None]:
    soup = BeautifulSoup(raw_html, "html.parser")
    periods: list[PeriodItem] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href", ""))
        if "process=list" not in href or "vcsm=" not in href:
            continue
        row = anchor.find_parent("tr")
        cells = row.find_all("td") if row is not None else []
        held_on = iso_date(cells[0].get_text(" ", strip=True)) if cells else None
        periods.append(
            PeriodItem(
                title=normalize_space(anchor.get_text(" ", strip=True)),
                url=urljoin(page_url, href),
                held_on=held_on,
            )
        )

    next_cursor = None
    for control in soup.find_all("input", attrs={"name": True}):
        if "次" not in str(control.get("alt", "")):
            continue
        match = re.search(r"param\[process:list_vcsm,cur_id:(\d+)\]", str(control.get("name", "")))
        if match:
            next_cursor = int(match.group(1))
            break
    return periods, next_cursor


def parse_meeting_list(raw_html: str, page_url: str, period: PeriodItem) -> list[MeetingItem]:
    soup = BeautifulSoup(raw_html, "html.parser")
    meetings: list[MeetingItem] = []
    for anchor in soup.find_all("a"):
        onclick = str(anchor.get("onclick", "") or anchor.get("onClick", ""))
        match = re.search(r"DataSubmit4\(['\"]([^'\"]*process=disp_base[^'\"]*)['\"]\)", onclick, flags=re.I)
        if not match:
            continue
        detail_url = urljoin(page_url, match.group(1))
        vcsv = (parse_qs(urlsplit(detail_url).query).get("vcsv") or [""])[0]
        if not vcsv:
            continue
        row = anchor.find_parent("tr")
        cells = row.find_all("td") if row is not None else []
        held_on = iso_date(cells[0].get_text(" ", strip=True)) if cells else period.held_on
        fetch_query = urlencode(
            {
                "process": "disp_right",
                "vcsv": vcsv,
                "spk_id": "",
                "hits": "",
                "all_hits": "",
            }
        )
        meetings.append(
            MeetingItem(
                title=normalize_space(anchor.get_text(" ", strip=True)),
                url=detail_url,
                year_label=year_label(period.title, held_on),
                meeting_group=period.title,
                held_on=held_on,
                fetch_url=urljoin(page_url, f"search.exe?{fetch_query}"),
            )
        )
    return meetings


def discover_meetings(
    session: requests.Session,
    source_url: str,
    timeout_seconds: float,
    max_meetings: int,
    delay_seconds: float,
) -> list[MeetingItem]:
    search_url = urljoin(source_url, "search.exe")
    list_url = search_url + "?process=list_vcsm"
    cursor: int | None = None
    meetings: list[MeetingItem] = []
    seen_periods: set[str] = set()

    while True:
        if cursor is None:
            response = session.get(list_url, timeout=timeout_seconds)
        else:
            control = f"param[process:list_vcsm,cur_id:{cursor}]"
            response = session.post(
                search_url,
                data={f"{control}.x": "1", f"{control}.y": "1"},
                timeout=timeout_seconds,
            )
        response.raise_for_status()
        periods, next_cursor = parse_period_list(decode_response(response), response.url)

        for period in periods:
            if period.url in seen_periods:
                continue
            seen_periods.add(period.url)
            period_response = session.get(period.url, timeout=timeout_seconds)
            period_response.raise_for_status()
            meetings.extend(parse_meeting_list(decode_response(period_response), period_response.url, period))
            if max_meetings > 0 and len(meetings) >= max_meetings:
                break
            if delay_seconds > 0:
                time.sleep(delay_seconds)

        if (max_meetings > 0 and len(meetings) >= max_meetings) or next_cursor is None:
            break
        cursor = next_cursor
        if delay_seconds > 0:
            time.sleep(delay_seconds)

    unique: dict[str, MeetingItem] = {}
    for item in meetings:
        unique[item.url] = item
    return list(unique.values())


def parse_minutes_body(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")
    for element in soup.find_all(["script", "style", "noscript"]):
        element.decompose()
    content = soup.select_one(".whitebag_right") or soup.body
    if content is None:
        return ""
    lines = [normalize_space(line) for line in content.get_text("\n").splitlines()]
    return "\n".join(line for line in lines if line).strip()


def build_minutes_text(item: MeetingItem, body: str) -> str:
    held_line = f"開催日: {item.held_on}\nHeld-On: {item.held_on}\n" if item.held_on else ""
    return (
        f"{item.title}\n"
        f"{item.meeting_group or ''}\n"
        f"{item.year_label}\n"
        f"{held_line}"
        f"Source URL: {item.url}\n\n"
        f"{body.strip()}\n"
    )


def emit_progress(current: int, total: int, state_path: Path, state: dict) -> None:
    print(f"[PROGRESS] unit=meeting current={current} total={total}", flush=True)
    state["progress_current"] = current
    state["progress_total"] = total
    state["progress_unit"] = "meeting"
    gijiroku_storage.save_state(state_path, state)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AmiVoice 系の議会会議録本文を保存します。")
    parser.add_argument("--slug", default=gijiroku_targets.default_slug_for_system("amivoice"))
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--delay-seconds", type=float, default=1.5)
    parser.add_argument("--max-meetings", type=int, default=0)
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    parser.add_argument("--ack-robots", action="store_true")
    parser.add_argument("--headful", action="store_true", help="HTTP取得方式では互換性のため受け付けるだけです。")
    parser.add_argument("--save-html", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    target = gijiroku_targets.load_gijiroku_target(args.slug, expected_system="amivoice")
    if not args.ack_robots:
        print("[ERROR] robots.txt / 利用規約確認のため --ack-robots を指定してください。")
        print(f"        robots.txt: {target['robots_txt_url']}")
        return 2

    work_dir = (args.output_dir or Path(target["work_dir"])).resolve()
    downloads_dir = work_dir / "downloads" if args.output_dir is not None else Path(target["downloads_dir"])
    index_json = work_dir / "meetings_index.json" if args.output_dir is not None else Path(target["index_json_path"])
    pages_dir = work_dir / "pages"
    state_path = work_dir / "scrape_state.json"
    result_csv = work_dir / f"run_result_{now_ts()}.csv"
    work_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)
    if args.save_html:
        pages_dir.mkdir(parents=True, exist_ok=True)
    state = gijiroku_storage.load_state(state_path)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.5"})
    timeout_seconds = max(1.0, args.timeout_ms / 1000.0)

    print(f"[INFO] Target: {target['name']} ({target['slug']}, {target['system_type']})")
    print(f"[INFO] Source URL: {target['source_url']}")
    print("[INFO] 会議一覧を収集中...")
    meeting_items = discover_meetings(
        session,
        str(target["source_url"]),
        timeout_seconds,
        args.max_meetings,
        args.delay_seconds,
    )
    print(f"[INFO] 会議候補 {len(meeting_items)} 件")
    index_json.parent.mkdir(parents=True, exist_ok=True)
    gijiroku_storage.save_meetings_index(index_json, [asdict(item) for item in meeting_items])
    if args.max_meetings > 0:
        meeting_items = meeting_items[: args.max_meetings]

    plans = gijiroku_planning.build_base_plans(meeting_items, downloads_dir)
    for plan in plans:
        existing = gijiroku_storage.existing_named_outputs(plan["meeting_download_dir"], plan["stem"])
        plan["existing_outputs"] = existing
        plan["needs_work"] = not existing
    previous_missing = gijiroku_planning.previous_missing_count(state)
    plans, work_items, missing_count = gijiroku_planning.select_work_items(
        plans,
        no_resume=args.no_resume,
        previous_missing_count=previous_missing,
    )
    gijiroku_planning.save_plan_summary(state_path, state, plans, missing_count, previous_missing)
    emit_progress(len(plans) - len(work_items), len(plans), state_path, state)

    with result_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["title", "year", "url", "status", "output", "error"])
        writer.writeheader()
        for idx, plan in enumerate(work_items, start=1):
            item: MeetingItem = plan["item"]
            status = ""
            output_path = ""
            error_text = ""
            try:
                response = session.get(item.fetch_url, timeout=timeout_seconds)
                response.raise_for_status()
                raw_html = decode_response(response)
                body = parse_minutes_body(raw_html)
                if not body:
                    raise RuntimeError("会議録本文を抽出できませんでした。")
                if args.save_html:
                    page_dir = pages_dir / plan["year_dir_name"]
                    if plan["meeting_group_dir"]:
                        page_dir = page_dir / plan["meeting_group_dir"]
                    gijiroku_storage.write_text(
                        page_dir / (plan["stem"] + ".html"),
                        raw_html,
                        compress=True,
                    )
                destination = gijiroku_storage.write_text(
                    plan["meeting_download_dir"] / (plan["stem"] + ".txt"),
                    build_minutes_text(item, body),
                    compress=True,
                )
                output_path = str(destination)
                status = "saved_text"
            except Exception as exc:
                status = "error"
                error_text = str(exc)
            state["items"][plan["resume_key"]] = {
                "title": item.title,
                "year_label": item.year_label,
                "held_on": item.held_on,
                "url": item.url,
                "status": "saved" if status == "saved_text" else status,
                "output_rel_path": str(Path(output_path).relative_to(downloads_dir)) if output_path else "",
                "updated_at": now_ts(),
            }
            gijiroku_storage.save_state(state_path, state)
            writer.writerow(
                {
                    "title": item.title,
                    "year": item.year_label,
                    "url": item.url,
                    "status": status,
                    "output": output_path,
                    "error": error_text,
                }
            )
            handle.flush()
            emit_progress(len(plans) - len(work_items) + idx, len(plans), state_path, state)
            if args.delay_seconds > 0 and idx < len(work_items):
                time.sleep(args.delay_seconds)

    print(f"[DONE] Saved index: {index_json}")
    print(f"[DONE] Result log : {result_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
