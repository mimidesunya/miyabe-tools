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
from collections import deque
from dataclasses import asdict, dataclass
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

sys.path.append(str(Path(__file__).parent))
import gijiroku_storage
import gijiroku_targets


DEFAULT_WAIT_MS = 10_000
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)
YEAR_LABEL_RE = re.compile(r"(昭和|平成|令和)\s*([元\d０-９]+)年")
RESULT_LINK_RE = re.compile(
    r'<a\b[^>]+href=["\']([^"\']*ResultFrame\.exe\?[^"\']+)["\'][^>]*>(.*?)</a>',
    flags=re.I | re.S,
)
FRAME_SRC_RE = re.compile(r'<frame\b[^>]+src=["\']([^"\']+)["\']', flags=re.I)
SEE_HREF_RE = re.compile(
    r'href=["\']([^"\']*See\.exe\?[^"\']*Code=[^"\']+)["\']',
    flags=re.I,
)
TREE_DEPTH_RE = re.compile(r"treedepth\.value='([^']+)'")
TITLE_RE = re.compile(r"<title>(.*?)</title>", flags=re.I | re.S)


@dataclass
class MeetingItem:
    title: str
    url: str
    year_label: str
    meeting_group: str | None = None


@dataclass
class SeeContext:
    source_url: str
    see_url: str
    post_url: str
    code: str
    root_html: str


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


def normalize_space(value: str) -> str:
    return re.sub(r"[ \t\u3000]+", " ", value).strip()


def to_ascii_digits(value: str) -> str:
    return value.translate(str.maketrans("０１２３４５６７８９", "0123456789"))


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


def normalize_meeting_group_dir(meeting_group: str | None) -> str:
    if not meeting_group:
        return ""
    return sanitize_filename(meeting_group, "meeting")


