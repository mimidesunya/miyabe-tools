#!/usr/bin/env python3
"""公式サイト上の PDF/テキスト会議録ページ向け HTTP スクレイパ。

香美市向けから始まったが、補助関数は他の静的 PDF スクレイパでも共有している。
PDF link を集めて本文を抽出し、安定した出力名の決定は gijiroku_planning に任せる。
"""

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

SCRAPER_DIR = Path(__file__).resolve().parent
MODULE_DIR = SCRAPER_DIR.parent
# 子プロセス型 batch runner から直接実行されるため、
# scraper ディレクトリとその親の両方を import 対象にする。
sys.path.append(str(MODULE_DIR))
sys.path.append(str(SCRAPER_DIR))
import gijiroku_planning
import pdf_ocr
import gijiroku_storage
import gijiroku_targets

try:
    import minutes_kind
except ModuleNotFoundError:  # pragma: no cover
    from tools.gijiroku import minutes_kind


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)
YEAR_LABEL_RE = re.compile(r"(昭和|平成|令和)\s*([元\d０-９]+)年")
WESTERN_REIWA_LABEL_RE = re.compile(r"(20\d{2})（令和([元\d０-９]+)）年")
# 会議録を `/gijiroku/r08/01230101.htm` のように元号の略記ディレクトリで
# 分ける取得元がある（ときがわ町）。題名が空なので、ここを読まないと
# 年が「不明」のままになり、並び順も鮮度も出せない。
ERA_DIR_RE = re.compile(r"/([rhs])(\d{2})/", re.IGNORECASE)
ERA_DIR_PREFIX = {"r": "令和", "h": "平成", "s": "昭和"}
PDF_SIZE_SUFFIX_RE = re.compile(
    r"\s*[［\[(（]?\s*PDF(?:ファイル)?\s*[／/：:｜|]\s*[^］\]）)]+[］\]）)]?\s*$",
    re.IGNORECASE,
)
KAMI_MINUTES_PAGE_RE = re.compile(r"^/site/gikai/kaigiroku(?:\d{4}|sokuhou)\.html$")
ATTACHMENT_ID_RE = re.compile(r"/uploaded/attachment/(\d+)\.pdf$", re.I)
ERA_BASE_YEAR = {"昭和": 1925, "平成": 1988, "令和": 2018}
FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
MINUTES_PAGE_KEYWORDS = (
    "会議録",
    # 「会議記録」と書く取得元がある（浦幌町）。「会議録」を含まないので
    # 別の語として並べないと、年別一覧へ降りられず 1 件も見つからない。
    "会議記録",
    "議事録",
    "kaigiroku",
    "gijiroku",
    "minutes",
    "定例会",
    "臨時会",
)
GIKAI_PATH_KEYWORDS = ("gikai", "gicho", "gichou", "gityou")
# 会議録そのものを指す URL の断片。議会の階層に置かれない自治体がある。
MINUTES_PATH_KEYWORDS = ("kaigiroku", "gijiroku", "kaigi-roku", "minutes")
# 拡張子を URL に出さずに PDF を返す配信エンドポイント。実体は
# Content-Type: application/pdf なので、URL だけでは PDF と判別できない。
ATTACHMENT_ENDPOINT_RE = re.compile(r"/UploadFileOutput\.ashx", re.I)


def looks_like_attachment_pdf(url: str, anchor_text: str) -> bool:
    """配信エンドポイント経由の PDF 添付らしいリンクかを判定する。

    誤って HTML ページを PDF として扱わないよう、会議録らしいリンク文字列を
    持つものだけに限る。
    """
    if anchor_text_names_a_pdf(url, anchor_text):
        return True
    if not ATTACHMENT_ENDPOINT_RE.search(url) and not query_names_a_pdf(url):
        return False
    haystack = normalize_space(anchor_text).lower()
    return any(keyword.lower() in haystack for keyword in MINUTES_PAGE_KEYWORDS)


def query_names_a_pdf(url: str) -> bool:
    """パスではなくクエリでファイル名を渡す配信 URL かを見る。

    /dl?q=…filelib_….pdf のように、拡張子がクエリ側にしか出ない取得元が
    ある（上天草市など）。パス末尾だけを見ると PDF と気づけない。
    """
    parts = urlsplit(url)
    if parts.query == "":
        return False
    return ".pdf" in parts.query.lower()


