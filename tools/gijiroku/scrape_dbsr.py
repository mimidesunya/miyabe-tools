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
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

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


@dataclass
class MeetingItem:
    title: str
    url: str
    year_label: str
    meeting_group: str | None = None
    list_url: str | None = None
    held_on: str | None = None
    doc_urls: list[str] | None = None
    doc_kind: str | None = None


@dataclass
class ListPage:
    title: str
    year_label: str
    url: str
    meeting_group: str
    auxiliary_docs: list[dict[str, str]]


@dataclass
class DayDocumentGroup:
    title: str
    year_label: str
    meeting_group: str
    list_url: str
    doc_urls: list[str]
    held_on: str


@dataclass
class DocumentRow:
    title: str
    url: str
    held_on: str


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
    text = re.sub(r"</(p|div|li|tr|table|h[1-6]|pre|section|article)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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


def cleaned_query_pairs(url: str) -> list[tuple[str, str]]:
    return [(key, value) for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True)]


def query_value(url: str, key: str) -> str:
    for current_key, current_value in cleaned_query_pairs(url):
        if current_key == key:
            return current_value
    return ""


def canonicalize_template_url(url: str) -> str:
    parts = urlsplit(url)
    query = cleaned_query_pairs(url)
    template = ""
    others: list[tuple[str, str]] = []
    for key, value in query:
        if key == "Template":
            template = value
        else:
            others.append((key, value))
    others.sort()
    normalized_query: list[tuple[str, str]] = []
    if template:
        normalized_query.append(("Template", template))
    normalized_query.extend(others)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(normalized_query), ""))


def request_text(request_context, url: str, timeout_ms: int, referer: str | None = None) -> str:
    headers = {"referer": referer} if referer else None
    response = request_context.get(url, timeout=timeout_ms, headers=headers)
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status}: {url}")
    body = response.body()
    for encoding in ("utf-8", "cp932", "shift_jis", "euc_jp"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="ignore")


def clean_page_title(title_text: str) -> str:
    if "|" in title_text:
        return normalize_space(title_text.split("|", 1)[0])
    return normalize_space(title_text)


def dbsr_index_root(source_url: str) -> str:
    parts = urlsplit(source_url)
    path = parts.path or "/"
    if path.endswith("/"):
        base_path = path
    else:
        base_path = path.rsplit("/", 1)[0] + "/"
    return urlunsplit((parts.scheme or "https", parts.netloc, base_path, "", ""))


def find_search_library_url(page, source_url: str) -> str:
    candidates: list[str] = []
    links = page.locator("a")
    for i in range(links.count()):
        href = safe_href(links.nth(i))
        if "Template=search-library" not in href:
            continue
        candidates.append(urljoin(page.url, href))
    if candidates:
        return candidates[0]

    base_url = dbsr_index_root(source_url)
    return urljoin(base_url, "100000?Template=search-library")


def held_on_from_text(value: str) -> str | None:
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

    match = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", value)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"

    return None


def japanese_date_label(year_label: str, held_on: str | None) -> str | None:
    if not held_on:
        return None
    year_match = re.search(r"(昭和|平成|令和)\s*([元\d０-９]+)年", year_label)
    date_match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", held_on)
    if not year_match or not date_match:
        return None
    return f"{year_match.group(1)}{year_match.group(2)}年{int(date_match.group(2))}月{int(date_match.group(3))}日"


def detect_meeting_group(text: str, title_text: str) -> str:
    cleaned = normalize_space(text)
    if cleaned:
        cleaned = re.sub(r"^(昭和|平成|令和)\s*[元\d０-９]+年\s*", "", cleaned).strip()
        if cleaned:
            return cleaned

    page_title = clean_page_title(title_text)
    page_title = re.sub(r"\s*目次$", "", page_title)
    page_title = re.sub(r"\s*会議録一覧$", "", page_title)
    page_title = re.sub(r"\s*検索結果一覧$", "", page_title)
    page_title = re.sub(r"^(昭和|平成|令和)\s*[元\d０-９]+年\s*", "", page_title).strip()
    return normalize_space(page_title) or "会議録"


