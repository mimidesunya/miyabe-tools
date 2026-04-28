#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

sys.path.append(str(Path(__file__).parent))
import gijiroku_storage
import gijiroku_targets


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)
YEAR_LABEL_RE = re.compile(r"(昭和|平成|令和)\s*([元\d０-９]+)年")
WESTERN_REIWA_LABEL_RE = re.compile(r"(20\d{2})（令和([元\d０-９]+)）年")
PDF_SIZE_SUFFIX_RE = re.compile(r"\s*[［\[]PDFファイル／[^］\]]+[］\]]\s*$")
KAMI_MINUTES_PAGE_RE = re.compile(r"^/site/gikai/kaigiroku(?:\d{4}|sokuhou)\.html$")
ATTACHMENT_ID_RE = re.compile(r"/uploaded/attachment/(\d+)\.pdf$", re.I)
ERA_BASE_YEAR = {"昭和": 1925, "平成": 1988, "令和": 2018}
FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
MINUTES_PAGE_KEYWORDS = (
    "会議録",
    "議事録",
    "kaigiroku",
    "gijiroku",
    "minutes",
    "定例会",
    "臨時会",
)
GIKAI_PATH_KEYWORDS = ("gikai", "gicho", "gichou", "gityou")
YEAR_OR_LIST_RE = re.compile(r"(20\d{2}|令和[元\d０-９]+|平成[元\d０-９]+|昭和[元\d０-９]+|list\d+|\d{4,6}\.html)", re.I)


@dataclass(frozen=True)
class PdfMeetingItem:
    title: str
    url: str
    year_label: str
    source_year: int | None
    source_fino: int | None
    page_url: str
    page_title: str
    meeting_group: str | None = None


def now_ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def normalize_space(value: str) -> str:
    value = html.unescape(str(value)).replace("\u200b", "")
    return re.sub(r"[ \t\r\n\u3000]+", " ", value).strip()


def to_ascii_digits(value: str) -> str:
    return value.translate(FULLWIDTH_DIGITS)


def japanese_year_to_int(value: str) -> int | None:
    raw = to_ascii_digits(value.strip())
    if raw == "元":
        return 1
    if raw.isdigit():
        return int(raw)
    return None


def era_to_gregorian(era: str, year_text: str) -> int | None:
    era_year = japanese_year_to_int(year_text)
    base_year = ERA_BASE_YEAR.get(era)
    if era_year is None or base_year is None:
        return None
    return base_year + era_year


def extract_year_info(*values: str) -> tuple[str, int | None]:
    for value in values:
        text = normalize_space(value)
        western_match = WESTERN_REIWA_LABEL_RE.search(text)
        if western_match:
            year = int(western_match.group(1))
            reiwa = to_ascii_digits(western_match.group(2)).replace("元", "1")
            return f"令和{reiwa}年", year

        match = YEAR_LABEL_RE.search(text)
        if match:
            era = match.group(1)
            era_year = to_ascii_digits(match.group(2)).replace("元", "1")
            return f"{era}{era_year}年", era_to_gregorian(era, match.group(2))

    return "不明", None


def sanitize_filename(text: str, fallback: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\t\r\n]+", "_", normalize_space(text)).strip(" .")
    return (cleaned or fallback)[:180]


def clean_pdf_label(value: str) -> str:
    cleaned = PDF_SIZE_SUFFIX_RE.sub("", normalize_space(value))
    cleaned = re.sub(r"\s*PDFファイル\s*$", "", cleaned).strip()
    return cleaned or "会議録"


def attachment_id(url: str) -> int | None:
    match = ATTACHMENT_ID_RE.search(urlsplit(url).path)
    if not match:
        return None
    return int(match.group(1))


def emit_progress(current: int, total: int, state_path: Path | None = None, state: dict | None = None) -> None:
    print(f"[PROGRESS] unit=meeting current={max(0, current)} total={max(0, total)}", flush=True)
    if state_path is None:
        return
    if state is not None:
        state["progress_current"] = max(0, int(current))
        state["progress_total"] = max(0, int(total))
        state["progress_unit"] = "meeting"
        gijiroku_storage.save_state(state_path, state)
    else:
        gijiroku_storage.update_progress_state(state_path, current=current, total=total, unit="meeting")