# ファイル名を URL のどこにも出さず、リンク文字列だけで示す配信口がある
# （上士幌町の /dl.php?up_code=… に「…会議録.pdf」と添える形）。
DOWNLOAD_ENDPOINT_RE = re.compile(r"/(?:dl|download|file)\.(?:php|aspx?|cgi|do)\b", re.I)


def anchor_text_names_a_pdf(url: str, anchor_text: str) -> bool:
    """配信口へのリンクで、文字列側がファイル名を名乗っているかを見る。"""
    if not DOWNLOAD_ENDPOINT_RE.search(url):
        return False
    return normalize_space(anchor_text).lower().endswith(".pdf")


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

    # 題名から読めないときだけ、URL の元号ディレクトリを見る。
    for value in values:
        directory = ERA_DIR_RE.search(str(value or ""))
        if not directory:
            continue
        era = ERA_DIR_PREFIX.get(directory.group(1).lower())
        era_year = int(directory.group(2))
        if era is None or era_year <= 0:
            continue
        return f"{era}{era_year}年", era_to_gregorian(era, str(era_year))

    return "不明", None


def sanitize_filename(text: str, fallback: str) -> str:
    return gijiroku_planning.sanitize_filename(normalize_space(text), fallback)


def clean_pdf_label(value: str) -> str:
    # 全角・半角の「[PDFファイル／248KB]」が残ると、題名末尾がファイル注記になる。
    cleaned = minutes_kind.strip_pdf_notes(value)
    if not cleaned:
        cleaned = PDF_SIZE_SUFFIX_RE.sub("", normalize_space(value))
        cleaned = re.sub(r"\s*PDFファイル\s*$", "", cleaned, flags=re.I).strip()
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


# 拡張子のない URL が PDF などを返すことがある。バイナリをそのまま
# HTML パーサへ渡すと AssertionError でプロセスごと落ちるため、
# ここで HTML ではない応答を弾く。
def looks_like_html_response(content_type: str, raw: bytes) -> bool:
    media_type = str(content_type or "").split(";", 1)[0].strip().lower()
    if media_type and not (
        media_type.startswith("text/")
        or media_type.endswith("+xml")
        or media_type in {"application/xml", "application/xhtml"}
    ):
        return False
    # Content-Type が text/html でも実体が PDF のサーバがある。
    head = raw[:1024]
    if head.startswith((b"%PDF-", b"PK\x03\x04", b"\x89PNG", b"GIF8", b"\xff\xd8\xff", b"\x1f\x8b")):
        return False
    return b"\x00" not in head


def request_text(session: requests.Session, url: str, timeout_ms: int) -> str:
    response = session.get(url, timeout=max(timeout_ms / 1000.0, 1.0))
    response.raise_for_status()
    raw = response.content
    if not looks_like_html_response(response.headers.get("Content-Type", ""), raw):
        raise ValueError(
            f"HTML ではない応答のため解析を中止します: {response.headers.get('Content-Type', '')!r} {url}"
        )
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
    # `/site/` を含む CMS だけを見ていた。使わない自治体では一覧を 1 ページも
    # 辿れず、入口に並ぶぶんしか取れない（一宮町は 83 件の一覧に対して 5 件）。
    # 同じサイトの中で、入口と同じ議会の階層にいるページを辿る。
    if "/site/" in path:
        return True
    # 議会・会議録だと分かる場所にあるページは、入口と階層が違っても辿る。
    # 小野町は入口が `/life/4/19/99/` で年別一覧が `/soshiki/10/kaigiroku08.html`
    # にあり、階層だけで見ると 1 ページも辿れなかった。
    if any(keyword in path for keyword in GIKAI_PATH_KEYWORDS + MINUTES_PATH_KEYWORDS):
        return True
    start_path = start.path.lower()
    # 入口のディレクトリを 1 つ上まで遡った範囲を「同じ階層」とみなす。
    # `/info/gikai/2/16.html` なら `/info/gikai/` の下。
    base = start_path.rsplit("/", 2)[0] + "/" if start_path.count("/") >= 2 else "/"
    return path.startswith(base)