def collect_list_page_entries(page, entries, year_label: str, items: dict[str, ListPage]) -> None:
    for entry_index in range(entries.count()):
        entry = entries.nth(entry_index)
        anchors = entry.locator("a")
        list_url = ""
        meeting_group = ""
        auxiliary_docs: list[dict[str, str]] = []

        for anchor_index in range(anchors.count()):
            anchor = anchors.nth(anchor_index)
            text = safe_inner_text(anchor)
            href = safe_href(anchor)
            if not href:
                continue
            absolute_url = canonicalize_template_url(urljoin(page.url, href))

            if "Template=list" in href and list_url == "":
                list_url = absolute_url
                meeting_group = detect_meeting_group(text, page.title())
                continue

            if "Template=mokuji" in href:
                auxiliary_docs.append(
                    {
                        "title": normalize_space(text) or "補助資料",
                        "url": absolute_url,
                    }
                )

        if not list_url:
            continue

        items.setdefault(
            list_url,
            ListPage(
                title=meeting_group,
                year_label=year_label,
                url=list_url,
                meeting_group=meeting_group,
                auxiliary_docs=auxiliary_docs,
            ),
        )


def discover_list_pages(page, target: dict, timeout_ms: int) -> list[ListPage]:
    page.goto(str(target["source_url"]), wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=3_000)
    except Exception:
        pass

    search_library_url = find_search_library_url(page, str(target["source_url"]))
    page.goto(search_library_url, wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=3_000)
    except Exception:
        pass

    items: dict[str, ListPage] = {}
    cells = page.locator("div.LibraryTable dl.cell")
    if cells.count() > 0:
        for cell_index in range(cells.count()):
            cell = cells.nth(cell_index)
            year_label = safe_inner_text(cell.locator("dt.cell__title").first) or "不明"
            collect_list_page_entries(page, cell.locator("dd.cell__item"), year_label, items)
        return list(items.values())

    cells = page.locator("div.LibraryTable dl")
    if cells.count() > 0:
        for cell_index in range(cells.count()):
            cell = cells.nth(cell_index)
            year_label = safe_inner_text(cell.locator("dt").first) or "不明"
            collect_list_page_entries(page, cell.locator("dd"), year_label, items)
        return list(items.values())

    cells = page.locator("ul.table.table--all > li.table__cell")
    for cell_index in range(cells.count()):
        cell = cells.nth(cell_index)
        year_label = safe_inner_text(cell.locator("dt.table__header:not(.visually-hidden)").first)
        if not year_label:
            year_label = safe_inner_text(cell.locator("dt.table__header").first) or "不明"
        collect_list_page_entries(page, cell.locator("dd.table__item"), year_label, items)

    return list(items.values())


def list_url_with_origin(list_url: str, origin_base: str) -> str:
    joined = urljoin(origin_base, list_url)
    return canonicalize_template_url(joined)


def infer_day_title_from_held_on(held_on: str) -> str:
    match = re.fullmatch(r"\d{4}-(\d{2})-(\d{2})", held_on)
    if match:
        return f"{match.group(1)}月{match.group(2)}日"
    return held_on


def document_suffix(title: str) -> str:
    normalized = normalize_space(title)
    for suffix in ("本文", "名簿", "署名", "一般質問"):
        if suffix in normalized:
            return suffix

    tail = re.sub(r"^.*?[）)]\s*", "", normalized)
    return tail or "会議録"


def is_disabled(locator) -> bool:
    try:
        return locator.is_disabled(timeout=1_000)
    except Exception:
        aria_disabled = (locator.get_attribute("aria-disabled") or "").strip().lower()
        disabled = locator.get_attribute("disabled")
        return aria_disabled == "true" or disabled is not None