def load_minutes_index_builder():
    try:
        import build_minutes_index as minutes_index_builder
    except ImportError as exc:
        print(
            "[WARN] build_minutes_index.py の依存を読み込めないため、minutes.sqlite の逐次更新はスキップします: "
            f"{exc}",
            flush=True,
        )
        return None
    return minutes_index_builder


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="自治体公式サイトの site/gikai 型会議録PDF一覧を巡回し、PDF本文をテキスト保存します。"
    )
    parser.add_argument("--slug", default="39212-kami-shi", help="対象自治体 slug")
    parser.add_argument("--ack-robots", action="store_true", help="robots.txt・利用規約・許諾確認済みとして実行する")
    parser.add_argument("--max-meetings", type=int, default=0, help="処理するPDF件数上限（0 は無制限）")
    parser.add_argument("--max-pages", type=int, default=120, help="一覧・詳細ページの探索上限（0 は無制限）")
    parser.add_argument("--delay-seconds", type=float, default=1.5, help="PDFアクセス間の待機秒数")
    parser.add_argument("--timeout-ms", type=int, default=10_000, help="HTTPタイムアウト（ミリ秒）")
    parser.add_argument("--save-html", action="store_true", help="取得した一覧ページHTMLを work 側へ保存する")
    parser.add_argument("--headful", action="store_true", help="互換オプション。HTTPスクレイパーなので無視します")
    parser.add_argument("--no-resume", action="store_true", help="既存の保存結果を無視して取り直す")
    return parser


def request_text(session: requests.Session, url: str, timeout_ms: int) -> str:
    response = session.get(url, timeout=max(timeout_ms / 1000.0, 1.0))
    response.raise_for_status()
    raw = response.content
    for encoding in ("utf-8", response.apparent_encoding, response.encoding, "cp932"):
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def request_bytes(session: requests.Session, url: str, timeout_ms: int) -> bytes:
    response = session.get(url, timeout=max(timeout_ms / 1000.0, 1.0))
    response.raise_for_status()
    return response.content


def page_title(soup: BeautifulSoup) -> str:
    heading = soup.select_one("#main_header h1") or soup.find("h1")
    if heading is not None:
        return normalize_space(heading.get_text(" ", strip=True))
    title = soup.find("title")
    return normalize_space(title.get_text(" ", strip=True)) if title is not None else ""


def is_kami_minutes_page(url: str) -> bool:
    parts = urlsplit(url)
    return parts.netloc == "www.city.kami.lg.jp" and KAMI_MINUTES_PAGE_RE.match(parts.path) is not None


def is_same_site_html_page(start_url: str, url: str) -> bool:
    start = urlsplit(start_url)
    target = urlsplit(url)
    if target.netloc != start.netloc:
        return False
    path = target.path.lower()
    if path.endswith(".pdf") or path.endswith((".jpg", ".jpeg", ".png", ".gif", ".zip", ".doc", ".docx", ".xls", ".xlsx")):
        return False
    return "/site/" in path


def looks_like_generic_minutes_page(anchor_text: str, url: str) -> bool:
    parts = urlsplit(url)
    path = parts.path.lower()
    haystack = normalize_space(f"{anchor_text} {parts.path}").lower()
    if any(keyword.lower() in haystack for keyword in MINUTES_PAGE_KEYWORDS):
        return True
    if any(keyword in path for keyword in GIKAI_PATH_KEYWORDS) and YEAR_OR_LIST_RE.search(haystack):
        return True
    return False


def should_follow_minutes_page(start_url: str, href: str, anchor_text: str, strict_kami: bool) -> str | None:
    absolute = urljoin(start_url, href.strip())
    absolute = absolute.split("#", 1)[0]
    if strict_kami:
        return absolute if is_kami_minutes_page(absolute) else None
    if not is_same_site_html_page(start_url, absolute):
        return None
    return absolute if looks_like_generic_minutes_page(anchor_text, absolute) else None