# 議会の階層にあっても会議録ではないページ。ここを辿ると議会だよりの PDF が
# 会議録として混ざる（小野町は 186 号・187 号を拾っていた）。
NOT_MINUTES_PAGE_RE = re.compile(
    r"だより|広報|koho|kouhou|dayori|傍聴|請願|陳情|名簿|中継|録画|交際費|kousaihi|政務活動費|視察",
    re.I,
)
# 「令和8年」だけのリンク。会議録の一覧から年別へ降りるときの形。
#
# 書き方は取得元でばらつく。元号を並べるだけでは足りず、
# 西暦（岐南町の「2026年」）と、元号の略記（東峰村の「R8年度」）でも
# 年別一覧へ降りられなくなっていた。どちらも入口ページ直下、または
# 会議録のページから辿るときにしか使わないので、緩めても他所へは出ない。
YEAR_ONLY_ANCHOR_RE = re.compile(
    r"^\s*(?:"
    r"(?:令和|平成|昭和|大正)\s*(?:\d{1,2}|元)"
    r"|(?:[RHSTrhst])\s*\.?\s*(?:\d{1,2}|元)"
    r"|(?:19|20)\d{2}"
    r")\s*年(?:度)?(?:[（(][^）)]*[）)])?\s*$"
)


def looks_like_generic_minutes_page(anchor_text: str, url: str, from_minutes_page: bool = False) -> bool:
    parts = urlsplit(url)
    path = parts.path.lower()
    haystack = normalize_space(f"{anchor_text} {parts.path}").lower()
    if NOT_MINUTES_PAGE_RE.search(f"{anchor_text} {parts.path}"):
        return False
    if any(keyword.lower() in haystack for keyword in MINUTES_PAGE_KEYWORDS):
        return True
    if any(keyword in path for keyword in GIKAI_PATH_KEYWORDS) and YEAR_OR_LIST_RE.search(haystack):
        return True
    # 会議録の一覧から降りる年別リンクは「令和8年」としか書かれていないことが
    # ある（那珂川町）。会議録のページから辿るときだけ通す。
    return from_minutes_page and bool(YEAR_ONLY_ANCHOR_RE.match(normalize_space(anchor_text)))


def should_follow_minutes_page(
    start_url: str,
    href: str,
    anchor_text: str,
    strict_kami: bool,
    from_minutes_page: bool = False,
) -> str | None:
    absolute = urljoin(start_url, href.strip())
    absolute = absolute.split("#", 1)[0]
    if strict_kami:
        return absolute if is_kami_minutes_page(absolute) else None
    if not is_same_site_html_page(start_url, absolute):
        return None
    if looks_like_generic_minutes_page(anchor_text, absolute, from_minutes_page):
        return absolute
    return None


# 自治体サイトが添付 PDF を置く場所。CMS ごとに違う。`/uploaded/attachment/`
# だけを見ていたので、`assets/files/` に置く取得元では PDF が 95 件並んで
# いても 1 件も通らなかった（南種子町・御宿町）。
SITE_ATTACHMENT_DIRS = (
    "/uploaded/attachment/",
    "/assets/files/",
    "/content/files/",
    "/files/",
    "/data/",
)


def is_site_attachment_pdf(url: str, page_url: str = "") -> bool:
    """この PDF を、その一覧ページが載せている会議録として扱ってよいか。

    置き場所の名前を数え上げても追いつかない（訓子府町 `/fs/`、大宜味村
    `/wp-content/uploads/`、美郷町 `/files/original/`）。同じホストに置かれた
    PDF なら、その一覧ページの持ち物として扱う。会議録でない PDF は
    このあとの `non_minutes_reason` が落とす。外部サイトの PDF だけを弾く。
    """
    parts = urlsplit(url)
    if not parts.path.lower().endswith(".pdf"):
        return False
    if page_url:
        page_host = urlsplit(page_url).netloc.lower()
        if page_host and parts.netloc.lower() == page_host:
            return True
    return any(directory in parts.path.lower() for directory in SITE_ATTACHMENT_DIRS)


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


