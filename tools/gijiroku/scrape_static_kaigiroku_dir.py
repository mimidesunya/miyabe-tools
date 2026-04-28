#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

sys.path.append(str(Path(__file__).parent))
import gijiroku_storage
import gijiroku_targets
from scrape_kami_city_pdf import (
    DEFAULT_USER_AGENT,
    attachment_id,
    clean_pdf_label,
    emit_progress,
    extract_pdf_text,
    extract_year_info,
    load_minutes_index_builder,
    normalize_pdf_text,
    normalize_space,
    normalize_year_dir,
    now_ts,
    page_title,
    request_bytes,
    request_text,
    sanitize_filename,
)


DOCUMENT_EXTENSIONS = {".html", ".htm", ".php", ".asp", ".aspx", ""}
SKIP_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".css",
    ".js",
    ".zip",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
}
MINUTES_DIR_RE = re.compile(r"(?:^|[_\-/])(kai?giroku|gijiroku|gikaikaigiroku)(?:$|[_\-/])", re.I)
YEAR_HINT_RE = re.compile(r"(令和|平成|昭和|20\d{2}|19\d{2}|r\d{1,2}|h\d{1,2})", re.I)
SIZE_SUFFIX_RE = re.compile(r"\s*[（(][\d,.]+\s*(?:kb|mb|kbyte|mbyte|バイト)[）)]\s*$", re.I)
SKIP_DOCUMENT_LABEL_RE = re.compile(r"^(.*目次|表紙|扉|奥付|索引)$")
HTML_MINUTES_MARKERS = (
    "出席議員",
    "欠席議員",
    "議事日程",
    "会議録署名議員",
    "会議に付した事件",
    "開議",
    "閉議",
    "散会",
    "質疑",
    "討論",
)
GENERIC_LABELS = {"", "pdf", "pdfファイル", "html", "詳細", "本文", "会議録", "議事録"}


@dataclass(frozen=True)
class StaticMinutesItem:
    title: str
    url: str
    doc_type: str
    year_label: str
    source_year: int | None
    source_fino: int | None
    page_url: str
    page_title: str
    meeting_group: str | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="静的な kaigiroku/gijiroku ディレクトリ配下を巡回し、会議録PDF/HTML本文を保存します。"
    )
    parser.add_argument("--slug", required=True, help="対象自治体 slug")
    parser.add_argument("--ack-robots", action="store_true", help="robots.txt・利用規約・許諾確認済みとして実行する")
    parser.add_argument("--max-meetings", type=int, default=0, help="処理する文書件数上限（0 は無制限）")
    parser.add_argument("--max-pages", type=int, default=250, help="巡回するHTMLページ上限（0 は無制限）")
    parser.add_argument("--delay-seconds", type=float, default=1.5, help="文書アクセス間の待機秒数")
    parser.add_argument("--timeout-ms", type=int, default=10_000, help="HTTPタイムアウト（ミリ秒）")
    parser.add_argument("--save-html", action="store_true", help="取得したHTMLを work 側へ保存する")
    parser.add_argument("--headful", action="store_true", help="互換オプション。HTTPスクレイパーなので無視します")
    parser.add_argument("--no-resume", action="store_true", help="既存の保存結果を無視して取り直す")
    parser.add_argument("--no-html-documents", action="store_true", help="HTML本文ページを文書候補に含めない")
    return parser


def load_target(slug: str) -> dict:
    return gijiroku_targets.load_gijiroku_target(slug, expected_system="static-kaigiroku-dir")