def extract_document_rows_from_page(page) -> list[DocumentRow]:
    rows: list[DocumentRow] = []
    items = page.locator("ul.result-document li.result-document__item")
    for index in range(items.count()):
        item = items.nth(index)
        anchor = item.locator(".ans-title__name a").first
        title = safe_inner_text(anchor)
        href = safe_href(anchor)
        if not title or not href:
            continue

        date_text = safe_inner_text(item.locator(".ans-title__date").first)
        held_on = held_on_from_text(date_text or title)
        if not held_on:
            continue

        rows.append(
            DocumentRow(
                title=title,
                url=canonicalize_template_url(urljoin(page.url, href)),
                held_on=held_on,
            )
        )
    if rows:
        return rows

    items = page.locator("div.recordcol div.title")
    for index in range(items.count()):
        item = items.nth(index)
        anchor = item.locator("a").first
        title = safe_inner_text(anchor)
        href = safe_href(anchor)
        if not title or not href:
            continue

        date_text = safe_inner_text(item.locator("span.date").first)
        held_on = held_on_from_text(date_text or title)
        if not held_on:
            continue

        rows.append(
            DocumentRow(
                title=title,
                url=canonicalize_template_url(urljoin(page.url, href)),
                held_on=held_on,
            )
        )
    return rows


def collect_list_page_documents(page, list_url: str, timeout_ms: int) -> list[DocumentRow]:
    page.goto(list_url, wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=3_000)
    except Exception:
        pass

    collected: list[DocumentRow] = []
    seen_urls: set[str] = set()
    seen_page_signatures: set[tuple[str, int]] = set()

    while True:
        page_rows = extract_document_rows_from_page(page)
        signature = (page_rows[0].url if page_rows else page.url, len(page_rows))
        if signature in seen_page_signatures:
            break
        seen_page_signatures.add(signature)

        for row in page_rows:
            if row.url in seen_urls:
                continue
            seen_urls.add(row.url)
            collected.append(row)

        next_button = page.locator("nav.pagination button[aria-label='次のページ']").first
        if next_button.count() > 0 and not is_disabled(next_button):
            try:
                next_button.click(timeout=timeout_ms)
                page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=3_000)
                except Exception:
                    pass
                continue
            except Exception:
                break

        next_url = ""
        links = page.locator("div.pagination a")
        for index in range(links.count()):
            link = links.nth(index)
            text = safe_inner_text(link)
            href = safe_href(link)
            if href and "次" in text:
                next_url = canonicalize_template_url(urljoin(page.url, href))
                break
        if next_url == "":
            break

        try:
            page.goto(next_url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=3_000)
            except Exception:
                pass
        except Exception:
            break

    return collected


def build_day_groups(list_page: ListPage, list_url: str, rows: list[DocumentRow]) -> list[DayDocumentGroup]:
    grouped: dict[str, list[DocumentRow]] = {}
    ordered_dates: list[str] = []
    for row in rows:
        if row.held_on not in grouped:
            grouped[row.held_on] = []
            ordered_dates.append(row.held_on)
        grouped[row.held_on].append(row)

    groups: list[DayDocumentGroup] = []
    for held_on in ordered_dates:
        doc_rows = grouped[held_on]
        body_rows = [row for row in doc_rows if "本文" in normalize_space(row.title)]
        chosen_rows = body_rows or doc_rows
        suffix = document_suffix(chosen_rows[0].title if chosen_rows else doc_rows[0].title)
        title = f"{infer_day_title_from_held_on(held_on)}－{suffix}"
        groups.append(
            DayDocumentGroup(
                title=title,
                year_label=list_page.year_label,
                meeting_group=list_page.meeting_group,
                list_url=list_url,
                doc_urls=[row.url for row in chosen_rows],
                held_on=held_on,
            )
        )
    return groups


