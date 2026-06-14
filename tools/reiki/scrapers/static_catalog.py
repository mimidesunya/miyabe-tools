#!/usr/bin/env python3
"""カタログ型例規集スクレイパの共通処理。

「目次（カタログ）ページから個別例規 HTML を辿って取得する」系統
（reiki.html / joureikun / legalcrud / reiki_menu / jourei-v5 など）で
共有する、ダウンロード・正規化 HTML 生成・manifest・進捗・レジューム
処理をまとめる。サイト固有の差分（目次の辿り方・本文の取り出し方）は
呼び出し側が discover / parse_article で渡す。

index builder（tools/search）が読む契約に合わせ、成果物は
- data/reiki/{slug}/html/{stem}.html  … 正規化 HTML（law-title/law-number/law-date/law-content）
- work/reiki/{slug}/markdown/{stem}.md … Markdown（補強用）
- work/reiki/{slug}/source/{stem}.html.gz … 取得時の生 HTML
- work/reiki/{slug}/source_manifest.json.gz … source_file/detail_url/日付などの台帳
を生成する。
"""

from __future__ import annotations

import html as html_lib
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import requests

SCRAPER_DIR = Path(__file__).resolve().parent
MODULE_DIR = SCRAPER_DIR.parent
# batch runner はこのファイルを直接実行する兄弟スクリプトから import する。
sys.path.append(str(MODULE_DIR))
sys.path.append(str(SCRAPER_DIR))
import reiki_io  # noqa: E402
import reiki_targets  # noqa: E402


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_DELAY = 0.3
TIMEOUT = 20

_ERA_BASE = {"明治": 1867, "大正": 1911, "昭和": 1925, "平成": 1988, "令和": 2018}
_WAREKI_RE = re.compile(r"(明治|大正|昭和|平成|令和)([0-9０-９元]+)年([0-9０-９]+)月([0-9０-９]+)日")
_STEM_RE = re.compile(r"[^0-9A-Za-z._-]+")


def to_seireki(text: str) -> str:
    """和暦表記を YYYY-MM-DD へ。判定できなければ空文字。"""
    if not text:
        return ""
    normalized = text.replace("元年", "1年")
    normalized = normalized.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    match = _WAREKI_RE.search(normalized)
    if not match:
        return ""
    era, year, month, day = match.groups()
    return f"{_ERA_BASE[era] + int(year):04d}-{int(month):02d}-{int(day):02d}"


def extract_wareki(text: str) -> str:
    match = _WAREKI_RE.search(text or "")
    return match.group(0) if match else ""


def sanitize_stem(value: str) -> str:
    stem = _STEM_RE.sub("_", str(value).strip()).strip("._-")
    return stem or "item"


@dataclass
class Article:
    """カタログから拾った個別例規 1 件への参照。"""

    code: str  # 安定識別子（保存ファイル名の stem に使う）
    url: str  # 詳細ページ URL
    title: str = ""  # カタログ側で分かるならタイトル（任意）


@dataclass
class ParsedArticle:
    """詳細ページ 1 件を解析した結果。"""

    title: str
    content_html: str
    date_text: str = ""  # 公布日などの和暦原文（任意）
    number: str = ""  # ○○条例第 X 号（任意）
    taxonomy_path: str = ""  # 体系・分野（任意）
    extra: dict = field(default_factory=dict)


def _esc(value: str) -> str:
    return html_lib.escape(str(value or ""), quote=False)


def build_clean_html(parsed: ParsedArticle, iso_date: str) -> str:
    parts = [f'<div class="law-title">{_esc(parsed.title)}</div>']
    if parsed.number:
        parts.append(f'<div class="law-number">{_esc(parsed.number)}</div>')
    # index builder は law-date の "(YYYY-MM-DD)" を公布日として読む。
    if parsed.date_text and re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso_date or ""):
        parts.append(f'<div class="law-date">{_esc(parsed.date_text)} ({iso_date})</div>')
    parts.append('<div class="law-content">')
    parts.append(parsed.content_html.strip())
    parts.append("</div>")
    return "\n".join(parts)


