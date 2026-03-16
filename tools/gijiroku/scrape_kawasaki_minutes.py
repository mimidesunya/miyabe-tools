#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import html
import json
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urljoin, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


BASE_URL = "https://www13.gijiroku.com/kawasaki_council/"
ROBOTS_TXT_URL = "https://www13.gijiroku.com/robots.txt"
DEFAULT_WAIT_MS = 10_000


@dataclass
class MeetingItem:
    title: str
    url: str
    year_label: str


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
    parser = argparse.ArgumentParser(
        description="川崎市議会会議録の年別一覧を巡回し、ダウンロード可能な記録を保存します。"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data") / "gijiroku" / "kawasaki_council",
        help="取得データの保存先ディレクトリ",
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
    return parser


def discover_meeting_items(page, timeout_ms: int) -> list[MeetingItem]:
    meetings: list[MeetingItem] = []
    year_pages: list[tuple[str, str]] = []

    for section_path in ["g08v_viewh.asp", "g08v_views.asp"]:
        section_url = urljoin(BASE_URL, section_path)
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

    year_pages = [(label, url) for (label, url) in year_pages]

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
                    meetings.append(MeetingItem(title=text, url=abs_href, year_label=year_label))
                    pending_url = ""
                else:
                    pending_url = abs_href
                continue

            if pending_url and href.startswith("javascript") and text:
                meetings.append(MeetingItem(title=text, url=pending_url, year_label=year_label))
                pending_url = ""

    uniq: dict[tuple[str, str], MeetingItem] = {}
    for item in meetings:
        key = (item.title, item.url)
        uniq[key] = item
    return list(uniq.values())


def try_download_from_detail(page, item: MeetingItem, output_dir: Path, timeout_ms: int) -> tuple[str, str]:
    page.goto(item.url, wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=3_000)
    except Exception:
        pass

    select_all_selectors = [
        'text=全ての発言を選択',
        'text=すべての発言を選択',
        'text=全選択',
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
        'text=テキスト',
        'text=Word',
        'text=ワード',
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
            suggested = download.suggested_filename
            ext = Path(suggested).suffix or ".dat"
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
                filename = sanitize_filename(item.title, "meeting") + ".txt"
                dest = output_dir / filename
                dest.write_text(text, encoding="utf-8")
                return "saved_text", str(dest)
    except Exception:
        pass

    html = page.content()
    direct_links = re.findall(r"(https?://[^\"'\s>]+(?:wiweb|voiweb)\.exe\?[^\"'\s>]+)", html)
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
                filename = sanitize_filename(item.title, "meeting") + ext
                dest = output_dir / filename
                dest.write_bytes(content)
                return "downloaded_direct", str(dest)
        except Exception:
            continue

    try:
        response = page.context.request.get(item.url, timeout=timeout_ms)
        if response.ok:
            filename = sanitize_filename(item.title, "meeting") + ".html"
            dest = output_dir / filename
            dest.write_bytes(response.body())
            return "saved_html", str(dest)
    except Exception:
        pass

    return "not_found", ""


def main() -> int:
    args = build_parser().parse_args()
    if not args.ack_robots:
        print("[ERROR] robots.txt / 利用規約確認のため --ack-robots を指定してください。")
        print(f"        robots.txt: {ROBOTS_TXT_URL}")
        return 2

    output_dir: Path = args.output_dir
    downloads_dir = output_dir / "downloads"
    pages_dir = output_dir / "pages"
    output_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)
    if args.save_html:
        pages_dir.mkdir(parents=True, exist_ok=True)

    index_json = output_dir / "meetings_index.json"
    result_csv = output_dir / f"run_result_{now_ts()}.csv"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headful)
        context = browser.new_context(accept_downloads=True, locale="ja-JP")
        page = context.new_page()
        page.set_default_timeout(args.timeout_ms)

        print("[INFO] 会議一覧を収集中...")
        meeting_items = discover_meeting_items(page, args.timeout_ms)
        print(f"[INFO] 会議候補 {len(meeting_items)} 件")

        index_json.write_text(
            json.dumps([asdict(item) for item in meeting_items], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if args.max_meetings > 0:
            meeting_items = meeting_items[: args.max_meetings]

        with result_csv.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["title", "year", "url", "status", "output", "error"],
            )
            writer.writeheader()

            for idx, item in enumerate(meeting_items, start=1):
                print(f"[{idx}/{len(meeting_items)}] {item.year_label} {item.title}")
                status = ""
                output_path = ""
                error_msg = ""
                year_dir_name = normalize_year_dir(item.year_label)
                meeting_download_dir = downloads_dir / year_dir_name
                meeting_download_dir.mkdir(parents=True, exist_ok=True)
                try:
                    status, output_path = try_download_from_detail(
                        page,
                        item,
                        meeting_download_dir,
                        args.timeout_ms,
                    )
                    if args.save_html and status == "not_found":
                        page_year_dir = pages_dir / year_dir_name
                        page_year_dir.mkdir(parents=True, exist_ok=True)
                        html_path = page_year_dir / (sanitize_filename(item.title, "meeting") + ".html")
                        html_path.write_text(page.content(), encoding="utf-8", errors="ignore")
                except PlaywrightTimeoutError as e:
                    status = "timeout"
                    error_msg = str(e)
                except Exception as e:
                    status = "error"
                    error_msg = str(e)

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
                f.flush()
                time.sleep(max(args.delay_seconds, 0))

        context.close()
        browser.close()

    print(f"[DONE] index: {index_json}")
    print(f"[DONE] result: {result_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