def title_from_heading_or_filename(page_html: str, fallback: str) -> str:
    match = re.search(r"<h1[^>]*>(.*?)</h1>", page_html, flags=re.I | re.S)
    if match:
        text = html_to_text(match.group(1))
        if text:
            return text
    return fallback


def discover_meeting_items(page, target: dict, timeout_ms: int, max_meetings: int = 0) -> list[MeetingItem]:
    base_url = str(target["base_url"])
    list_pages = discover_list_pages(page, target, timeout_ms)

    meetings: list[MeetingItem] = []
    seen_titles: set[tuple[str, str, str]] = set()

    for list_page in list_pages:
        try:
            list_url = list_url_with_origin(list_page.url, base_url)
            rows = collect_list_page_documents(page, list_url, timeout_ms)
        except Exception:
            continue

        for group in build_day_groups(list_page, list_url, rows):
            key = (group.year_label, group.meeting_group, group.title)
            if key in seen_titles:
                continue
            seen_titles.add(key)
            meetings.append(
                MeetingItem(
                    title=group.title,
                    url=group.doc_urls[0] if group.doc_urls else group.list_url,
                    year_label=group.year_label,
                    meeting_group=group.meeting_group,
                    list_url=group.list_url,
                    held_on=group.held_on,
                    doc_urls=group.doc_urls,
                    doc_kind="minutes",
                )
            )
            if max_meetings > 0 and len(meetings) >= max_meetings:
                return meetings

        for auxiliary_doc in list_page.auxiliary_docs:
            aux_title = normalize_space(auxiliary_doc.get("title", "")) or "補助資料"
            aux_url = str(auxiliary_doc.get("url", "")).strip()
            if not aux_url:
                continue
            title = f"{list_page.meeting_group}－{aux_title}"
            key = (list_page.year_label, list_page.meeting_group, title)
            if key in seen_titles:
                continue
            seen_titles.add(key)
            meetings.append(
                MeetingItem(
                    title=title,
                    url=aux_url,
                    year_label=list_page.year_label,
                    meeting_group=list_page.meeting_group,
                    list_url=list_url,
                    held_on=None,
                    doc_urls=[aux_url],
                    doc_kind="toc" if aux_title == "目次" else "aux",
                )
            )
            if max_meetings > 0 and len(meetings) >= max_meetings:
                return meetings

    return meetings


def extract_document_body(page_html: str) -> str:
    voice_paragraphs = re.findall(r'<p[^>]*class="[^"]*voice__text[^"]*"[^>]*>(.*?)</p>', page_html, flags=re.I | re.S)
    voice_sections = [html_to_text(fragment) for fragment in voice_paragraphs]
    voice_sections = [section for section in voice_sections if section]
    if voice_sections:
        return "\n\n".join(voice_sections).strip()

    preferred_patterns = [
        r"<pre[^>]*>(.*?)</pre>",
        r'<main[^>]*>(.*?)</main>',
        r'<div[^>]*class="[^"]*content[^"]*"[^>]*>(.*?)</div>',
    ]
    for pattern in preferred_patterns:
        match = re.search(pattern, page_html, flags=re.I | re.S)
        if not match:
            continue
        text = html_to_text(match.group(1))
        if text:
            return text

    return html_to_text(page_html)


def extract_document_heading(page_html: str) -> str:
    title = title_from_heading_or_filename(page_html, "")
    if title:
        return title
    text = html_to_text(page_html)
    lines = [normalize_space(line) for line in text.splitlines() if normalize_space(line)]
    return lines[0] if lines else ""


def document_date_label(page_html: str, meeting_item: MeetingItem) -> str | None:
    explicit = japanese_date_label(meeting_item.year_label, meeting_item.held_on)
    if explicit:
        return explicit

    heading = extract_document_heading(page_html)
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", heading)
    if match:
        return f"{match.group(1)}年{int(match.group(2))}月{int(match.group(3))}日"
    return None


