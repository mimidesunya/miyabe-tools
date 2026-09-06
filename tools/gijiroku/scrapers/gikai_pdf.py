#!/usr/bin/env python3
"""自治体サイト上の PDF 会議録を汎用的に収集するスクレイパ（system_type=独自 向け）。

会議録の長い裾野は「自治体 CMS 上に置かれた PDF 会議録」で占められる。site-gikai-pdf
（/uploaded/attachment/ 等の特定 CMS 前提）と違い、ここでは入口 URL から同一ドメインを
浅く BFS クロールし、会議録らしいページを辿って .pdf を拾う汎用版。

ダウンロード・本文抽出(pypdf)・出力命名・resume・state は kami_city_pdf と
gijiroku_planning/gijiroku_storage の実装を再利用する。ここでの新規部分は
「汎用クロール＋任意 .pdf 収集」だけ。

知見/落とし穴:
- index builder(choose_minutes_source_files)は downloads_dir の .txt/.html/.htm
  しか読まない。**PDF は必ずテキスト抽出して .txt(.gz) で保存する**こと
  （PDF をそのまま置いても検索インデックスに入らない）。composed_minutes_text +
  gijiroku_storage.write_text がこれを担う。
- 汎用クロールは議会サイト上の議案・資料・広報・表紙 PDF にも当たる。
  会議録らしいページを辿ったうえで、採用直前に題名と本文を見て会議録でない
  ものを落とす。既に索引された分は、同じ判定を classify_doc_type から呼ぶ。
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

# 取得・本文抽出・出力命名・state は kami_city_pdf の実装をそのまま再利用する
# （site-gikai-pdf でも共有しているロジック。重複実装を避ける）。
from kami_city_pdf import (  # noqa: E402
    DEFAULT_USER_AGENT,
    PdfMeetingItem,
    attachment_id,
    clean_pdf_label,
    emit_progress,
    extract_year_info,
    looks_like_attachment_pdf,
    looks_like_generic_minutes_page,
    YEAR_ONLY_ANCHOR_RE,
    now_ts,
    page_title,
    process_pdf_meeting_plan,
    request_text,
)

try:
    import minutes_kind
except ModuleNotFoundError:  # pragma: no cover
    from tools.gijiroku import minutes_kind

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


# 会議録を「令和8年」のように年だけのリンクで年度別に分ける取得元がある
# （河内町など）。リンク文字列にも URL にも会議録を示す語が無いので通常の
# 判定では 1 段目で行き止まりになる。入口 URL は台帳にその自治体の会議録の
# 入口として登録されたものなので、そこに並ぶ年リンクだけは会議録の年度別
# ページとみなして辿る。2 段目より深くは通常どおり判定する。
#
# 年の書き方は取得元でばらつくので、判定は kami_city_pdf と共有する。
# 元号だけを見ていたころは、西暦（岐南町）と元号の略記（東峰村）で
# 1 件も見つけられなかった。
YEAR_ONLY_LINK = YEAR_ONLY_ANCHOR_RE


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
    walk: dict | None = None,
) -> list[PdfMeetingItem]:
    """入口から辿って PDF を集める。

    `walk` を渡すと、開けなかったページ数と、ページ数の上限に当たったかを
    控える。どちらもその先の会議録が見えなくなるのに、黙って捨てていた。
    """
    start_netloc = urlsplit(start_url).netloc
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(start_url, 0)])
    items: dict[str, PdfMeetingItem] = {}
    missed: list[str] = []
    dropped_by_url: dict[str, str] = {}
    # 深さの上限で辿るのをやめたリンク。その先の会議録は見えていない。
    depth_capped = 0

    while queue and len(visited) < max_pages:
        url, depth = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        try:
            html = request_text(session, url, timeout_ms)
            # PDF などを掴んだ場合、パーサが AssertionError を投げて
            # プロセスごと落ちる。解析も同じ try で守る。
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            # 開けないページの先は、まるごと見えなくなる。数えておく。
            missed.append(url)
            continue
        title = page_title(soup)
        page_year_label, page_source_year = extract_year_info(title)

        # フレームで組まれた会議録ページ（桜川市など）は、入口ページに
        # リンクが 1 本も無く中身が frame の中にある。frame は本文の一部
        # なので、リンク文字列の判定を通さずそのまま辿る。
        for frame in soup.find_all(["frame", "iframe"]):
            src = str(frame.get("src", "")).strip()
            if not src:
                continue
            absolute = urljoin(url, src).split("#", 1)[0]
            if (
                depth < max_depth
                and absolute not in visited
                and _is_followable_html(start_netloc, absolute)
            ):
                # frame の中身は同じ 1 ページの続きなので先に処理する。
                queue.appendleft((absolute, depth + 1))

        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href", "")).strip()
            if not href or href.lower().startswith(("javascript:", "mailto:", "tel:")):
                continue
            absolute = urljoin(url, href).split("#", 1)[0]
            text = anchor.get_text(" ", strip=True)
            if (
                urlsplit(absolute).path.lower().endswith(".pdf")
                or looks_like_attachment_pdf(absolute, text)
            ):
                label = clean_label(text) or title
                skip_reason = minutes_kind.non_minutes_reason(label, "")
                if skip_reason:
                    dropped_by_url.setdefault(absolute, skip_reason)
                    continue
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
                depth >= max_depth
                and absolute not in visited
                and _is_followable_html(start_netloc, absolute)
                and looks_like_generic_minutes_page(text, absolute)
            ):
                # 辿る価値のあるリンクだが、深さの上限で捨てている。
                depth_capped += 1
            elif (
                depth < max_depth
                and absolute not in visited
                and _is_followable_html(start_netloc, absolute)
                and (
                    looks_like_generic_minutes_page(text, absolute)
                    or (depth == 0 and YEAR_ONLY_LINK.match(clean_label(text)))
                )
            ):
                queue.append((absolute, depth + 1))

    dropped_reasons: dict[str, int] = {}
    for reason in dropped_by_url.values():
        dropped_reasons[reason] = dropped_reasons.get(reason, 0) + 1
    if walk is not None:
        walk.update(
            {
                "missed_pages": len(missed),
                "missed_examples": missed[:10],
                # 上限に当たって止まったなら、まだ辿る先が残っている。
                # 深さ上限は毎回同じ深さで止まるので、これを未完了にすると
                # 永久に再投入し続ける。数えて見せるだけにする。
                "limit_reached": bool(queue) and len(visited) >= max_pages,
                "depth_capped_links": depth_capped,
                "visited_pages": len(visited),
                "dropped_non_minutes": len(dropped_by_url),
                "dropped_non_minutes_reasons": dropped_reasons,
            }
        )
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
    catalog_walk: dict = {}
    meeting_items = crawl_pdf_items(
        session,
        str(target["source_url"]),
        timeout_ms=args.timeout_ms,
        max_pages=args.max_pages,
        max_depth=args.max_depth,
        walk=catalog_walk,
    )
    if args.max_meetings > 0:
        meeting_items = meeting_items[: args.max_meetings]
    print(f"[INFO] PDF候補 {len(meeting_items)} 件")
    # 開けなかったページと、ページ数の上限に当たったことを残す。
    # 残さないと「発見数＝保存数」で完了に見え、キューは 30 日巡ってこない。
    # 一覧の置き換えが拒まれるなら、今回の走査を取り切れたとは言えない。
    crawl_dropped = int(catalog_walk.get("dropped_non_minutes") or 0)
    explained_drops = gijiroku_storage.explained_non_minutes_drops(
        dropped_count=crawl_dropped,
        missed_pages=int(catalog_walk.get("missed_pages") or 0),
        limit_reached=bool(catalog_walk.get("limit_reached")) or args.max_meetings > 0,
    )
    plan_shrank = gijiroku_storage.meetings_index_would_shrink(
        index_json,
        [asdict(item) for item in meeting_items],
        explained_drop_count=explained_drops,
    )
    gijiroku_storage.record_catalog_walk(
        work_dir,
        discovered=len(meeting_items),
        plan_shrank=plan_shrank,
        missed_pages=int(catalog_walk.get("missed_pages") or 0),
        missed_examples=catalog_walk.get("missed_examples") or [],
        limit_reached=bool(catalog_walk.get("limit_reached")) or args.max_meetings > 0,
        extra={
            "visited_pages": int(catalog_walk.get("visited_pages") or 0),
            "dropped_non_minutes": crawl_dropped,
            "dropped_non_minutes_reasons": catalog_walk.get("dropped_non_minutes_reasons") or {},
        },
    )

    index_json.parent.mkdir(parents=True, exist_ok=True)
    gijiroku_storage.save_meetings_index(
        index_json,
        [asdict(item) for item in meeting_items],
        explained_drop_count=explained_drops,
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
        saved_count = 0
        accepted_items: list = []
        body_drop_reasons: dict[str, int] = {}
        emit_progress(saved_count, len(meeting_items), state_path, state)
        work_ids = {id(plan) for plan in work_items}
        ordered_plans = list(work_items) + [plan for plan in planned_items if id(plan) not in work_ids]

        for idx, plan in enumerate(ordered_plans, start=1):
            item = plan["item"]
            print(f"[{idx}/{len(ordered_plans)}] {item.year_label} {item.title}")
            pdf_path = plan["pdf_path"]
            result = process_pdf_meeting_plan(
                session, plan, no_resume=args.no_resume, timeout_ms=args.timeout_ms
            )
            status = str(result.get("status") or "")
            output_path = str(result.get("output_path") or "")
            item = result.get("item") or item
            error_msg = str(result.get("error") or "")
            if status == "skipped_not_minutes":
                reason = str(result.get("reason") or "non_minutes_body")
                body_drop_reasons[reason] = body_drop_reasons.get(reason, 0) + 1
            elif status in {"saved_text", "skipped_existing"}:
                accepted_items.append(item)
                saved_count += 1
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
            emit_progress(saved_count, len(meeting_items), state_path, state)
            if result.get("downloaded") and args.delay_seconds > 0 and idx < len(ordered_plans):
                time.sleep(args.delay_seconds)

        if accepted_items:
            extra_explained = explained_drops + sum(body_drop_reasons.values()) if explained_drops else 0
            gijiroku_storage.save_meetings_index(
                index_json,
                [asdict(item) for item in accepted_items],
                explained_drop_count=extra_explained,
            )
        gijiroku_storage.merge_dropped_non_minutes(work_dir, body_drop_reasons)

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