# HTML の `<base href>`。相対 URL はこれを基準に解決する。見落とすと、
# ページの階層をそのまま前置してしまい 404 になる。御宿町・南種子町・一宮町は
# 3 件とも `<base href="https://…/">` を持っていて、候補は見つかるのに
# ダウンロードが全部 404 だった（候補 21 件に対して保存 0 件）。
def page_base_url(soup, page_url: str) -> str:
    base = soup.find("base", href=True)
    if base is None:
        return page_url
    href = str(base.get("href") or "").strip()
    return urljoin(page_url, href) if href else page_url


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

    pages: dict[str, None] = {start_url: None}
    # 年度別の一覧が親ページからぶら下がることがある。入口だけを見ていると、
    # 入口に並ぶぶんしか取れない（一宮町は 83 件の一覧に対して 5 件）。
    # 辿った先からも探す。深さは 2 まで。議会の階層から出ない。
    frontier = [(start_url, start_html, 0)]
    selectors = "#subsite_menu_wrap a[href], #site_navi a[href], #main_body a[href], a[href]"
    while frontier:
        current_url, current_html, depth = frontier.pop(0)
        soup = BeautifulSoup(current_html, "html.parser")
        # `<base href>` を見ないと、ページの階層を前置して 404 になる。
        current_base = page_base_url(soup, current_url)
        # いま見ているページ自体が会議録の一覧なら、年だけのリンクも辿ってよい。
        current_is_minutes = current_url == start_url or looks_like_generic_minutes_page(
            page_title(soup), current_url
        )
        for anchor in soup.select(selectors):
            href = str(anchor.get("href", "")).strip()
            if not href:
                continue
            page_url = should_follow_minutes_page(
                start_url,
                urljoin(current_base, href),
                anchor.get_text(" ", strip=True),
                strict_kami,
                current_is_minutes,
            )
            if not page_url or page_url in pages:
                continue
            pages[page_url] = None
            if max_pages > 0 and len(pages) >= max_pages:
                LIST_PAGE_LIMIT_HIT.append(start_url)
                return list(pages.keys())
            # 「議会 → 年 → 会議 → PDF」と 3 段置く取得元がある（那珂川町・小野町）。
            # 2 段で止めると、その先の PDF が 1 件も見えない。ページ数は
            # max_pages で頭打ちなので、深さを 1 つ増やしても際限なくは広がらない。
            if depth + 1 >= 3:
                continue
            try:
                child_html = request_text(session, page_url, timeout_ms)
            except Exception:
                continue
            frontier.append((page_url, child_html, depth + 1))
    return list(pages.keys())


# 一覧ページの上限に当たった入口。当たったならまだ辿る先が残っている。
LIST_PAGE_LIMIT_HIT: list[str] = []