def build_markdown(parsed: ParsedArticle, iso_date: str, content_text: str) -> str:
    lines = [f"# {parsed.title}".rstrip()]
    lines.append("")
    if parsed.number:
        lines.append(f"**番号:** {parsed.number}")
    if parsed.date_text:
        suffix = f" ({iso_date})" if iso_date else ""
        lines.append(f"**日付:** {parsed.date_text}{suffix}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(content_text.strip())
    return "\n".join(lines).strip() + "\n"


def html_to_plain(value: str) -> str:
    text = re.sub(r"<(script|style)[\s\S]*?</\1>", "", value, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(div|p|li|tr|table|section|article|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t　]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_text(session: requests.Session, url: str, *, referer: str = "") -> str | None:
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer
    try:
        response = session.get(url, headers=headers, timeout=TIMEOUT)
        response.raise_for_status()
    except Exception as exc:
        print(f"[WARN] fetch failed {url}: {exc}", flush=True)
        return None
    if not response.encoding or response.encoding.lower() in {"iso-8859-1", "ascii"}:
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def emit_progress(current: int, total: int, state_path: Path) -> None:
    reiki_io.update_progress_state(state_path, current=current, total=total, unit="ordinance")
    print(f"[PROGRESS] unit=ordinance current={max(0, current)} total={max(0, total)}", flush=True)


DiscoverFn = Callable[[requests.Session, str], list[Article]]
ParseFn = Callable[[str, str], ParsedArticle | None]


def run(
    *,
    slug: str,
    expected_system: str,
    discover: DiscoverFn,
    parse_article: ParseFn,
    force: bool = False,
    check_updates: bool = False,
    delay: float = DEFAULT_DELAY,
    limit: int = 0,
) -> int:
    target = reiki_targets.load_reiki_target(slug, expected_system=expected_system)
    source_dir = Path(target["source_dir"])
    html_dir = Path(target["html_dir"])
    markdown_dir = Path(target["markdown_dir"])
    work_root = Path(target["work_root"])
    manifest_path = work_root / "source_manifest.json.gz"
    state_path = work_root / "scrape_state.json"
    source_url = str(target["source_url"])

    source_dir.mkdir(parents=True, exist_ok=True)
    html_dir.mkdir(parents=True, exist_ok=True)
    markdown_dir.mkdir(parents=True, exist_ok=True)

    print(f"Target: {target['name']} ({target['slug']}, {target['system_type']})", flush=True)
    print(f"Source URL: {source_url}", flush=True)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    articles = discover(session, source_url)
    if limit > 0:
        articles = articles[:limit]
    total = len(articles)
    print(f"Discovered {total} ordinances.", flush=True)
    if total == 0:
        raise RuntimeError("No ordinances discovered; refusing to mark target as scraped.")

    emit_progress(0, total, state_path)

    manifest: list[dict] = []
    downloaded = 0
    skipped = 0
    failed = 0
    seen_stems: set[str] = set()
    for index, article in enumerate(articles):
        stem = sanitize_stem(article.code)
        if stem in seen_stems:
            stem = f"{stem}_{index}"
        seen_stems.add(stem)
        filename = f"{stem}.html"
        source_path = source_dir / filename
        clean_path = html_dir / filename
        markdown_path = markdown_dir / f"{stem}.md"

        existing_source = reiki_io.existing_path(source_path)
        existing_clean = reiki_io.existing_path(clean_path)
        existing_markdown = reiki_io.existing_path(markdown_path)
        complete = (
            existing_source is not None
            and existing_clean is not None
            and existing_markdown is not None
        )

        parsed: ParsedArticle | None = None
        iso_date = ""
        if force or check_updates or not complete:
            raw = fetch_text(session, article.url, referer=source_url)
            if raw is None:
                failed += 1
                continue
            try:
                parsed = parse_article(raw, article.url)
            except Exception as exc:
                print(f"[WARN] parse failed {article.url}: {exc}", flush=True)
                parsed = None
            if parsed is None or not parsed.content_html.strip():
                failed += 1
                continue
            reiki_io.write_text(source_path, raw, compress=True)
            iso_date = to_seireki(parsed.date_text)
            clean_html = build_clean_html(parsed, iso_date)
            content_text = html_to_plain(parsed.content_html)
            reiki_io.write_text(clean_path, clean_html)
            reiki_io.write_text(markdown_path, build_markdown(parsed, iso_date, content_text), compress=True)
            downloaded += 1
            time.sleep(delay)
        else:
            skipped += 1

        title = article.title or (parsed.title if parsed else "")
        number = parsed.number if parsed else ""
        date_text = parsed.date_text if parsed else ""
        if not iso_date and date_text:
            iso_date = to_seireki(date_text)
        manifest.append(
            {
                "code": article.code,
                "source_file": filename,
                "stored_source_file": reiki_io.gzip_path(source_path).name,
                "detail_url": article.url,
                "source_url": article.url,
                "title": title,
                "number": number,
                "enactment_date": iso_date,
                "taxonomy_path": parsed.taxonomy_path if parsed else "",
            }
        )

        if ((index + 1) % 25) == 0 or (index + 1) == total:
            reiki_io.write_json(manifest_path, manifest, compress=True)
            emit_progress(index + 1, total, state_path)

    reiki_io.write_json(manifest_path, manifest, compress=True)
    emit_progress(total, total, state_path)
    print(
        f"Finished. downloaded={downloaded} skipped={skipped} failed={failed} "
        f"manifest={len(manifest)} -> {manifest_path}",
        flush=True,
    )
    return 0