def fetch_meeting_text(request_context, item: MeetingItem, timeout_ms: int) -> tuple[int, str]:
    if not item.doc_urls:
        return 0, ""

    sections: list[str] = []
    fragment_count = 0
    for doc_url in item.doc_urls:
        page_html = request_text(request_context, doc_url, timeout_ms, referer=item.list_url or item.url)
        body_text = extract_document_body(page_html)
        heading = extract_document_heading(page_html)
        section_lines: list[str] = []
        if heading and normalize_space(heading) != normalize_space(item.meeting_group or ""):
            section_lines.append(heading)
            section_lines.append("-" * min(max(len(normalize_space(heading)), 8), 40))
        if body_text:
            section_lines.append(body_text)
        section_text = "\n".join(section_lines).strip()
        if section_text:
            sections.append(section_text)
            fragment_count += 1

    if not sections:
        return 0, ""

    header_lines = [item.title]
    if item.meeting_group:
        header_lines.append(item.meeting_group)
    header_lines.append(item.year_label)

    sample_html = request_text(request_context, item.doc_urls[0], timeout_ms, referer=item.list_url or item.url)
    held_on_label = document_date_label(sample_html, item)
    if held_on_label:
        header_lines.append(f"開催日: {held_on_label}")
    header_lines.append(f"Source URL: {item.url}")
    if item.list_url and item.list_url != item.url:
        header_lines.append(f"List URL: {item.list_url}")
    header_lines.append("")
    header_lines.append("\n\n".join(sections).strip())
    return fragment_count, "\n".join(header_lines).strip() + "\n"


def save_debug_html(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    default_slug = gijiroku_targets.default_slug_for_system("dbsr")
    parser = argparse.ArgumentParser(
        description="dbsr / db-search / kaigiroku-indexphp 系の議会会議録一覧を巡回し、日程単位の本文テキストを保存します。"
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
        "--timeout-ms",
        type=int,
        default=DEFAULT_WAIT_MS,
        help="Playwright / HTTP 操作タイムアウト（ミリ秒）",
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
    target = gijiroku_targets.load_gijiroku_target(args.slug, expected_system="dbsr")

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
        context = browser.new_context(accept_downloads=False, locale="ja-JP", user_agent=DEFAULT_USER_AGENT)
        page = context.new_page()
        page.set_default_timeout(args.timeout_ms)

        print("[INFO] 会議一覧を収集中...")
        meeting_items = discover_meeting_items(page, target, args.timeout_ms, args.max_meetings)
        print(f"[INFO] 会議候補 {len(meeting_items)} 件")

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
                document_count = len(item.doc_urls or [])
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
                    index_result = minutes_index_builder.best_effort_upsert_source_file(
                        output_db,
                        downloads_dir,
                        existing_output,
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
                            "documents": len(item.doc_urls or []),
                            "fragments": 0,
                        }
                    )
                    handle.flush()
                    emit_progress(idx, len(meeting_items), state_path, state)
                    continue

                try:
                    fragment_count, meeting_text = fetch_meeting_text(context.request, item, args.timeout_ms)
                    if not meeting_text:
                        status = "not_found"
                    else:
                        dest = gijiroku_storage.write_text(dest_base, meeting_text, compress=True)
                        output_path = str(dest)
                        status = "saved_text"
                except PlaywrightTimeoutError as exc:
                    status = "timeout"
                    error_msg = str(exc)
                except Exception as exc:
                    status = "error"
                    error_msg = str(exc)
                    if args.save_html and item.doc_urls:
                        debug_path = pages_dir / year_dir_name
                        if meeting_group_dir:
                            debug_path = debug_path / meeting_group_dir
                        try:
                            sample_html = request_text(context.request, item.doc_urls[0], args.timeout_ms, referer=item.url)
                            gijiroku_storage.write_text(
                                debug_path / (sanitize_filename(item.title, "meeting") + ".html"),
                                sample_html,
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
                        "documents": document_count,
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