def discover_pdf_items(
    session: requests.Session,
    page_urls: list[str],
    timeout_ms: int,
    pages_dir: Path | None = None,
    *,
    require_site_attachment: bool = False,
    walk: dict | None = None,
) -> list[PdfMeetingItem]:
    """`walk` を渡すと、解析できなかった一覧ページの数を控える。"""
    items_by_url: dict[str, PdfMeetingItem] = {}
    missed: list[str] = []
    dropped_by_url: dict[str, str] = {}

    for page_url in page_urls:
        try:
            page_html = request_text(session, page_url, timeout_ms)
            soup = BeautifulSoup(page_html, "html.parser")
        except Exception as exc:
            # 1 ページの取得・解析失敗で自治体全体を落とさない。
            print(f"[WARN] 一覧ページを解析できません: {page_url} ({exc})", file=sys.stderr)
            missed.append(page_url)
            continue
        title = page_title(soup)
        base_url = page_base_url(soup, page_url)
        page_year_label, page_source_year = extract_year_info(title)
        if pages_dir is not None:
            filename = sanitize_filename(Path(urlsplit(page_url).path).stem, "page") + ".html"
            gijiroku_storage.write_text(pages_dir / filename, page_html, compress=True)

        # 本文の入れ物は取得元ごとに違う。決め打ちに当たらないサイトでは
        # PDF が 95 件並んでいても候補 0 件になり、しかも成功として終わって
        # いた（南種子町・御宿町）。当たらなければページ全体を見る。
        content = None
        for selector in ("#main_body .detail_free", "#main_body", "main", "#content", ".contents"):
            found = soup.select_one(selector)
            # 入れ物に PDF が 1 つも無いなら、それは本文ではない。
            if found is not None and found.select_one('a[href$=".pdf"], a[href$=".PDF"]') is not None:
                content = found
                break
        if content is None:
            content = soup
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
            pdf_url = urljoin(base_url, str(node.get("href", "")).strip())
            if not urlsplit(pdf_url).path.lower().endswith(".pdf"):
                continue
            if require_site_attachment and not is_site_attachment_pdf(pdf_url, page_url):
                continue
            label = clean_pdf_label(node.get_text(" ", strip=True))
            if label == "会議録" and current_group:
                label = current_group
            if label == "会議録":
                fallback_id = attachment_id(pdf_url)
                label = f"{title} {fallback_id}" if fallback_id is not None else title
            skip_reason = minutes_kind.non_minutes_reason(label, "")
            if skip_reason:
                dropped_by_url.setdefault(pdf_url, skip_reason)
                continue
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

    dropped_reasons: dict[str, int] = {}
    for reason in dropped_by_url.values():
        dropped_reasons[reason] = dropped_reasons.get(reason, 0) + 1
    if walk is not None:
        walk.update(
            {
                "missed_pages": len(missed),
                "missed_examples": missed[:10],
                "visited_pages": len(page_urls),
                "dropped_non_minutes": len(dropped_by_url),
                "dropped_non_minutes_reasons": dropped_reasons,
            }
        )
    return list(items_by_url.values())


def extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF本文抽出には pypdf が必要です。dev/requirements/gijiroku.txt をインストールしてください。") from exc

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
    return normalize_pdf_text(minutes_kind.repair_cp932_mojibake("\n\n".join(parts)))


