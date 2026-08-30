#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""東根市 msearch 系の静的会議録目次と本文を保存する。"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

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
MINUTES_FILE_RE = re.compile(r"^r\d{4}[tr]\d+\.html?$", flags=re.I)
FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
ERA_BASE = {"明治": 1867, "大正": 1911, "昭和": 1925, "平成": 1988, "令和": 2018}


@dataclass
class MeetingItem:
    title: str
    url: str
    year_label: str
    meeting_group: str | None = None
    held_on: str | None = None


def now_ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def decode_response(response: requests.Response) -> str:
    for encoding in ("utf-8", "cp932", "shift_jis"):
        try:
            return response.content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return response.content.decode(response.apparent_encoding or "utf-8", errors="replace")


def era_year(value: str) -> tuple[str, int] | None:
    match = re.search(r"(明治|大正|昭和|平成|令和)\s*([元\d０-９]+)年", value)
    if not match:
        return None
    raw_year = match.group(2).translate(FULLWIDTH_DIGITS)
    year_number = 1 if raw_year == "元" else int(raw_year)
    return normalize_space(match.group(0)), ERA_BASE[match.group(1)] + year_number


def held_on_from_row(row_text: str, group: str) -> str | None:
    era = era_year(group)
    match = re.search(r"([0-9０-９]{1,2})月\s*([0-9０-９]{1,2})日", row_text)
    if era is None or match is None:
        return None
    month = int(match.group(1).translate(FULLWIDTH_DIGITS))
    day = int(match.group(2).translate(FULLWIDTH_DIGITS))
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return f"{era[1]:04d}-{month:02d}-{day:02d}"


def static_index_url(source_url: str) -> str:
    parts = urlsplit(source_url)
    if parts.path.lower().endswith("/kensaku/mokuji.html"):
        return source_url
    return f"{parts.scheme or 'https'}://{parts.netloc}/kensaku/mokuji.html"


def parse_index(raw_html: str, index_url: str) -> list[MeetingItem]:
    soup = BeautifulSoup(raw_html, "html.parser")
    meetings: list[MeetingItem] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href", ""))
        filename = Path(urlsplit(href).path).name
        if not MINUTES_FILE_RE.fullmatch(filename):
            continue
        group_node = anchor.find_previous(
            lambda tag: tag.name in {"b", "strong"}
            and re.search(r"(明治|大正|昭和|平成|令和).+第.+回", tag.get_text(" ", strip=True))
        )
        group = normalize_space(group_node.get_text(" ", strip=True)).strip("【】 ") if group_node else ""
        title = normalize_space(anchor.get_text(" ", strip=True)) or filename
        row = anchor.find_parent("tr")
        row_text = normalize_space(row.get_text(" ", strip=True)) if row is not None else title
        era = era_year(group or row_text)
        meetings.append(
            MeetingItem(
                title=f"{group} {title}".strip(),
                url=urljoin(index_url, href),
                year_label=era[0] if era else "unknown",
                meeting_group=group or None,
                held_on=held_on_from_row(row_text, group),
            )
        )

    unique: dict[str, MeetingItem] = {}
    for item in meetings:
        unique[item.url] = item
    return list(unique.values())


def parse_body(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")
    for element in soup.find_all(["script", "style", "noscript"]):
        element.decompose()
    if soup.body is None:
        return ""
    lines = [normalize_space(line) for line in soup.body.get_text("\n").splitlines()]
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
    parser = argparse.ArgumentParser(description="msearch 系の静的会議録本文を保存します。")
    parser.add_argument("--slug", default=gijiroku_targets.default_slug_for_system("msearch"))
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
    target = gijiroku_targets.load_gijiroku_target(args.slug, expected_system="msearch")
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
    index_url = static_index_url(str(target["source_url"]))

    print(f"[INFO] Target: {target['name']} ({target['slug']}, {target['system_type']})")
    print(f"[INFO] Source URL: {target['source_url']}")
    print(f"[INFO] Static index: {index_url}")
    response = session.get(index_url, timeout=timeout_seconds)
    response.raise_for_status()
    meeting_items = parse_index(decode_response(response), response.url)
    # 目次 1 枚だけを読む形。取得に失敗すれば例外で落ちるので、ここまで
    # 来たら目次は歩けている。--max-meetings で切ったときだけ未完了。
    # 一覧の置き換えが拒まれるなら、今回の走査を取り切れたとは言えない。
    plan_shrank = gijiroku_storage.meetings_index_would_shrink(
        index_json, [asdict(item) for item in meeting_items]
    )
    gijiroku_storage.record_catalog_walk(
        work_dir,
        discovered=len(meeting_items),
        plan_shrank=plan_shrank,
        limit_reached=args.max_meetings > 0,
        extra={"pages_walked": 1},
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
    # 失敗した分は取れていない。試行数で進めると n/n になり、
    # 本文が欠けていても公開画面が「取得完了」と出る。
    failed_count = 0
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
                detail = session.get(item.url, timeout=timeout_seconds)
                detail.raise_for_status()
                raw_html = decode_response(detail)
                body = parse_body(raw_html)
                if not body:
                    raise RuntimeError("会議録本文を抽出できませんでした。")
                destination = gijiroku_storage.write_text(
                    plan["meeting_download_dir"] / (plan["stem"] + ".txt"),
                    build_minutes_text(item, body),
                    compress=True,
                )
                output_path = str(destination)
                status = "saved_text"
                if args.save_html:
                    page_dir = pages_dir / plan["year_dir_name"]
                    if plan["meeting_group_dir"]:
                        page_dir = page_dir / plan["meeting_group_dir"]
                    gijiroku_storage.write_text(
                        page_dir / (plan["stem"] + ".html"),
                        raw_html,
                        compress=True,
                    )
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
            # 失敗の種類は共有の定義で決める。error だけ数えると
            # timeout や not_found が「取れた」に混ざる。
            if status in gijiroku_storage.SCRAPE_FAILED_STATUSES:
                failed_count += 1
            handle.flush()
            emit_progress(
                len(plans) - len(work_items) + idx - failed_count,
                len(plans),
                state_path,
                state,
            )
            if args.delay_seconds > 0 and idx < len(work_items):
                time.sleep(args.delay_seconds)

    print(f"[DONE] Saved index: {index_json}")
    print(f"[DONE] Result log : {result_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