def normalized_url(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path or "/"
    return urlunsplit((parts.scheme or "https", parts.netloc, path, parts.query, ""))


def path_extension(url: str) -> str:
    return Path(urlsplit(url).path).suffix.lower()


def crawl_prefix(start_url: str) -> str:
    path = urlsplit(start_url).path or "/"
    if not path.endswith("/"):
        path_dir = path.rsplit("/", 1)[0] + "/"
    else:
        path_dir = path

    segments = [segment for segment in path_dir.split("/") if segment]
    for idx, segment in enumerate(segments):
        if MINUTES_DIR_RE.search(segment):
            return "/" + "/".join(segments[: idx + 1]) + "/"
    return path_dir


def same_host(start_url: str, candidate_url: str) -> bool:
    return urlsplit(start_url).netloc == urlsplit(candidate_url).netloc


def should_follow_page(start_url: str, prefix: str, candidate_url: str) -> bool:
    if not same_host(start_url, candidate_url):
        return False
    parts = urlsplit(candidate_url)
    path = parts.path or "/"
    if not path.startswith(prefix):
        return False
    ext = path_extension(candidate_url)
    if ext in SKIP_EXTENSIONS or ext == ".pdf":
        return False
    return ext in DOCUMENT_EXTENSIONS


def should_follow_related_minutes_page(start_url: str, candidate_url: str, label: str) -> bool:
    if not same_host(start_url, candidate_url):
        return False
    path = urlsplit(candidate_url).path.lower()
    if not any(keyword in path for keyword in ("gikai", "shigikai", "songikai", "parliament")):
        return False
    ext = path_extension(candidate_url)
    if ext in SKIP_EXTENSIONS or ext == ".pdf":
        return False
    return ext in DOCUMENT_EXTENSIONS and looks_like_minutes_link(label, candidate_url)


def is_document_pdf(candidate_url: str) -> bool:
    return path_extension(candidate_url) == ".pdf"


def looks_like_minutes_link(label: str, url: str) -> bool:
    haystack = normalize_space(f"{label} {urlsplit(url).path}").lower()
    if any(word in haystack for word in ("会議録", "議事録", "kaigiroku", "gijiroku")):
        return True
    if any(word in haystack for word in ("定例会", "臨時会", "本会議", "委員会")) and YEAR_HINT_RE.search(haystack):
        return True
    return False


def clean_label(value: str, fallback: str) -> str:
    label = clean_pdf_label(value)
    label = SIZE_SUFFIX_RE.sub("", label).strip()
    if label.lower().replace(" ", "") in GENERIC_LABELS:
        label = ""
    return label or fallback


def is_skippable_document_label(label: str) -> bool:
    return SKIP_DOCUMENT_LABEL_RE.match(normalize_space(label)) is not None


def relative_page_filename(page_url: str, fallback: str = "page") -> str:
    path = urlsplit(page_url).path.strip("/")
    if not path:
        return f"{fallback}.html"
    safe = sanitize_filename(path.replace("/", "__"), fallback)
    return safe + ".html"


def text_from_html(soup: BeautifulSoup) -> str:
    for node in soup.select("script, style, noscript, header, footer, nav, form"):
        node.decompose()

    content = (
        soup.select_one("main")
        or soup.select_one("article")
        or soup.select_one("#main")
        or soup.select_one("#content")
        or soup.select_one("#contents")
        or soup.select_one(".contents")
        or soup.body
        or soup
    )
    for br in content.find_all("br"):
        br.replace_with("\n")
    for block in content.find_all(["p", "div", "li", "tr", "h1", "h2", "h3", "h4"]):
        block.append("\n")
    lines = [normalize_space(line) for line in content.get_text("\n").splitlines()]
    return normalize_pdf_text("\n".join(line for line in lines if line))


def looks_like_html_minutes_document(title: str, url: str, text: str) -> bool:
    if len(text) < 800:
        return False
    marker_count = sum(1 for marker in HTML_MINUTES_MARKERS if marker in text)
    if marker_count >= 2:
        return True
    title_haystack = normalize_space(f"{title} {urlsplit(url).path}")
    return marker_count >= 1 and looks_like_minutes_link(title_haystack, url)


def discover_items(
    session: requests.Session,
    start_url: str,
    timeout_ms: int,
    pages_dir: Path | None,
    *,
    max_pages: int,
    include_html_documents: bool,
) -> list[StaticMinutesItem]:
    prefix = crawl_prefix(start_url)
    queue: list[str] = [normalized_url(start_url)]
    seen_pages: set[str] = set()
    items_by_url: dict[str, StaticMinutesItem] = {}

    while queue:
        page_url = queue.pop(0)
        if page_url in seen_pages:
            continue
        if max_pages > 0 and len(seen_pages) >= max_pages:
            break
        seen_pages.add(page_url)

        try:
            page_html = request_text(session, page_url, timeout_ms)
        except Exception as exc:
            print(f"[WARN] HTML取得失敗: {page_url} ({exc})", flush=True)
            continue

        soup = BeautifulSoup(page_html, "html.parser")
        title = page_title(soup)
        page_year_label, page_source_year = extract_year_info(title, page_url)
        if pages_dir is not None:
            gijiroku_storage.write_text(pages_dir / relative_page_filename(page_url), page_html, compress=True)

        if include_html_documents:
            html_text = text_from_html(BeautifulSoup(page_html, "html.parser"))
            if looks_like_html_minutes_document(title, page_url, html_text):
                year_label, source_year = extract_year_info(title, page_url)
                if year_label == "不明":
                    year_label, source_year = page_year_label, page_source_year
                item = StaticMinutesItem(
                    title=clean_label(title, "会議録"),
                    url=page_url,
                    doc_type="html",
                    year_label=year_label,
                    source_year=source_year,
                    source_fino=None,
                    page_url=page_url,
                    page_title=title,
                    meeting_group=None,
                )
                items_by_url.setdefault(page_url, item)

        content = soup.select_one("main") or soup.select_one("#main") or soup.select_one("#content") or soup.body or soup
        current_group: str | None = None
        for node in content.descendants:
            node_name = getattr(node, "name", None)
            if node_name in {"caption", "h2", "h3", "h4", "h5", "p"}:
                if not node.find("a"):
                    group = clean_label(node.get_text(" ", strip=True), "")
                    if group and len(group) <= 120:
                        current_group = group
                continue

            if node_name != "a" or not node.has_attr("href"):
                continue

            href = str(node.get("href", "")).strip()
            if not href or href.startswith(("mailto:", "tel:", "javascript:")):
                continue
            absolute = normalized_url(urljoin(page_url, href))
            label = clean_label(node.get_text(" ", strip=True), Path(urlsplit(absolute).path).stem or title or "会議録")
            if is_skippable_document_label(label):
                continue

            if (
                should_follow_page(start_url, prefix, absolute)
                or should_follow_related_minutes_page(start_url, absolute, label)
            ) and absolute not in seen_pages and absolute not in queue:
                if looks_like_minutes_link(label, absolute) or YEAR_HINT_RE.search(urlsplit(absolute).path):
                    queue.append(absolute)

            if not is_document_pdf(absolute) or not same_host(start_url, absolute):
                continue
            if not looks_like_minutes_link(label, absolute) and not looks_like_minutes_link(current_group or "", absolute):
                continue

            year_label, source_year = extract_year_info(label, current_group or "", title, page_url)
            if year_label == "不明":
                year_label = page_year_label
                source_year = page_source_year
            item = StaticMinutesItem(
                title=label,
                url=absolute,
                doc_type="pdf",
                year_label=year_label,
                source_year=source_year,
                source_fino=attachment_id(absolute),
                page_url=page_url,
                page_title=title,
                meeting_group=current_group,
            )
            items_by_url.setdefault(absolute, item)

    return list(items_by_url.values())


def composed_minutes_text(item: StaticMinutesItem, body_text: str) -> str:
    header = [item.year_label, item.title]
    if item.meeting_group and normalize_space(item.meeting_group) != normalize_space(item.title):
        header.append(item.meeting_group)
    header.append(f"出典: {item.url}")
    return "\n".join(header) + "\n\n" + body_text.strip() + "\n"


def extract_html_document_text(session: requests.Session, url: str, timeout_ms: int) -> str:
    page_html = request_text(session, url, timeout_ms)
    return text_from_html(BeautifulSoup(page_html, "html.parser"))


def main() -> int:
    args = build_parser().parse_args()
    if not args.ack_robots:
        print("ERROR: --ack-robots を指定してください。robots.txt・利用規約・許諾確認後に実行してください。", file=sys.stderr)
        return 2

    target = load_target(args.slug)
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
    print("[INFO] 静的ディレクトリを巡回中...")
    meeting_items = discover_items(
        session,
        str(target["source_url"]),
        args.timeout_ms,
        pages_dir,
        max_pages=args.max_pages,
        include_html_documents=not args.no_html_documents,
    )
    if args.max_meetings > 0:
        meeting_items = meeting_items[: args.max_meetings]
    print(f"[INFO] 文書候補 {len(meeting_items)} 件")

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
            fieldnames=["title", "year", "url", "doc_type", "status", "output", "pdf", "error", "documents", "fragments"],
        )
        writer.writeheader()

        for idx, item in enumerate(meeting_items, start=1):
            print(f"[{idx}/{len(meeting_items)}] {item.year_label} {item.title}")
            year_dir = normalize_year_dir(item.year_label)
            text_base = downloads_dir / year_dir / (sanitize_filename(item.title, "meeting") + ".txt")
            pdf_path = pdf_dir / year_dir / f"{item.source_fino or idx}_{sanitize_filename(item.title, 'meeting')}.pdf"
            resume_key = gijiroku_storage.item_signature(asdict(item))
            existing_output = gijiroku_storage.existing_output(text_base)
            status = ""
            output_path = ""
            pdf_output = ""
            error_msg = ""

            if not args.no_resume and existing_output is not None:
                status = "skipped_existing"
                output_path = str(existing_output)
            else:
                try:
                    if item.doc_type == "pdf":
                        pdf_bytes = request_bytes(session, item.url, args.timeout_ms)
                        gijiroku_storage.write_bytes(pdf_path, pdf_bytes, compress=False)
                        pdf_output = str(pdf_path)
                        extracted = extract_pdf_text(pdf_bytes)
                    else:
                        extracted = extract_html_document_text(session, item.url, args.timeout_ms)
                    if not extracted:
                        status = "empty_text"
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
                "doc_type": item.doc_type,
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
                    "doc_type": item.doc_type,
                    "status": status,
                    "output": output_path,
                    "pdf": pdf_output,
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