def normalize_pdf_text(value: str) -> str:
    # \u58ca\u308c\u305f PDF \u304b\u3089\u5b64\u7acb\u30b5\u30ed\u30b2\u30fc\u30c8\u304c\u6df7\u3058\u308b\u3053\u3068\u304c\u3042\u308b\u3002\u305d\u306e\u307e\u307e\u4fdd\u5b58\u3059\u308b\u3068
    # \u300csurrogates not allowed\u300d\u3067\u66f8\u304d\u51fa\u3057\u304c\u5931\u6557\u3057\u3001\u4f1a\u8b70\u307e\u308b\u3054\u3068\u53d6\u5f97\u5931\u6557\u306b\u306a\u308b\u3002
    text = "".join(ch for ch in value if not 0xD800 <= ord(ch) <= 0xDFFF)
    text = text.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    compact_chars = r"一-龯々〆ヵヶぁ-んァ-ヴー０-９0-9"
    text = re.sub(rf"(?<=[{compact_chars}])[\t ]+(?=[{compact_chars}])", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def ocr_pdf_when_enabled(pdf_path: Path) -> tuple[str, str]:
    """文字情報の無い PDF を OCR する。無効なら何もしない。

    返すのは (本文, 理由)。無効・未導入・失敗のときは本文が空になる。
    同じ PDF を毎周回 OCR し直さないよう、試した回数を自治体ごとに残す。
    """
    if not pdf_ocr.is_enabled():
        return "", ""
    source = Path(pdf_path)
    if not source.is_file():
        return "", ""
    # work/gijiroku/<slug>/pdfs/<年>/<ファイル>.pdf の <slug> ディレクトリ。
    try:
        work_dir = source.parents[2]
    except IndexError:
        return "", ""
    key = source.name
    digest = pdf_ocr.file_digest(source)
    if not pdf_ocr.should_try(work_dir, key, digest):
        return "", ""

    body, reason = pdf_ocr.ocr_pdf_text(source)
    if body:
        pdf_ocr.record_attempt(work_dir, key, digest, status="ok")
        print(f"[INFO] OCR で本文にしました（{len(body)}字）: {source.name}", flush=True)
        return normalize_pdf_text(body), ""
    pdf_ocr.record_attempt(work_dir, key, digest, status="failed", reason=reason)
    print(f"[WARN] OCR でも本文になりませんでした（{reason}）: {source.name}", flush=True)
    return "", reason


def process_pdf_meeting_plan(
    session,
    plan: dict,
    *,
    no_resume: bool,
    timeout_ms: int,
) -> dict:
    """既存テキストも会議録か判定する。ヘッダ付き保存文を本文として再保存しない。"""
    item = plan["item"]
    pdf_path = plan["pdf_path"]
    existing_output = None if no_resume else plan.get("existing_output")
    downloaded = False

    def fetch_pdf_text() -> str:
        nonlocal downloaded
        pdf_bytes = request_bytes(session, item.url, timeout_ms)
        gijiroku_storage.write_bytes(pdf_path, pdf_bytes, compress=False)
        downloaded = True
        return extract_pdf_text(pdf_bytes)

    extracted = ""
    if existing_output is not None:
        try:
            existing_text = gijiroku_storage.read_text_auto(Path(existing_output))
        except Exception:
            existing_text = ""
            existing_output = None
        if existing_text:
            probe = minutes_kind.adopt_minutes_document(
                item.title,
                existing_text,
                url=item.url,
                year_label=item.year_label,
                source_year=getattr(item, "source_year", None),
            )
            if not probe.accepted:
                gijiroku_storage.quarantine_invalid_file(
                    Path(existing_output), reason=probe.reason or "not_minutes"
                )
                return {
                    "status": "skipped_not_minutes",
                    "output_path": "",
                    "item": item,
                    "reason": probe.reason,
                    "downloaded": False,
                }
            held_ok = not probe.held_on or f"Held-On: {probe.held_on}" in existing_text
            if probe.title == item.title and held_ok:
                return {
                    "status": "skipped_existing",
                    "output_path": str(existing_output),
                    "item": item,
                    "reason": None,
                    "downloaded": False,
                }
            if pdf_path.exists():
                try:
                    extracted = extract_pdf_text(gijiroku_storage.read_bytes(pdf_path))
                except Exception:
                    extracted = ""
    try:
        if not extracted:
            extracted = fetch_pdf_text()
    except Exception as exc:
        return {
            "status": "error",
            "output_path": "",
            "item": item,
            "reason": None,
            "downloaded": downloaded,
            "error": str(exc),
        }
    if not extracted:
        # 紙をスキャンしただけで文字情報を持たない PDF がある。OCR が無いと
        # 何周回しても同じように除外される（2026-09-06 時点で 3,860 件）。
        # 通常の巡回では動かさない。1 件に数十秒かかるので、混ぜると一巡が
        # 数日延びる。専用の掃き取りが環境変数で有効にして呼ぶ。
        extracted, ocr_reason = ocr_pdf_when_enabled(pdf_path)
        if not extracted:
            return {
                "status": "empty_pdf_text",
                "output_path": "",
                "item": item,
                "reason": ocr_reason or None,
                "downloaded": downloaded,
            }
    result = gijiroku_planning.persist_adopted_minutes(
        plan,
        extracted,
        compose=composed_minutes_text,
        existing_output=Path(existing_output) if existing_output else None,
        output_key="text_base",
    )
    result["downloaded"] = downloaded
    result["error"] = ""
    return result


def composed_minutes_text(
    item: PdfMeetingItem, pdf_text: str, *, held_on: str | None = None
) -> str:
    header = [item.year_label, item.title]
    if item.meeting_group and normalize_space(item.meeting_group) != normalize_space(item.title):
        header.append(item.meeting_group)
    header.extend(minutes_kind.held_on_header_lines(held_on))
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
    catalog_walk: dict = {}
    meeting_items = discover_pdf_items(
        session,
        page_urls,
        args.timeout_ms,
        pages_dir,
        require_site_attachment=not strict_kami,
        walk=catalog_walk,
    )
    if args.max_meetings > 0:
        meeting_items = meeting_items[: args.max_meetings]
    print(f"[INFO] PDF候補 {len(meeting_items)} 件")
    # 0 件で成功にしない。取得元の作りが変われば PDF は見つからなくなる。
    # 成功として記録されると 30 日ごとに同じ 0 件を繰り返すだけで、誰も
    # 気づかない。南種子町は PDF が 95 件並んでいるのに候補 0 件で「完了」
    # していた。例規側は既にこうしてある。
    #
    # 一覧ページすら開けなかったのなら、それは別の失敗として既に記録される。
    if not meeting_items and page_urls:
        raise SystemExit(
            "[ERROR] 会議録の PDF を 1 件も見つけられませんでした。"
            f"取得元の作りが変わった可能性があります: {target['source_url']}"
        )
    # 解析できなかった一覧ページと、ページ数の上限を残す。残さないと
    # 「発見数＝保存数」で完了に見え、キューは 30 日巡ってこない。
    # 一覧の置き換えが拒まれるなら、今回の走査を取り切れたとは言えない。
    crawl_dropped = int(catalog_walk.get("dropped_non_minutes") or 0)
    explained_drops = gijiroku_storage.explained_non_minutes_drops(
        dropped_count=crawl_dropped,
        missed_pages=int(catalog_walk.get("missed_pages") or 0),
        limit_reached=bool(LIST_PAGE_LIMIT_HIT) or args.max_meetings > 0,
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
        limit_reached=bool(LIST_PAGE_LIMIT_HIT) or args.max_meetings > 0,
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

    with result_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["title", "year", "url", "status", "output", "pdf", "error", "documents", "fragments"],
        )
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
            planned_items,
            no_resume=args.no_resume,
            previous_missing_count=previous_missing,
        )
        date_range = gijiroku_planning.describe_date_range(planned_items)
        if date_range:
            print(f"[INFO] Discovered meeting date range: {date_range}", flush=True)
        gijiroku_planning.save_plan_summary(state_path, state, planned_items, missing_count, previous_missing)
        if missing_count > 0:
            work_mode = gijiroku_planning.work_mode_label(missing_count, previous_missing)
            if work_mode == "update_check":
                print(f"[INFO] Update check found new outputs: {missing_count}/{len(planned_items)}", flush=True)
            else:
                print(f"[INFO] Resume missing outputs first: {missing_count}/{len(planned_items)}", flush=True)
        # PDF 候補数には、本文抽出できない添付や会議録本体ではない PDF が混ざることがある。
        # 保存済み本文・除外・実エラーを分けて、親バッチの完了判定が候補数だけに引っ張られないようにする。
        # 既存ファイルも本文判定する。リンク題名のまま残ると、公開題名が嘘のままになる。
        saved_count = 0
        status_counts: dict[str, int] = {}
        accepted_items: list = []
        body_drop_reasons: dict[str, int] = {}
        emit_progress(saved_count, len(meeting_items), state_path, state)
        work_ids = {id(plan) for plan in work_items}
        ordered_plans = list(work_items) + [plan for plan in planned_items if id(plan) not in work_ids]

        for idx, plan in enumerate(ordered_plans, start=1):
            item = plan["item"]
            print(f"[{idx}/{len(ordered_plans)}] {item.year_label} {item.title}")
            resume_key = plan["resume_key"]
            pdf_path = plan["pdf_path"]
            status = ""
            output_path = ""
            error_msg = ""
            downloaded = False
            result = process_pdf_meeting_plan(
                session, plan, no_resume=args.no_resume, timeout_ms=args.timeout_ms
            )
            status = str(result.get("status") or "")
            output_path = str(result.get("output_path") or "")
            item = result.get("item") or item
            downloaded = bool(result.get("downloaded"))
            error_msg = str(result.get("error") or "")
            if status == "skipped_not_minutes":
                reason = str(result.get("reason") or "non_minutes_body")
                body_drop_reasons[reason] = body_drop_reasons.get(reason, 0) + 1
            elif status in {"saved_text", "skipped_existing"}:
                accepted_items.append(item)
                saved_count += 1

            if status:
                status_counts[status] = status_counts.get(status, 0) + 1
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
            emit_progress(saved_count, len(meeting_items), state_path, state)
            if downloaded and args.delay_seconds > 0 and idx < len(ordered_plans):
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
    emit_progress(
        int(validation["progress_current"]),
        int(validation["progress_total"]),
        state_path,
        state,
    )
    print(f"[DONE] Saved index: {index_json}")
    print(f"[DONE] Result log : {result_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