def is_site_attachment_pdf(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return path.endswith(".pdf") and "/uploaded/attachment/" in path


def load_supported_target(slug: str) -> dict:
    last_error: Exception | None = None
    for expected_system in ("site-gikai-pdf", "kami-city-pdf"):
        try:
            return gijiroku_targets.load_gijiroku_target(slug, expected_system=expected_system)
        except ValueError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ValueError(f"Municipality slug not found: {slug}")


def discover_minutes_pages(
    session: requests.Session,
    start_url: str,
    timeout_ms: int,
    pages_dir: Path | None = None,
    *,
    strict_kami: bool = False,
    max_pages: int = 120,
) -> list[str]:
    start_html = request_text(session, start_url, timeout_ms)
    if pages_dir is not None:
        gijiroku_storage.write_text(pages_dir / "start.html", start_html, compress=True)

    soup = BeautifulSoup(start_html, "html.parser")
    pages: dict[str, None] = {start_url: None}
    selectors = "#subsite_menu_wrap a[href], #site_navi a[href], #main_body a[href], a[href]"
    for anchor in soup.select(selectors):
        href = str(anchor.get("href", "")).strip()
        if not href:
            continue
        page_url = should_follow_minutes_page(start_url, href, anchor.get_text(" ", strip=True), strict_kami)
        if page_url:
            pages[page_url] = None
            if max_pages > 0 and len(pages) >= max_pages:
                break
    return list(pages.keys())


def discover_pdf_items(
    session: requests.Session,
    page_urls: list[str],
    timeout_ms: int,
    pages_dir: Path | None = None,
    *,
    require_site_attachment: bool = False,
) -> list[PdfMeetingItem]:
    items_by_url: dict[str, PdfMeetingItem] = {}

    for page_url in page_urls:
        page_html = request_text(session, page_url, timeout_ms)
        soup = BeautifulSoup(page_html, "html.parser")
        title = page_title(soup)
        page_year_label, page_source_year = extract_year_info(title)
        if pages_dir is not None:
            filename = sanitize_filename(Path(urlsplit(page_url).path).stem, "page") + ".html"
            gijiroku_storage.write_text(pages_dir / filename, page_html, compress=True)

        content = soup.select_one("#main_body .detail_free") or soup.select_one("#main_body") or soup
        current_group: str | None = None
        for node in content.descendants:
            node_name = getattr(node, "name", None)
            if node_name in {"p", "caption", "h2", "h3", "h4"}:
                if not node.find("a", href=lambda href: bool(href) and str(href).lower().endswith(".pdf")):
                    text = clean_pdf_label(node.get_text(" ", strip=True))
                    if text and "Adobe Reader" not in text and "PDF形式" not in text:
                        current_group = text
                continue

            if node_name != "a" or not node.has_attr("href"):
                continue
            pdf_url = urljoin(page_url, str(node.get("href", "")).strip())
            if not urlsplit(pdf_url).path.lower().endswith(".pdf"):
                continue
            if require_site_attachment and not is_site_attachment_pdf(pdf_url):
                continue
            label = clean_pdf_label(node.get_text(" ", strip=True))
            if label == "会議録" and current_group:
                label = current_group
            if label == "会議録":
                fallback_id = attachment_id(pdf_url)
                label = f"{title} {fallback_id}" if fallback_id is not None else title
            year_label, source_year = extract_year_info(label, current_group or "", title)
            if year_label == "不明":
                year_label = page_year_label
                source_year = page_source_year
            item = PdfMeetingItem(
                title=label,
                url=pdf_url,
                year_label=year_label,
                source_year=source_year,
                source_fino=attachment_id(pdf_url),
                page_url=page_url,
                page_title=title,
                meeting_group=current_group,
            )
            items_by_url.setdefault(pdf_url, item)

    return list(items_by_url.values())


def extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF本文抽出には pypdf が必要です。tools/gijiroku/requirements.txt をインストールしてください。") from exc

    reader = PdfReader(BytesIO(pdf_bytes))
    parts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if text:
            parts.append(text)
    return normalize_pdf_text("\n\n".join(parts))


def normalize_pdf_text(value: str) -> str:
    text = value.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    compact_chars = r"一-龯々〆ヵヶぁ-んァ-ヴー０-９0-9"
    text = re.sub(rf"(?<=[{compact_chars}])[\t ]+(?=[{compact_chars}])", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def composed_minutes_text(item: PdfMeetingItem, pdf_text: str) -> str:
    header = [item.year_label, item.title]
    if item.meeting_group and normalize_space(item.meeting_group) != normalize_space(item.title):
        header.append(item.meeting_group)
    header.append(f"出典: {item.url}")
    return "\n".join(header) + "\n\n" + pdf_text.strip() + "\n"


def normalize_year_dir(year_label: str) -> str:
    return sanitize_filename(year_label or "unknown", "unknown")


def main() -> int:
    args = build_parser().parse_args()
    if not args.ack_robots:
        print("ERROR: --ack-robots を指定してください。robots.txt・利用規約・許諾確認後に実行してください。", file=sys.stderr)
        return 2

    target = load_supported_target(args.slug)
    slug = str(target["slug"])
    work_dir = Path(target["work_dir"])
    downloads_dir = Path(target["downloads_dir"])
    index_json = Path(target["index_json_path"])
    output_db = Path(target["db_path"])
    pages_dir = work_dir / "pages" if args.save_html else None
    pdf_dir = work_dir / "pdfs"
    state_path = work_dir / "scrape_state.json"
    result_csv = work_dir / f"run_result_{now_ts()}.csv"

    work_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    if pages_dir is not None:
        pages_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        }
    )

    print(f"[INFO] Target: {target['name']} ({slug}, {target['system_type']})")
    print(f"[INFO] Source URL: {target['source_url']}")
    print("[INFO] 会議録ページを収集中...")
    strict_kami = str(target["system_type"]) == "kami-city-pdf"
    page_urls = discover_minutes_pages(
        session,
        str(target["source_url"]),
        args.timeout_ms,
        pages_dir,
        strict_kami=strict_kami,
        max_pages=args.max_pages,
    )
    print(f"[INFO] 会議録ページ {len(page_urls)} 件")
    meeting_items = discover_pdf_items(
        session,
        page_urls,
        args.timeout_ms,
        pages_dir,
        require_site_attachment=not strict_kami,
    )
    if args.max_meetings > 0:
        meeting_items = meeting_items[: args.max_meetings]
    print(f"[INFO] PDF候補 {len(meeting_items)} 件")

    index_json.parent.mkdir(parents=True, exist_ok=True)
    index_json.write_text(json.dumps([asdict(item) for item in meeting_items], ensure_ascii=False, indent=2), encoding="utf-8")
    minutes_index_builder = load_minutes_index_builder()
    minutes_meta_map = minutes_index_builder.parse_source_meta(index_json) if minutes_index_builder is not None else {}
    indexing_enabled = False
    if minutes_index_builder is not None:
        indexing_enabled = minutes_index_builder.prepare_incremental_index(
            output_db,
            logger=lambda message: print(message, flush=True),
            context=slug,
        )

    state = gijiroku_storage.load_state(state_path)
    emit_progress(0, len(meeting_items), state_path, state)

    with result_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["title", "year", "url", "status", "output", "pdf", "error", "documents", "fragments"],
        )
        writer.writeheader()

        for idx, item in enumerate(meeting_items, start=1):
            print(f"[{idx}/{len(meeting_items)}] {item.year_label} {item.title}")
            year_dir = normalize_year_dir(item.year_label)
            text_base = downloads_dir / year_dir / (sanitize_filename(item.title, "meeting") + ".txt")
            pdf_name = f"{item.source_fino or idx}_{sanitize_filename(item.title, 'meeting')}.pdf"
            pdf_path = pdf_dir / year_dir / pdf_name
            resume_key = gijiroku_storage.item_signature(asdict(item))
            existing_output = gijiroku_storage.existing_output(text_base)
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

            state["items"][resume_key] = {
                "title": item.title,
                "year_label": item.year_label,
                "url": item.url,
                "status": status,
                "output_rel_path": str(Path(output_path).relative_to(downloads_dir)) if output_path else "",
                "pdf_rel_path": str(pdf_path.relative_to(work_dir)) if pdf_path.exists() else "",
                "updated_at": now_ts(),
            }
            gijiroku_storage.save_state(state_path, state)

            if indexing_enabled and output_path:
                index_result = minutes_index_builder.best_effort_upsert_source_file(
                    output_db,
                    downloads_dir,
                    Path(output_path),
                    meta_map=minutes_meta_map,
                    logger=lambda message: print(message, flush=True),
                    context=f"{slug} {item.title}",
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
                    "pdf": str(pdf_path) if pdf_path.exists() else "",
                    "error": error_msg,
                    "documents": 1,
                    "fragments": 0,
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
            context=slug,
        )
    print(f"[DONE] Saved index: {index_json}")
    print(f"[DONE] Result log : {result_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