def html_to_text(raw_html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", "", raw_html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|tr|table|h[1-6]|pre|font|title)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def decode_html(body: bytes) -> str:
    for encoding in ("cp932", "shift_jis", "utf-8", "euc_jp"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="ignore")


def build_http_client():
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    opener.addheaders = [
        ("User-Agent", DEFAULT_USER_AGENT),
        ("Accept-Language", "ja,en-US;q=0.9,en;q=0.8"),
    ]
    return opener


def request_text(opener, url: str, timeout_ms: int, *, data: dict[str, str] | None = None, referer: str | None = None) -> tuple[str, str]:
    payload = None
    headers: dict[str, str] = {}
    if referer:
        headers["Referer"] = referer
    if data is not None:
        payload = urlencode(data, encoding="cp932").encode("ascii")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = Request(url, data=payload, headers=headers)
    with opener.open(request, timeout=max(timeout_ms / 1000.0, 1.0)) as response:
        body = response.read()
        final_url = response.geturl()
    return decode_html(body), final_url


def extract_title(page_html: str) -> str:
    match = TITLE_RE.search(page_html)
    if not match:
        return ""
    return normalize_space(html_to_text(match.group(1)))


def extract_hidden_value(page_html: str, name: str) -> str:
    patterns = [
        rf'<input\b[^>]*name=["\']{re.escape(name)}["\'][^>]*value=["\']([^"\']*)["\']',
        rf'<input\b[^>]*value=["\']([^"\']*)["\'][^>]*name=["\']{re.escape(name)}["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, page_html, flags=re.I)
        if match:
            return html.unescape(match.group(1)).strip()
    return ""


def extract_viewtree_action(page_html: str) -> str:
    patterns = [
        r'<form\b[^>]*name=["\']viewtree["\'][^>]*action=["\']([^"\']+)["\']',
        r'<form\b[^>]*action=["\']([^"\']+)["\'][^>]*name=["\']viewtree["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, page_html, flags=re.I)
        if match:
            return html.unescape(match.group(1)).strip()
    return ""


def parse_code_from_url(url: str) -> str:
    return str(parse_qs(urlsplit(url).query).get("Code", [""])[0]).strip()


def normalize_year_label(value: str) -> str | None:
    match = YEAR_LABEL_RE.search(value)
    if not match:
        return None
    return f"{match.group(1)}{to_ascii_digits(match.group(2))}年"


def meeting_group_from_depth(depth: str | None) -> str | None:
    if not depth:
        return None
    trimmed = re.sub(r"^(昭和|平成|令和)\s*[元\d０-９]+年\s*", "", depth).strip()
    cleaned = normalize_space(trimmed)
    return cleaned or None


def unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        key = normalize_space(value)
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(value)
    return ordered


def parse_tree_depths(page_html: str) -> list[str]:
    return unique_preserve_order([html.unescape(value) for value in TREE_DEPTH_RE.findall(page_html)])


def compose_document_title(depth: str | None, anchor_body: str, href: str) -> str:
    doc_label = normalize_space(html_to_text(anchor_body))
    meeting_group = meeting_group_from_depth(depth) or ""
    if meeting_group and doc_label:
        if meeting_group in doc_label:
            return doc_label
        if doc_label.startswith(("(", "（")):
            return f"{meeting_group}{doc_label}"
        return normalize_space(f"{meeting_group} {doc_label}")
    if doc_label:
        return doc_label

    file_name = str(parse_qs(urlsplit(href).query).get("fileName", [""])[0]).strip()
    return file_name or "meeting"


def parse_result_links(page_html: str, base_url: str, current_depth: str | None) -> list[MeetingItem]:
    meeting_group = meeting_group_from_depth(current_depth)
    current_year = normalize_year_label(current_depth or "")
    items: list[MeetingItem] = []
    seen_urls: set[str] = set()

    for href, body in RESULT_LINK_RE.findall(page_html):
        absolute_url = urljoin(base_url, html.unescape(href))
        if absolute_url in seen_urls:
            continue
        seen_urls.add(absolute_url)

        anchor_text = normalize_space(html_to_text(body))
        year_label = current_year or normalize_year_label(anchor_text) or "不明"
        items.append(
            MeetingItem(
                title=compose_document_title(current_depth, body, absolute_url),
                url=absolute_url,
                year_label=year_label,
                meeting_group=meeting_group,
            )
        )

    return items


def resolve_see_context(opener, target: dict, timeout_ms: int) -> SeeContext:
    source_url = str(target["source_url"])
    source_html, resolved_source_url = request_text(opener, source_url, timeout_ms)

    if "See.exe" in urlsplit(resolved_source_url).path:
        see_url = resolved_source_url
    else:
        match = SEE_HREF_RE.search(source_html)
        if not match:
            raise RuntimeError("See.exe のリンクを見つけられませんでした。")
        see_url = urljoin(resolved_source_url, html.unescape(match.group(1)))

    code = parse_code_from_url(see_url) or parse_code_from_url(resolved_source_url) or extract_hidden_value(source_html, "Code")
    if not code:
        raise RuntimeError("kensakusystem の Code パラメータを取得できませんでした。")

    see_html, resolved_see_url = request_text(opener, see_url, timeout_ms, referer=resolved_source_url)
    post_action = extract_viewtree_action(see_html)
    if post_action:
        post_url = urljoin(resolved_see_url, post_action)
    else:
        parts = urlsplit(resolved_see_url)
        post_url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    return SeeContext(
        source_url=resolved_source_url,
        see_url=resolved_see_url,
        post_url=post_url,
        code=code,
        root_html=see_html,
    )


def fetch_tree_page(opener, context: SeeContext, depth: str, timeout_ms: int) -> str:
    page_html, _ = request_text(
        opener,
        context.post_url,
        timeout_ms,
        data={
            "Code": context.code,
            "treedepth": depth,
            "page": "",
            "fileName": "",
        },
        referer=context.see_url,
    )
    return page_html


def discover_meeting_items(opener, target: dict, timeout_ms: int, max_meetings: int = 0) -> list[MeetingItem]:
    context = resolve_see_context(opener, target, timeout_ms)
    pending: deque[tuple[str | None, str]] = deque([(None, context.root_html)])
    seen_depths: set[str] = set()
    seen_urls: set[str] = set()
    meetings: list[MeetingItem] = []

    while pending:
        current_depth, page_html = pending.popleft()

        for item in parse_result_links(page_html, context.see_url, current_depth):
            if item.url in seen_urls:
                continue
            seen_urls.add(item.url)
            meetings.append(item)
            if max_meetings > 0 and len(meetings) >= max_meetings:
                return meetings

        for depth in parse_tree_depths(page_html):
            depth_key = normalize_space(depth)
            if not depth_key or depth_key in seen_depths:
                continue
            seen_depths.add(depth_key)
            try:
                child_html = fetch_tree_page(opener, context, depth, timeout_ms)
            except Exception as exc:
                print(f"[WARN] tree fetch failed: {depth_key} ({exc})")
                continue
            pending.append((depth, child_html))

    return meetings


def first_frame_src(page_html: str, keyword: str) -> str:
    for src in FRAME_SRC_RE.findall(page_html):
        if keyword.lower() in src.lower():
            return html.unescape(src)
    return ""


def build_print_all_url(get_text_url: str) -> str:
    parts = urlsplit(get_text_url)
    segments = parts.query.split("/")
    if len(segments) < 5:
        raise RuntimeError(f"GetText3 URL の形式を解釈できませんでした: {get_text_url}")
    new_query = "/".join(
        [
            segments[0],
            segments[1],
            segments[2],
            segments[3],
            segments[4],
            "PRINT_ALL",
            "0",
            "0",
        ]
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, ""))


def extract_document_body(page_html: str) -> str:
    body_only = re.sub(r"<head[\s\S]*?</head>", "", page_html, flags=re.I)
    return html_to_text(body_only).strip()


def fetch_meeting_text(opener, item: MeetingItem, timeout_ms: int) -> tuple[int, str]:
    result_frame_html, result_frame_url = request_text(opener, item.url, timeout_ms, referer=item.url)
    text_frame_src = first_frame_src(result_frame_html, "r_TextFrame.exe")
    if not text_frame_src:
        raise RuntimeError(f"ResultFrame から本文フレームを取得できませんでした: {item.url}")

    text_frame_url = urljoin(result_frame_url, text_frame_src)
    text_frame_html, resolved_text_frame_url = request_text(opener, text_frame_url, timeout_ms, referer=result_frame_url)
    get_text_src = first_frame_src(text_frame_html, "GetText3.exe")
    if not get_text_src:
        raise RuntimeError(f"r_TextFrame から GetText3 を取得できませんでした: {text_frame_url}")

    get_text_url = urljoin(resolved_text_frame_url, get_text_src)
    full_text_url = build_print_all_url(get_text_url)
    full_html, _ = request_text(opener, full_text_url, timeout_ms, referer=resolved_text_frame_url)
    body_text = extract_document_body(full_html)
    if not body_text:
        raise RuntimeError(f"本文テキストを抽出できませんでした: {item.url}")

    header_lines = [item.title]
    if item.meeting_group:
        header_lines.append(item.meeting_group)
    header_lines.append(item.year_label)
    header_lines.append(f"Source URL: {item.url}")
    header_lines.append("")
    header_lines.append(body_text)
    return 1, "\n".join(header_lines).strip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    default_slug = gijiroku_targets.default_slug_for_system("kensakusystem")
    parser = argparse.ArgumentParser(
        description="kensakusystem 系の議会会議録一覧を巡回し、全文テキストを保存します。"
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
        help="互換のため受理のみ（kensakusystem では未使用）",
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
        help="HTTP 操作タイムアウト（ミリ秒）",
    )
    parser.add_argument(
        "--ack-robots",
        action="store_true",
        help="robots.txt・利用規約・許諾確認済みとして実行する",
    )
    parser.add_argument(
        "--save-html",
        action="store_true",
        help="取得失敗時や調査用に HTML を保存する",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="既存の保存結果を無視して最初から取り直す",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    target = gijiroku_targets.load_gijiroku_target(args.slug, expected_system="kensakusystem")

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

    opener = build_http_client()

    print("[INFO] 会議一覧を収集中...")
    meeting_items = discover_meeting_items(opener, target, args.timeout_ms, args.max_meetings)
    print(f"[INFO] 会議候補 {len(meeting_items)} 件")

    index_json.parent.mkdir(parents=True, exist_ok=True)
    index_json.write_text(
        json.dumps([asdict(item) for item in meeting_items], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    emit_progress(0, len(meeting_items), state_path, state)

    with result_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["title", "year", "url", "status", "output", "error", "documents", "fragments"],
        )
        writer.writeheader()

        for idx, item in enumerate(meeting_items, start=1):
            print(f"[{idx}/{len(meeting_items)}] {item.year_label} {item.title}")
            status = ""
            output_path = ""
            error_msg = ""
            fragment_count = 0
            year_dir_name = normalize_year_dir(item.year_label)
            meeting_group_dir = normalize_meeting_group_dir(item.meeting_group)
            meeting_download_dir = downloads_dir / year_dir_name
            if meeting_group_dir:
                meeting_download_dir = meeting_download_dir / meeting_group_dir
            meeting_download_dir.mkdir(parents=True, exist_ok=True)
            dest_base = meeting_download_dir / (sanitize_filename(item.title, "meeting") + ".txt")
            resume_key = gijiroku_storage.item_signature(asdict(item))
            existing_output = gijiroku_storage.existing_output(dest_base)

            if not args.no_resume and existing_output is not None:
                output_path = str(existing_output)
                status = "skipped_existing"
                state["items"][resume_key] = {
                    "title": item.title,
                    "year_label": item.year_label,
                    "url": item.url,
                    "status": "saved_text",
                    "output_rel_path": str(existing_output.relative_to(downloads_dir)),
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
                        "documents": 1,
                        "fragments": 0,
                    }
                )
                handle.flush()
                emit_progress(idx, len(meeting_items), state_path, state)
                continue

            try:
                fragment_count, meeting_text = fetch_meeting_text(opener, item, args.timeout_ms)
                dest = gijiroku_storage.write_text(dest_base, meeting_text, compress=True)
                output_path = str(dest)
                status = "saved_text"
            except Exception as exc:
                status = "error"
                error_msg = str(exc)
                if args.save_html:
                    debug_path = pages_dir / year_dir_name
                    if meeting_group_dir:
                        debug_path = debug_path / meeting_group_dir
                    try:
                        debug_html, _ = request_text(opener, item.url, args.timeout_ms, referer=item.url)
                        gijiroku_storage.write_text(
                            debug_path / (sanitize_filename(item.title, "meeting") + ".html"),
                            debug_html,
                            compress=True,
                        )
                    except Exception:
                        pass

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
                    "documents": 1,
                    "fragments": fragment_count,
                }
            )
            handle.flush()
            emit_progress(idx, len(meeting_items), state_path, state)
            if args.delay_seconds > 0 and idx < len(meeting_items):
                time.sleep(args.delay_seconds)

    print(f"[DONE] Saved index: {index_json}")
    print(f"[DONE] Result log : {result_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
