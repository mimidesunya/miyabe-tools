#!/usr/bin/env python3
"""自治体サイト上の PDF 会議録を汎用的に収集するスクレイパ（system_type=独自 向け）。

会議録の長い裾野は「自治体 CMS 上に置かれた PDF 会議録」で占められる。site-gikai-pdf
（/uploaded/attachment/ 等の特定 CMS 前提）と違い、ここでは入口 URL から同一ドメインを
浅く BFS クロールし、会議録らしいページを辿って .pdf を拾う汎用版。

ダウンロード・本文抽出(pypdf)・出力命名・resume・state は kami_city_pdf と
gijiroku_planning/gijiroku_storage の実装を再利用する。ここでの新規部分は
「汎用クロール＋任意 .pdf 収集」だけ。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

SCRAPER_DIR = Path(__file__).resolve().parent
MODULE_DIR = SCRAPER_DIR.parent
sys.path.append(str(MODULE_DIR))
sys.path.append(str(SCRAPER_DIR))
import gijiroku_planning  # noqa: E402
import gijiroku_storage  # noqa: E402
import gijiroku_targets  # noqa: E402
import kami_city_pdf as kp  # noqa: E402
from kami_city_pdf import (  # noqa: E402
    DEFAULT_USER_AGENT,
    PdfMeetingItem,
    attachment_id,
    clean_pdf_label,
    composed_minutes_text,
    emit_progress,
    extract_pdf_text,
    extract_year_info,
    looks_like_generic_minutes_page,
    normalize_space,
    now_ts,
    page_title,
    request_bytes,
    request_text,
)

ASSET_SUFFIXES = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip", ".doc", ".docx",
    ".xls", ".xlsx", ".ppt", ".pptx", ".css", ".js", ".ico", ".mp4", ".mp3",
)
# リンク文字列末尾の「[PDF｜297.3KB]」「（PDF：1.2MB）」等のファイル種別/サイズ注記。
PDF_ANNOTATION_RE = re.compile(
    r"\s*[\[\(（［]\s*PDF\s*[^\]\)）］]*[\]\)）］]\s*$",
    re.IGNORECASE,
)


def clean_label(text: str) -> str:
    label = clean_pdf_label(text)
    prev = None
    while label and label != prev:
        prev = label
        label = PDF_ANNOTATION_RE.sub("", label).strip()
    return label


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="汎用 自治体サイト PDF 会議録スクレイパ")
    parser.add_argument("--slug", required=True, help="対象自治体 slug")
    parser.add_argument("--ack-robots", action="store_true", help="robots.txt・利用規約・許諾確認済みとして実行する")
    parser.add_argument("--max-meetings", type=int, default=0, help="処理するPDF件数上限（0 は無制限）")
    parser.add_argument("--max-pages", type=int, default=200, help="クロールするページ数上限")
    parser.add_argument("--max-depth", type=int, default=3, help="入口からのリンク追跡の深さ上限")
    parser.add_argument("--delay-seconds", type=float, default=1.5, help="PDFアクセス間の待機秒数")
    parser.add_argument("--timeout-ms", type=int, default=10_000, help="HTTPタイムアウト（ミリ秒）")
    parser.add_argument("--save-html", action="store_true", help="互換用（未使用）")
    parser.add_argument("--headful", action="store_true", help="互換用（HTTPなので無視）")
    parser.add_argument("--no-resume", action="store_true", help="既存の保存結果を無視して取り直す")
    return parser


def _is_followable_html(start_netloc: str, url: str) -> bool:
    parts = urlsplit(url)
    if parts.netloc != start_netloc:
        return False
    return not parts.path.lower().endswith(ASSET_SUFFIXES)


def crawl_pdf_items(
    session: requests.Session,
    start_url: str,
    *,
    timeout_ms: int,
    max_pages: int,
    max_depth: int,
) -> list[PdfMeetingItem]:
    start_netloc = urlsplit(start_url).netloc
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(start_url, 0)])
    items: dict[str, PdfMeetingItem] = {}

    while queue and len(visited) < max_pages:
        url, depth = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        try:
            html = request_text(session, url, timeout_ms)
        except Exception:
            continue
        soup = BeautifulSoup(html, "html.parser")
        title = page_title(soup)
        page_year_label, page_source_year = extract_year_info(title)

        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href", "")).strip()
            if not href or href.lower().startswith(("javascript:", "mailto:", "tel:")):
                continue
            absolute = urljoin(url, href).split("#", 1)[0]
            text = anchor.get_text(" ", strip=True)
            if urlsplit(absolute).path.lower().endswith(".pdf"):
                label = clean_label(text) or title
                year_label, source_year = extract_year_info(label, title)
                if year_label == "不明":
                    year_label, source_year = page_year_label, page_source_year
                items.setdefault(
                    absolute,
                    PdfMeetingItem(
                        title=label,
                        url=absolute,
                        year_label=year_label,
                        source_year=source_year,
                        source_fino=attachment_id(absolute),
                        page_url=url,
                        page_title=title,
                        meeting_group=None,
                    ),
                )
            elif (
                depth < max_depth
                and absolute not in visited
                and _is_followable_html(start_netloc, absolute)
                and looks_like_generic_minutes_page(text, absolute)
            ):
                queue.append((absolute, depth + 1))

    return list(items.values())


def main() -> int:
    args = build_parser().parse_args()
    if not args.ack_robots:
        print("ERROR: --ack-robots を指定してください。robots.txt・利用規約・許諾確認後に実行してください。", file=sys.stderr)
        return 2

    target = gijiroku_targets.load_gijiroku_target(args.slug, expected_system="独自")
    slug = str(target["slug"])
    work_dir = Path(target["work_dir"])
    downloads_dir = Path(target["downloads_dir"])
    index_json = Path(target["index_json_path"])
    pdf_dir = work_dir / "pdfs"
    state_path = work_dir / "scrape_state.json"
    result_csv = work_dir / f"run_result_{now_ts()}.csv"
    for d in (work_dir, downloads_dir, pdf_dir):
        d.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_USER_AGENT, "Accept-Language": "ja,en-US;q=0.9,en;q=0.8"})

    print(f"[INFO] Target: {target['name']} ({slug}, {target['system_type']})")
    print(f"[INFO] Source URL: {target['source_url']}")
    print("[INFO] 会議録PDFを収集中（汎用クロール）...")
    meeting_items = crawl_pdf_items(
        session,
        str(target["source_url"]),
        timeout_ms=args.timeout_ms,
        max_pages=args.max_pages,
        max_depth=args.max_depth,
    )
    if args.max_meetings > 0:
        meeting_items = meeting_items[: args.max_meetings]
    print(f"[INFO] PDF候補 {len(meeting_items)} 件")

    index_json.parent.mkdir(parents=True, exist_ok=True)
    index_json.write_text(
        json.dumps([asdict(item) for item in meeting_items], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    state = gijiroku_storage.load_state(state_path)
    emit_progress(0, len(meeting_items), state_path, state)

    saved_count = 0
    status_counts: dict[str, int] = {}
    with result_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["title", "year", "url", "status", "output", "pdf", "error"])
        writer.writeheader()

        planned_items = []
        for plan in gijiroku_planning.build_base_plans(meeting_items, downloads_dir, use_group_dir=False):
            item = plan["item"]
            gijiroku_planning.attach_text_output(plan, key="text_base")
            pdf_name = f"{item.source_fino or plan['original_idx']}_{plan['stem']}.pdf"
            plan["pdf_path"] = pdf_dir / plan["year_dir"] / pdf_name
            planned_items.append(plan)

        previous_missing = gijiroku_planning.previous_missing_count(state)
        planned_items, work_items, missing_count = gijiroku_planning.select_work_items(
            planned_items, no_resume=args.no_resume, previous_missing_count=previous_missing
        )
        gijiroku_planning.save_plan_summary(state_path, state, planned_items, missing_count, previous_missing)
        saved_count = 0 if args.no_resume else sum(1 for plan in planned_items if plan.get("existing_output") is not None)
        emit_progress(saved_count, len(meeting_items), state_path, state)

        for idx, plan in enumerate(work_items, start=1):
            item = plan["item"]
            print(f"[{idx}/{len(work_items)}] {item.year_label} {item.title}")
            text_base = plan["text_base"]
            pdf_path = plan["pdf_path"]
            existing_output = plan["existing_output"]
            status = ""
            output_path = ""
            error_msg = ""
            if not args.no_resume and existing_output is not None:
                status = "skipped_existing"
                output_path = str(existing_output)
            else:
                try:
                    pdf_bytes = request_bytes(session, item.url, args.timeout_ms)
                    gijiroku_storage.write_bytes(pdf_path, pdf_bytes, compress=False)
                    extracted = extract_pdf_text(pdf_bytes)
                    if not extracted:
                        status = "empty_pdf_text"
                    else:
                        dest = gijiroku_storage.write_text(text_base, composed_minutes_text(item, extracted), compress=True)
                        output_path = str(dest)
                        status = "saved_text"
                except Exception as exc:
                    status = "error"
                    error_msg = str(exc)
            if status:
                status_counts[status] = status_counts.get(status, 0) + 1
            state["items"][plan["resume_key"]] = {
                "title": item.title,
                "year_label": item.year_label,
                "url": item.url,
                "status": status,
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
                    "pdf": str(pdf_path) if pdf_path.exists() else "",
                    "error": error_msg,
                }
            )
            handle.flush()
            if status == "saved_text":
                saved_count += 1
            emit_progress(saved_count, len(meeting_items), state_path, state)
            if args.delay_seconds > 0 and idx < len(work_items):
                time.sleep(args.delay_seconds)

    validation = gijiroku_storage.apply_classified_scrape_validation(
        state_path,
        state,
        discovered_count=len(meeting_items),
        downloaded_count=saved_count,
        status_counts=status_counts,
    )
    emit_progress(int(validation["progress_current"]), int(validation["progress_total"]), state_path, state)
    print(f"[DONE] Saved index: {index_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
