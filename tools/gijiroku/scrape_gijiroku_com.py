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
from typing import Iterable
from urllib.parse import parse_qs, unquote_to_bytes, urljoin, urlparse, urlsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

sys.path.append(str(Path(__file__).parent))
import gijiroku_storage
import gijiroku_targets


DEFAULT_WAIT_MS = 10_000


@dataclass
class MeetingItem:
    title: str
    url: str
    year_label: str
    meeting_group: str | None = None


def now_ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def unique_preserve_order(values: Iterable[str]) -> list[str]:
    seen = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


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


def decode_query_component(value: str) -> str:
    if not value:
        return ""

    try:
        raw = unquote_to_bytes(value)
    except Exception:
        return ""

    for encoding in ("cp932", "shift_jis", "utf-8"):
        try:
            return normalize_space(raw.decode(encoding))
        except Exception:
            continue

    return normalize_space(raw.decode("utf-8", errors="ignore"))


def raw_query_values(url: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for part in urlsplit(url).query.split("&"):
        if not part:
            continue
        key, _, value = part.partition("=")
        values.setdefault(key, []).append(value)
    return values


def trim_group_label(label: str, title: str) -> str:
    trimmed = normalize_space(label)
    trimmed = re.sub(r"^(昭和|平成|令和)\s*[元\d０-９]+年\s*", "", trimmed)
    title_pattern = re.escape(normalize_space(title))
    trimmed = re.sub(rf"[｜|－-]\s*{title_pattern}$", "", trimmed).strip()
    return normalize_space(trimmed)


def extract_meeting_group(item_title: str, url: str) -> str | None:
    query = raw_query_values(url)
    title_hint = decode_query_component((query.get("TITL") or [""])[0])
    title_subt = decode_query_component((query.get("TITL_SUBT") or [""])[0])

    candidates: list[str] = []
    if title_hint:
        candidates.append(title_hint)
    if title_subt:
        trimmed = trim_group_label(title_subt, item_title)
        if trimmed:
            candidates.append(trimmed)

    normalized_title = normalize_space(item_title)
    for candidate in candidates:
        if candidate and candidate != normalized_title:
            return candidate
    return None


def normalize_meeting_group_dir(meeting_group: str | None) -> str:
    if not meeting_group:
        return ""
    return sanitize_filename(meeting_group, "meeting")


def safe_inner_text(locator, timeout_ms: int = 1_500) -> str:
    try:
        return (locator.inner_text(timeout=timeout_ms) or "").strip()
    except Exception:
        return ""


def safe_href(locator) -> str:
    try:
        return locator.get_attribute("href") or ""
    except Exception:
        return ""


def html_to_text(raw_html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", "", raw_html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|tr|table|h[1-6])>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_response_text(request_context, url: str, timeout_ms: int) -> tuple[str, bytes]:
    response = request_context.get(url, timeout=timeout_ms)
    if not response.ok:
        return "", b""
    body = response.body()
    for encoding in ("cp932", "shift_jis", "utf-8"):
        try:
            return body.decode(encoding, errors="ignore"), body
        except Exception:
            continue
    return "", body


def resolve_act203_url(request_context, act100_url: str, timeout_ms: int) -> str:
    act100_html, _ = fetch_response_text(request_context, act100_url, timeout_ms)
    if not act100_html:
        return ""

    act200_paths = re.findall(r"winopen\('([^']*ACT=200[^']*)'\)", act100_html)
    if not act200_paths:
        act200_paths = re.findall(r"\"([^\"]*ACT=200[^\"]*)\"", act100_html)
    if not act200_paths:
        return ""

    target_fino = parse_qs(urlparse(act100_url).query).get("FINO", [""])[0]
    ranked_paths: list[tuple[int, str]] = []
    for path in act200_paths:
        score = 0
        if target_fino and f"FINO={target_fino}" in path:
            score += 10
        if "HUID=" in path:
            score += 1
        ranked_paths.append((score, path))
    ranked_paths.sort(key=lambda x: x[0], reverse=True)

    act200_url = urljoin(act100_url, ranked_paths[0][1])
    act200_html, _ = fetch_response_text(request_context, act200_url, timeout_ms)
    if not act200_html:
        return ""

    frame_srcs = re.findall(r"<FRAME[^>]+SRC=\"([^\"]+)\"", act200_html, flags=re.I)
    for src in frame_srcs:
        if "ACT=203" in src:
            return urljoin(act200_url, src)
    return ""


def build_parser() -> argparse.ArgumentParser:
    default_slug = gijiroku_targets.default_slug_for_system("gijiroku.com")
    parser = argparse.ArgumentParser(
        description="gijiroku.com 系の議会会議録一覧を巡回し、ダウンロード可能な記録を保存します。"
    )
    parser.add_argument(
        "--slug",
        default=default_slug,
        help="自治体slug。data/config.json と work/municipalities/assembly_minutes_system_urls.tsv から対象を解決します。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="取得データの保存先ディレクトリ（未指定時は config の gijiroku.data_dir）",
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
        "--timeout-ms",
        type=int,
        default=DEFAULT_WAIT_MS,
        help="Playwright操作タイムアウト（ミリ秒）",
    )
    parser.add_argument(
        "--ack-robots",
        action="store_true",
        help="robots.txt・利用規約・許諾確認済みとして実行する",
    )
    parser.add_argument(
        "--save-html",
        action="store_true",
        help="ダウンロード失敗時に会議詳細HTMLを保存する",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="既存の保存結果を無視して最初から取り直す",
    )
    return parser


def discover_meeting_items(page, base_url: str, timeout_ms: int) -> list[MeetingItem]:
    meetings: list[MeetingItem] = []
    year_pages: list[tuple[str, str]] = []

    for section_path in ["g08v_viewh.asp", "g08v_views.asp"]:
        section_url = urljoin(base_url, section_path)
        page.goto(section_url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=3_000)
        except Exception:
            pass

        links = page.locator("a")
        for i in range(links.count()):
            text = safe_inner_text(links.nth(i), 1_200)
            href = safe_href(links.nth(i))
            if not href:
                continue
            if "FYY=" not in href or "TYY=" not in href:
                continue
            if not re.search(r"(昭和|平成|令和|20\d{2}).{0,4}年", text):
                continue
            year_pages.append((text, urljoin(page.url, href)))

    for year_label, year_url in year_pages:
        try:
            page.goto(year_url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception:
            continue
        try:
            page.wait_for_load_state("networkidle", timeout=3_000)
        except Exception:
            pass

        frame = page.frame(name="BOTTOM")
        if frame is None:
            continue

        anchors = frame.locator("a")
        count = anchors.count()
        pending_url = ""

        for i in range(count):
            locator = anchors.nth(i)
            text = safe_inner_text(locator, 700)
            href = safe_href(locator)

            if "voiweb.exe?ACT=100" in href and "FINO=" in href:
                abs_href = urljoin(frame.url, href)
                if text:
                    meetings.append(
                        MeetingItem(
                            title=text,
                            url=abs_href,
                            year_label=year_label,
                            meeting_group=extract_meeting_group(text, abs_href),
                        )
                    )
                    pending_url = ""
                else:
                    pending_url = abs_href
                continue

            if pending_url and href.startswith("javascript") and text:
                meetings.append(
                    MeetingItem(
                        title=text,
                        url=pending_url,
                        year_label=year_label,
                        meeting_group=extract_meeting_group(text, pending_url),
                    )
                )
                pending_url = ""

    uniq: dict[tuple[str, str], MeetingItem] = {}
    for item in meetings:
        uniq[(item.title, item.url)] = item
    return list(uniq.values())


def try_download_from_detail(page, item: MeetingItem, output_dir: Path, timeout_ms: int) -> tuple[str, str]:
    page.goto(item.url, wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=3_000)
    except Exception:
        pass

    select_all_selectors = [
        "text=全ての発言を選択",
        "text=すべての発言を選択",
        "text=全選択",
        'input[value*="全ての発言"]',
    ]
    for selector in select_all_selectors:
        locator = page.locator(selector).first
        if locator.count() > 0:
            try:
                locator.click(timeout=1_500)
                page.wait_for_timeout(300)
                break
            except Exception:
                continue

    download_targets = [
        "text=テキスト",
        "text=Word",
        "text=ワード",
        'a[href*="ACT=201"]',
        'a[href*="ACT=202"]',
    ]

    for selector in download_targets:
        locator = page.locator(selector).first
        if locator.count() == 0:
            continue

        try:
            with page.expect_download(timeout=5_000) as dl_info:
                locator.click(timeout=2_000)
            download = dl_info.value
            ext = Path(download.suggested_filename).suffix or ".dat"
            filename = sanitize_filename(item.title, "meeting") + ext
            dest = output_dir / filename
            download.save_as(str(dest))
            return "downloaded", str(dest)
        except Exception:
            continue

    try:
        act203_url = resolve_act203_url(page.context.request, item.url, timeout_ms)
        if act203_url:
            full_html, _ = fetch_response_text(page.context.request, act203_url, timeout_ms)
            if full_html:
                text = html_to_text(full_html)
                dest = gijiroku_storage.write_text(
                    output_dir / (sanitize_filename(item.title, "meeting") + ".txt"),
                    text,
                    compress=True,
                )
                return "saved_text", str(dest)
    except Exception:
        pass

    html_content = page.content()
    direct_links = re.findall(r"(https?://[^\"'\s>]+(?:wiweb|voiweb)\.exe\?[^\"'\s>]+)", html_content)
    for link in unique_preserve_order(direct_links):
        try:
            response = page.context.request.get(link, timeout=timeout_ms)
            if response.ok:
                content = response.body()
                ext = ".txt"
                ctype = (response.headers.get("content-type") or "").lower()
                if "msword" in ctype:
                    ext = ".doc"
                elif "html" in ctype:
                    ext = ".html"
                dest = gijiroku_storage.write_bytes(
                    output_dir / (sanitize_filename(item.title, "meeting") + ext),
                    content,
                    compress=ext in {".html", ".htm", ".txt"},
                )
                return "downloaded_direct", str(dest)
        except Exception:
            continue

    try:
        response = page.context.request.get(item.url, timeout=timeout_ms)
        if response.ok:
            dest = gijiroku_storage.write_bytes(
                output_dir / (sanitize_filename(item.title, "meeting") + ".html"),
                response.body(),
                compress=True,
            )
            return "saved_html", str(dest)
    except Exception:
        pass

    return "not_found", ""


def main() -> int:
    args = build_parser().parse_args()
    target = gijiroku_targets.load_gijiroku_target(args.slug, expected_system="gijiroku.com")

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
    pages_dir = work_dir / "pages"
    result_csv = work_dir / f"run_result_{now_ts()}.csv"
    state_path = work_dir / "scrape_state.json"
    state = gijiroku_storage.load_state(state_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)
    if args.save_html:
        pages_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] Target: {target['name']} ({target['slug']}, {target['system_type']})")
    print(f"[INFO] Source URL: {target['source_url']}")
    print(f"[INFO] Base URL: {target['base_url']}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headful)
        context = browser.new_context(accept_downloads=True, locale="ja-JP")
        page = context.new_page()
        page.set_default_timeout(args.timeout_ms)

        print("[INFO] 会議一覧を収集中...")
        meeting_items = discover_meeting_items(page, target["base_url"], args.timeout_ms)
        print(f"[INFO] 会議候補 {len(meeting_items)} 件")

        index_json.parent.mkdir(parents=True, exist_ok=True)
        index_json.write_text(
            json.dumps([asdict(item) for item in meeting_items], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if args.max_meetings > 0:
            meeting_items = meeting_items[: args.max_meetings]

        with result_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["title", "year", "url", "status", "output", "error"],
            )
            writer.writeheader()

            for idx, item in enumerate(meeting_items, start=1):
                print(f"[{idx}/{len(meeting_items)}] {item.year_label} {item.title}")
                status = ""
                output_path = ""
                error_msg = ""
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
                    writer.writerow(
                        {
                            "title": item.title,
                            "year": item.year_label,
                            "url": item.url,
                            "status": status,
                            "output": output_path,
                            "error": "",
                        }
                    )
                    handle.flush()
                    continue
                try:
                    status, output_path = try_download_from_detail(
                        page,
                        item,
                        meeting_download_dir,
                        args.timeout_ms,
                    )
                    if args.save_html and status == "not_found":
                        page_year_dir = pages_dir / year_dir_name
                        if meeting_group_dir:
                            page_year_dir = page_year_dir / meeting_group_dir
                        page_year_dir.mkdir(parents=True, exist_ok=True)
                        gijiroku_storage.write_text(
                            page_year_dir / (sanitize_filename(item.title, "meeting") + ".html"),
                            page.content(),
                            compress=True,
                        )
                except PlaywrightTimeoutError as exc:
                    status = "timeout"
                    error_msg = str(exc)
                except Exception as exc:
                    status = "error"
                    error_msg = str(exc)

                state["items"][resume_key] = {
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
                        "error": error_msg,
                    }
                )
                handle.flush()
                time.sleep(max(args.delay_seconds, 0))

        context.close()
        browser.close()

    print(f"[DONE] index: {index_json}")
    print(f"[DONE] result: {result_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
