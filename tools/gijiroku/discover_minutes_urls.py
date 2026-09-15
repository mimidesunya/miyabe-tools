#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""会議録システムの代表URLを未調査自治体について再探索する。

`assembly_minutes_system_urls.tsv` で `crawl_status=unresolved` かつ URL 空の自治体を対象に、
`municipality_homepages.csv` の公式ホームページを起点として `議会` / `会議録` / `議事録`
系リンクを最大3階層まで辿り、既知の会議録システム（ベンダ）を URL の指紋で判定する。

方針:
- ここは「候補の発見」だけを行う。正本 TSV は書き換えない。候補は work/ の CSV へ出す。
- 既知ベンダ（kaigiroku.net / dbsr / gijiroku.com(voices) / kensakusystem / amivoice /
  discussvision / voicetechno など）は URL の host/パスから高信頼で分類できる。
- 自治体サイト内で会議録ページに辿り着いたがベンダが特定できない場合は、
  代表 URL 候補として低信頼で記録し、`system_type` は空（人手で 独自 / site-gikai-pdf /
  static-kaigiroku-dir を判断）にする。
- 反映は運用者が doc/assembly-minutes-url-survey.md の手順で確認してから行う。
  新規 URL は robots 差分監査が済むまで自動取得されない（enabled にしない限り）。

使い方:
    python tools/gijiroku/discover_minutes_urls.py --limit 15
    python tools/gijiroku/discover_minutes_urls.py --codes 01202 06367 --save-out work/xxx.csv
"""

from __future__ import annotations

import argparse
import csv
import heapq
import itertools
import re
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

SCRAPER_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = SCRAPER_DIR.parent.parent
DATA_ROOT = WORKSPACE_ROOT / "data"
MUNI_DIR = DATA_ROOT / "municipalities"

USER_AGENT = "miyabe-tools/1.0 (+public municipal minutes survey; contact via project)"

# 会議録系リンクを優先するためのキーワード（アンカーテキスト / href 双方に効かせる）。
MINUTES_HINT_RE = re.compile(
    r"議会|会議録|議事録|本会議|定例会|委員会記録|かいぎろく|ぎかい|"
    r"gikai|giji|kaigiroku|minutes|council|assembly",
    re.I,
)
# ナビで頻出だが会議録本体でないものを軽く抑制する。
NEGATIVE_HINT_RE = re.compile(r"選挙|傍聴|請願|中継|ライブ|youtube|議員report|なり手", re.I)

# 会議録キーワードは無いが、そこを経由しないと議会へ辿り着けないハブ（市政ポータル等）。
HUB_HINT_RE = re.compile(
    r"市政|区政|町政|村政|県政|行政|組織|市の(組織|しくみ)|市役所|about|gov(/|$)|shisei|soshiki",
    re.I,
)

# 既知ベンダの URL 指紋。左から順に評価し、最初に当たった system_type を採る。
# (system_type, host 正規表現 or None, path 正規表現 or None)
VENDOR_FINGERPRINTS: list[tuple[str, re.Pattern | None, re.Pattern | None]] = [
    ("kaigiroku.net", re.compile(r"(^|\.)kaigiroku\.net$", re.I), None),
    ("dbsr", re.compile(r"\.dbsr\.jp$", re.I), None),
    ("dbsr", re.compile(r"(^|\.)db-search\.com$", re.I), None),
    ("kensakusystem", re.compile(r"(^|\.)kensakusystem\.jp$", re.I), None),
    ("amivoice", re.compile(r"(^|\.)amivoice\.com$", re.I), None),
    ("discussvision", re.compile(r"(^|\.)discussvision\.net$", re.I), None),
    ("voicetechno", re.compile(r"(^|\.)voicetechno\.net$", re.I), None),
    ("gijiroku.com", re.compile(r"(^|\.)gijiroku\.com$", re.I), None),
    # 自治体自ホストに置かれた gijiroku.com 系（voices）: /voices/gNNv_search.asp
    ("voices", None, re.compile(r"/voices/g\d+v_search\.asp", re.I)),
    # kaigiroku を index.php で公開する自治体ホスト（dbsr 相当）
    ("kaigiroku-indexphp", re.compile(r"^kaigiroku\.(city|pref|town|vill)\.", re.I),
     re.compile(r"/index\.php", re.I)),
]

VENDOR_HOSTS_QUICK = (
    "kaigiroku.net", "dbsr.jp", "db-search.com", "kensakusystem.jp",
    "amivoice.com", "discussvision.net", "voicetechno.net", "gijiroku.com",
)


def is_video_only_vendor(url: str, system_type: str) -> bool:
    """録画・中継の配信だけで、会議録の本文が無い取得元か。

    登録簿では discussvision と kensakusystem の `-vod` を video_only で除外している。
    探索がこれを「高信頼」として返すと、実行時にそのまま取得対象へ載ってしまう
    （指宿市・南さつま市の議会中継リンク）。本文の会議録は別にあることが多いので、
    見つけても探索を続ける。
    """
    if system_type == "discussvision":
        return True
    return system_type == "kensakusystem" and "-vod" in urlsplit(url).path.lower()


def classify_vendor(url: str) -> str | None:
    parts = urlsplit(url)
    host = (parts.netloc or "").lower()
    path = parts.path or ""
    for system_type, host_re, path_re in VENDOR_FINGERPRINTS:
        if host_re is not None and not host_re.search(host):
            continue
        if path_re is not None and not path_re.search(path):
            continue
        if host_re is None and path_re is None:
            continue
        return system_type
    return None


# 自ホスト会議録の深掘り判定用。
MINUTES_TEXT_RE = re.compile(r"会議録|議事録|定例会|臨時会|本会議|会議結果|第\s*\d+\s*回", re.I)
ERA_YEAR_RE = re.compile(r"(令和|平成|昭和|R|H|S)\s*\d{1,2}|20\d{2}|19\d{2}", re.I)
MINUTES_PATH_RE = re.compile(r"gikai|giji|kaigiroku|kaigi|minutes", re.I)
# 会議録本体でない紛らわしいもの（議会だより/広報/日程/傍聴など）は会議録扱いしない。
MINUTES_NEG_RE = re.compile(
    r"だより|便り|広報|kouhou|koho|dayori|newsletter|お知らせ|日程|予定|傍聴|"
    r"名簿|報酬|政務活動|請願|陳情|意見書|選挙|中継|ライブ|録画",
    re.I,
)


def looks_like_minutes_pdf(url: str, text: str) -> bool:
    if not url.lower().split("?", 1)[0].endswith(".pdf"):
        return False
    blob = f"{text} {url}"
    if MINUTES_NEG_RE.search(blob):
        return False
    if MINUTES_TEXT_RE.search(blob):
        return True
    # gikai/kaigiroku 配下の PDF で、年度らしさがあれば会議録とみなす。
    if MINUTES_PATH_RE.search(url) and ERA_YEAR_RE.search(blob):
        return True
    return False


def looks_like_dated_minutes_html(url: str, text: str) -> bool:
    low = url.lower().split("?", 1)[0]
    if not (low.endswith(".html") or low.endswith(".htm") or low.endswith("/")):
        return False
    if not MINUTES_PATH_RE.search(url):
        return False
    blob = f"{text} {url}"
    if MINUTES_NEG_RE.search(blob):
        return False
    # 静的会議録ディレクトリは「会議録」語 + 年度 の両方を要求して誤検出を抑える。
    return bool(MINUTES_TEXT_RE.search(blob) and ERA_YEAR_RE.search(blob))


# 会議録そのものを名乗る語。「定例会」「第N回」は会期日程・一般質問の通告・
# 議会だよりにも付くので、会議録の入口や本文の判定には使わない。
MINUTES_STRONG_RE = re.compile(r"会議録|議事録|会議記録|委員会記録|審議録|kaigiroku|gijiroku|kaigi-roku", re.I)
# 会議録を名乗っていても、自治体の議会の会議の記録ではないもの。
CANDIDATE_NEG_RE = re.compile(
    r"だより|便り|広報|dayori|tayori|kouhou|koho|"
    r"子ども議会|こども議会|子供議会|中学生議会|高校生議会|女性議会|模擬議会|少年議会|ジュニア議会",
    re.I,
)
# 会議録の一覧に並ぶが会議の記録ではない PDF の題名。
ITEM_NEG_RE = re.compile(
    r"だより|便り|広報|dayori|日程|予定|順序|通告|要旨|項目|一覧|議案書|説明資料|結果|賛否|名簿|"
    r"提言|報告会|傍聴|請願|陳情|意見書|表紙|目次|予算書|決算書|招集告示|子ども議会|こども議会|中学生議会|模擬議会",
    re.I,
)
# 会議録のページに並ぶ PDF の題名が、会議（の日）を指しているか。
MEETING_LABEL_RE = re.compile(
    r"定例会|臨時会|委員会|本会議|会議|初日|最終日|\d+日目|第\d+日|\d{1,2}月\d{1,2}日|開会|閉会|審査|質疑|一般質問"
)
# 議会ではない合議体の会議録。自治体のサイトには議会と並んで置かれる
# （葛巻町「教育委員会定例会会議録」・月形町「農業委員会総会議事録」）。
NON_ASSEMBLY_RE = re.compile(
    r"農業委員会|教育委員会|選挙管理委員会|監査委員|固定資産評価審査|審議会|協議会|懇話会|懇談会|"
    r"検討委員会|策定委員会|推進委員会|評価委員会|総合教育会議|区長会|自治会|運営協議"
)
# 「議会運営委員会」「議会改革検討委員会」は議会の委員会。上の判定から外す。
ASSEMBLY_COMMITTEE_RE = re.compile(r"議会[^\s、・／/|｜]{0,8}委員会")
# 議会の会議であることを示す語。題名・一覧ページ・辿ってきた経路のどこかに要る。
ASSEMBLY_RE = re.compile(r"議会|gikai|定例会|臨時会|本会議|常任委員会|特別委員会|一般質問|council|assembly", re.I)
FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
# 会議録と判定するのに要る PDF の数。1〜2 件は議会の概要や子ども議会の記録でも起きる。
MIN_MINUTES_PDFS = 3


def is_non_assembly(text: str) -> bool:
    return bool(NON_ASSEMBLY_RE.search(ASSEMBLY_COMMITTEE_RE.sub("", str(text or ""))))


def is_minutes_pdf_item(label: str, page_title: str, context: str = "") -> bool:
    """会議録の PDF として数えてよいか。

    題名が会議録を名乗るか、会議録を名乗るページに会議の名前・日付で並んでいるか。
    会期日程・一般質問の通告・議決結果・議会だよりは、会議録のページに並んでいても数えない。
    村田町の「会期日程・各種行事」ページ（開催予定表・一般質問項目）を、題名の
    「定例会」だけで会議録と判定して取得対象にしていた。

    `context` は一覧ページへ辿ってきた経路（リンク元の頁題名・URL・リンク文字列）。
    題名にも一覧ページにも議会の語が無い取得元（湧別町「会議結果・会議録」）でも、
    議会のページから辿ってきたなら議会の会議録と分かる。
    """
    label = re.sub(r"\s+", " ", str(label or "")).strip().translate(FULLWIDTH_DIGITS)
    page_title = re.sub(r"\s+", " ", str(page_title or "")).strip()
    if label == "":
        return False
    if CANDIDATE_NEG_RE.search(label) or CANDIDATE_NEG_RE.search(page_title):
        return False
    if ITEM_NEG_RE.search(label):
        return False
    if is_non_assembly(label) or is_non_assembly(page_title):
        return False
    if not ASSEMBLY_RE.search(f"{label} {page_title} {context}"):
        return False
    if MINUTES_STRONG_RE.search(label):
        return True
    if not MINUTES_STRONG_RE.search(page_title):
        return False
    return bool(MEETING_LABEL_RE.search(label))


def is_minutes_entry_title(text: str) -> bool:
    """ページ題名やリンク文字列が、会議録の入口を指しているか。"""
    blob = re.sub(r"\s+", " ", str(text or ""))
    return bool(MINUTES_STRONG_RE.search(blob)) and not CANDIDATE_NEG_RE.search(blob) and not is_non_assembly(blob)


def page_heading(soup: BeautifulSoup) -> str:
    """<title> と最初の <h1>。CMS によって片方にしか頁の名前が無い。"""
    parts: list[str] = []
    if soup.title is not None:
        parts.append(soup.title.get_text(" ", strip=True))
    heading = soup.find("h1")
    if heading is not None:
        parts.append(heading.get_text(" ", strip=True))
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def meta_refresh_links(soup: BeautifulSoup, base_url: str) -> list[tuple[str, str]]:
    """<meta http-equiv="refresh"> の行き先。入口ページ（遠野市）は本体へこれで送る。"""
    out: list[tuple[str, str]] = []
    for meta in soup.find_all("meta"):
        if str(meta.get("http-equiv", "")).strip().lower() != "refresh":
            continue
        match = re.search(r"url\s*=\s*['\"]?([^'\";]+)", str(meta.get("content", "")), re.I)
        if match:
            out.append((urljoin(base_url, match.group(1).strip()), "refresh"))
    return out


class _PoliteSession:
    """スクレイパの巡回をそのまま使うときに、1 ページごとに間を空ける。"""

    def __init__(self, session: requests.Session, delay: float) -> None:
        self._session = session
        self._delay = max(float(delay or 0), 0.0)
        self._first = True

    def get(self, url, **kwargs):
        if not self._first and self._delay:
            time.sleep(self._delay)
        self._first = False
        kwargs.setdefault("allow_redirects", True)
        return self._session.get(url, **kwargs)

    def __getattr__(self, name):
        return getattr(self._session, name)


def _load_gikai_pdf():
    scrapers = SCRAPER_DIR / "scrapers"
    for path in (str(SCRAPER_DIR), str(scrapers)):
        if path not in sys.path:
            sys.path.append(path)
    import gikai_pdf  # noqa: WPS433  取得側と同じ巡回で確かめるため遅延 import

    return gikai_pdf


def probe_minutes_pdfs(session: requests.Session, start_url: str, timeout: float,
                       page_delay: float, max_pages: int = 12, max_depth: int = 2,
                       context: str = "") -> dict | None:
    """「独自」（gikai_pdf）が入口から実際に集める PDF を数え、会議録らしいものを判定する。

    探索とスクレイパで辿り方が違うと、探索では会議録に見えても取得では 0 件、
    あるいはその逆になる。取得側の巡回（`crawl_pdf_items`）をそのまま少ない
    ページ数で走らせ、集まった PDF のうち会議録と数えられるものを見る。
    取得側を読み込めない環境では None を返す（呼び出し側が簡易判定へ落とす）。
    """
    try:
        gikai_pdf = _load_gikai_pdf()
    except Exception:
        return None
    walk: dict = {}
    try:
        items = gikai_pdf.crawl_pdf_items(
            _PoliteSession(session, page_delay), start_url,
            timeout_ms=int(timeout * 1000), max_pages=max_pages, max_depth=max_depth, walk=walk,
        )
    except Exception:
        return {"items": 0, "minutes": 0, "pages": 0, "examples": []}
    route = f"{context} {start_url}"
    minutes = [item for item in items if is_minutes_pdf_item(item.title, item.page_title, route)]
    return {
        "items": len(items),
        "minutes": len(minutes),
        "pages": int(walk.get("visited_pages", 0) or 0),
        "examples": [item.title for item in minutes[:3]],
    }


def probe_own_site(session: requests.Session, start_url: str, home_host: str,
                   timeout: float, page_delay: float, max_probe_pages: int = 6,
                   context: str = "") -> dict | None:
    """会議録ページ起点で同一ホストを浅く辿り、PDF会議録 / 日付HTML一覧を判定する（簡易版）。

    戻り値: {system_type, candidate_url, evidence} または None。
    - 会議録と数えられる PDF が集まる → "独自"（gikai_pdf の汎用クロールが受ける）
    - 日付付き会議録HTMLのディレクトリ → "static-kaigiroku-dir"
    """
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(start_url, 0)])
    fetched = 0
    while queue and fetched < max_probe_pages:
        url, depth = queue.popleft()
        norm = url.split("#", 1)[0]
        if norm in visited:
            continue
        visited.add(norm)
        if fetched and page_delay:
            time.sleep(page_delay)
        soup, final = fetch_page(session, norm, timeout)
        fetched += 1
        if soup is None:
            continue

        title = page_heading(soup)
        if CANDIDATE_NEG_RE.search(title):
            continue
        links = iter_links(soup, final)
        pdf_hits = [
            (u, t) for (u, t) in links
            if u.lower().split("?", 1)[0].endswith(".pdf") and is_minutes_pdf_item(t, title, f"{context} {start_url} {final}")
        ]
        html_dated = [(u, t) for (u, t) in links if looks_like_dated_minutes_html(u, t)]

        if len(pdf_hits) >= MIN_MINUTES_PDFS:
            return {
                "system_type": "独自",
                "candidate_url": final,
                "evidence": f"pdf会議録 {len(pdf_hits)}件",
            }
        if len(html_dated) >= 5 and MINUTES_PATH_RE.search(final) and is_minutes_entry_title(title + " " + final):
            return {
                "system_type": "static-kaigiroku-dir",
                "candidate_url": final,
                "evidence": f"日付HTML {len(html_dated)}件",
            }

        if depth >= 2:
            continue
        # 会議録/議事録リンクを優先して次を辿る（同一ホストのみ）。
        nexts = []
        for u, t in links:
            host = urlsplit(u).netloc.lower()
            same = host == home_host or host.endswith("." + home_host)
            if not same:
                continue
            blob = f"{t} {u}"
            if CANDIDATE_NEG_RE.search(blob):
                continue
            if MINUTES_TEXT_RE.search(blob) or re.search(r"kaigiroku|gijiroku|giji|minutes", u, re.I):
                nexts.append((u, t))
        for u, t in nexts[:8]:
            nn = u.split("#", 1)[0]
            if nn not in visited:
                queue.append((u, depth + 1))
    return None


def load_homepage_index() -> dict[str, str]:
    index: dict[str, str] = {}
    with open(MUNI_DIR / "municipality_homepages.csv", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            code = (row.get("jis_code") or "").strip()
            url = (row.get("url") or "").strip()
            if code and url and code not in index:
                index[code] = url
    return index


def load_master_names() -> dict[str, str]:
    names: dict[str, str] = {}
    with open(MUNI_DIR / "municipality_master.tsv", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            code = (row.get("jis_code") or "").strip()
            if code:
                names[code] = (row.get("full_name") or row.get("name") or "").strip()
    return names


def load_unresolved_targets() -> list[str]:
    """URL 未特定（unresolved かつ url 空）の jis_code を返す。"""
    codes: list[str] = []
    with open(MUNI_DIR / "assembly_minutes_system_urls.tsv", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            status = (row.get("crawl_status") or "").strip()
            url = (row.get("url") or "").strip()
            system_type = (row.get("system_type") or "").strip()
            if status == "unresolved" and url == "" and system_type == "":
                code = (row.get("jis_code") or "").strip()
                if code:
                    codes.append(code)
    return codes


@dataclass
class Discovery:
    jis_code: str
    name: str
    homepage: str
    candidate_url: str = ""
    system_type: str = ""
    confidence: str = "none"   # high / medium / low / none
    evidence: str = ""
    pages_fetched: int = 0
    note: str = ""


def decode_html(raw: bytes, content_type: str = "", apparent_encoding: str | None = None) -> str:
    """HTML の本文を文字列にする。

    Content-Type に charset が無いと requests は ISO-8859-1 と見なす。そのまま
    読むと日本語のリンク文字列が化けて「会議録」「議会」のどの語にも当たらず、
    議会のページへ辿り着けない。2026-09 の点検では、登録簿に URL の無い自治体
    のかなりの数（勝浦市・美祢市・有田町・山県市など）がこれで止まっていた。
    宣言（ヘッダ → meta）を優先し、無ければ UTF-8、だめなら推定と CP932 の順に試す。
    """
    declared: list[str] = []
    match = re.search(r"charset=[\"']?([\w.:-]+)", content_type or "", re.I)
    if match:
        declared.append(match.group(1))
    head = raw[:4096]
    meta = re.search(rb"<meta[^>]+charset=[\"']?([\w.:-]+)", head, re.I)
    if meta:
        declared.append(meta.group(1).decode("ascii", "ignore"))
    # 西欧の文字集合を名乗る日本の自治体サイトは、実際には UTF-8 か CP932 である。
    declared = [d for d in declared if d.lower().replace("_", "-") not in ("iso-8859-1", "latin-1", "latin1", "us-ascii", "ascii")]
    for encoding in (*declared, "utf-8", apparent_encoding, "cp932"):
        if not encoding:
            continue
        name = encoding.lower()
        if name in ("shift_jis", "shift-jis", "sjis", "x-sjis"):
            encoding = "cp932"
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def fetch_page(session: requests.Session, url: str, timeout: float) -> tuple[BeautifulSoup | None, str]:
    """ページを開き、(soup, 最終 URL) を返す。リダイレクト先を相対リンクの基準にする。"""
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
    except requests.RequestException:
        return None, url
    if resp.status_code != 200:
        return None, url
    raw = resp.content
    ctype = resp.headers.get("Content-Type", "")
    if raw[:5] == b"%PDF-":
        return None, url
    if "html" not in ctype.lower() and b"<html" not in raw[:2000].lower():
        return None, url
    apparent = None
    if not re.search(r"charset=", ctype, re.I) and not re.search(rb"<meta[^>]+charset=", raw[:4096], re.I):
        try:
            apparent = resp.apparent_encoding
        except Exception:
            apparent = None
    text = decode_html(raw, ctype, apparent)
    try:
        soup = BeautifulSoup(text, "html.parser")
    except Exception:
        return None, url
    final_url = str(getattr(resp, "url", "") or url)
    base = soup.find("base", href=True)
    if base is not None:
        try:
            final_url = urljoin(final_url, str(base["href"]).strip())
        except Exception:
            pass
    return soup, final_url


def fetch(session: requests.Session, url: str, timeout: float) -> BeautifulSoup | None:
    soup, _final = fetch_page(session, url, timeout)
    return soup


def iter_links(soup: BeautifulSoup, base_url: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        text = re.sub(r"\s+", " ", a.get_text(" ", strip=True))[:80]
        try:
            absolute = urljoin(base_url, href)
        except Exception:
            continue
        if urlsplit(absolute).scheme not in ("http", "https"):
            continue
        out.append((absolute, text))
    return out


def link_priority(url: str, text: str, home_host: str) -> int:
    """探索順の優先度。大きいほど先に見る。"""
    host = urlsplit(url).netloc.lower()
    same_host = (host == home_host) or host.endswith("." + home_host) or home_host.endswith("." + host)
    score = 0
    blob = f"{text} {url}"
    # 既知ベンダのホストは最優先で確定候補になり得る。
    if any(v in host for v in VENDOR_HOSTS_QUICK):
        score += 100
    if MINUTES_HINT_RE.search(blob):
        score += 20
    if NEGATIVE_HINT_RE.search(blob):
        score -= 15
    # 会議録っぽいパス
    if re.search(r"gikai|giji|kaigiroku|kaigi|minutes|council", url, re.I):
        score += 8
    # 同ホストの市政/行政ハブは、会議録キーワードが無くても議会への通り道として辿る。
    if same_host and HUB_HINT_RE.search(blob):
        score += 6
    if same_host:
        # 同ホストは軽い基礎点を与え、手掛かりが弱くても議会セクションへ潜れるようにする。
        score += 2
    else:
        # 外部ホストは、ベンダでも会議録ヒントでもなければ広げない。
        if not any(v in host for v in VENDOR_HOSTS_QUICK) and not MINUTES_HINT_RE.search(blob):
            score -= 30
    return score


def _site_hosts(*urls: str) -> set[str]:
    hosts: set[str] = set()
    for url in urls:
        host = urlsplit(url).netloc.lower()
        if host:
            hosts.add(host)
    return hosts


MUNICIPAL_HOST_RE = re.compile(r"(?:^|\.)(city|town|vill|village|pref)\.([a-z0-9-]+)\.", re.I)


def is_same_site(url: str, hosts: set[str]) -> bool:
    """自治体のサイト（またはそのサブドメイン）か。

    旧ドメインと lg.jp が並ぶ自治体がある。砺波市はトップが city.tonami.toyama.jp、
    議会が www.city.tonami.lg.jp、会議録が info.city.tonami.lg.jp にある。
    `city.<名前>.` の組が同じなら同じ自治体とみなす。
    """
    host = urlsplit(url).netloc.lower()
    if host == "":
        return False
    match = MUNICIPAL_HOST_RE.search(host)
    for known in hosts:
        base = known[4:] if known.startswith("www.") else known
        if host == known or host == base or host.endswith("." + base):
            return True
        known_match = MUNICIPAL_HOST_RE.search(known)
        if match and known_match and match.groups() == known_match.groups():
            return True
    return False


ASSET_SUFFIXES = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip", ".doc", ".docx",
                  ".xls", ".xlsx", ".ppt", ".pptx", ".mp4", ".mp3", ".css", ".js")
NEWS_URL_RE = re.compile(r"news|topics|oshirase|whatsnew|/info/|/blog/|/event", re.I)
# 1 自治体で会議録の入口候補を確かめる数。候補ごとに取得側の巡回を走らせる。
MAX_PROBES = 2
# 入口ページ（くらし・行政／観光の振り分けだけのトップ）で辿る同ホストリンクの数。
ENTRANCE_FALLBACK_LINKS = 5


def discover_one(session: requests.Session, code: str, name: str, homepage: str,
                 max_pages: int, max_depth: int, timeout: float,
                 page_delay: float, deep_probe: bool = True) -> Discovery:
    result = Discovery(jis_code=code, name=name, homepage=homepage)
    home_host = urlsplit(homepage).netloc.lower()
    hosts = _site_hosts(homepage)

    visited: set[str] = set()
    # 会議録ヒントの強いリンクを先に開く。幅優先だと、トップから並ぶ組織・部署の
    # ページで上限を使い切り、議会の会議録ページまで届かなかった（士別市・粕屋町）。
    frontier: list[tuple[int, int, int, str]] = []
    order = itertools.count()
    heapq.heappush(frontier, (-1000, 0, next(order), homepage))
    best_local: Discovery | None = None  # 自ホスト会議録ページの低信頼候補
    # 会議録の入口候補: URL -> 点数（題名が会議録を名乗る頁 30、リンク文字列だけ 20）
    candidates: dict[str, int] = {}
    # 候補へ辿ってきた経路（リンク元の頁題名・URL・リンク文字列）。議会の会議録かの判断に使う。
    routes: dict[str, str] = {}
    probed: set[str] = set()
    probe_notes: list[str] = []
    video_only: list[str] = []

    def medium(url: str, system_type: str, evidence: str) -> Discovery:
        return Discovery(
            jis_code=code, name=name, homepage=homepage,
            candidate_url=url, system_type=system_type, confidence="medium",
            evidence=evidence[:200], note="要確認(自ホスト会議録)",
            pages_fetched=result.pages_fetched,
        )

    def probe(url: str) -> Discovery | None:
        probed.add(url)
        route = routes.get(url, "")
        found = probe_minutes_pdfs(session, url, timeout, page_delay, context=route)
        if found is None:
            # 取得側を読み込めない環境。簡易判定で代える。
            simple = probe_own_site(session, url, urlsplit(url).netloc.lower(), timeout, page_delay,
                                    context=route)
            if simple and simple["system_type"] == "独自":
                return medium(simple["candidate_url"], simple["system_type"], f"own-site: {simple['evidence']}")
            if simple:
                probe_notes.append(simple["evidence"])
            return None
        if found["minutes"] >= MIN_MINUTES_PDFS:
            examples = " / ".join(found["examples"])
            return medium(url, "独自", f"own-site: 会議録PDF {found['minutes']}件（{found['pages']}頁を確認）{examples}")
        probe_notes.append(f"PDF{found['items']}件中 会議録{found['minutes']}件")
        # 日付付き HTML の一覧は、年別の目次（勝浦市「令和8年会議録」）と
        # 見分けられないので取得対象にはしない。人が見るための手掛かりとして残す。
        return None

    def next_candidate(min_score: int) -> str | None:
        ranked = sorted(
            ((score, url) for url, score in candidates.items() if url not in probed and score >= min_score),
            key=lambda item: (-item[0], len(item[1])),
        )
        return ranked[0][1] if ranked else None

    while frontier and result.pages_fetched < max_pages:
        _neg, depth, _order, url = heapq.heappop(frontier)
        norm = url.split("#", 1)[0]
        if norm in visited:
            continue
        visited.add(norm)

        if result.pages_fetched and page_delay:
            time.sleep(page_delay)
        soup, final = fetch_page(session, norm, timeout)
        result.pages_fetched += 1
        if soup is None:
            continue
        final = final.split("#", 1)[0]
        visited.add(final)
        if depth == 0:
            # トップが別ドメインへ転送する自治体がある（砺波市 toyama.jp → lg.jp）。
            hosts |= _site_hosts(final)
            home_host = urlsplit(final).netloc.lower() or home_host

        links = meta_refresh_links(soup, final) + iter_links(soup, final)

        # このページ上のリンクからベンダを検出（fetch せずに確定できる）。
        for absolute, text in links:
            vendor = classify_vendor(absolute)
            if vendor and is_video_only_vendor(absolute, vendor):
                if absolute not in video_only:
                    video_only.append(absolute)
                continue
            if vendor:
                result.candidate_url = absolute
                result.system_type = vendor
                result.confidence = "high"
                result.evidence = f"link[{text}] -> {vendor}"
                return result

        # 現在ページ自体がベンダなら確定。
        vendor_self = classify_vendor(final)
        if vendor_self and not is_video_only_vendor(final, vendor_self):
            result.candidate_url = final
            result.system_type = vendor_self
            result.confidence = "high"
            result.evidence = f"page -> {vendor_self}"
            return result

        heading = page_heading(soup)
        if depth >= 1 and is_same_site(final, hosts) and is_minutes_entry_title(heading):
            # 「会議録を掲載しました」のお知らせ記事は、その回の分しか並ばない。
            # 常設の会議録ページ（湧別町 content=516）を先に確かめる。
            score = 25 if NEWS_URL_RE.search(final) else 30
            candidates[final] = max(candidates.get(final, 0), score)
            routes[final] = f"{routes.get(norm, '')} {heading} {final}"

        # 自ホストで会議録らしいページに来ていれば低信頼候補として控える。
        if best_local is None and depth >= 1 and MINUTES_HINT_RE.search(final + " " + heading):
            if (re.search(r"gikai|giji|kaigiroku|kaigi|minutes", final, re.I)
                    and not CANDIDATE_NEG_RE.search(final + " " + heading)):
                best_local = Discovery(
                    jis_code=code, name=name, homepage=homepage,
                    candidate_url=final, system_type="", confidence="low",
                    evidence="own-site minutes-like page", note="要人手判定(独自/PDF/静的)",
                )

        for absolute, text in links:
            path = urlsplit(absolute).path.lower()
            if path.endswith(ASSET_SUFFIXES) or not is_same_site(absolute, hosts):
                continue
            if is_minutes_entry_title(text):
                key = absolute.split("#", 1)[0]
                candidates.setdefault(key, 20)
            key = absolute.split("#", 1)[0]
            if key not in routes:
                routes[key] = f"{routes.get(norm, '')[-200:]} {heading} {final} {text}"

        # 題名が会議録を名乗る頁に着いたら、その場で確かめる。見つかれば残りを開かない。
        if deep_probe and len(probed) < MAX_PROBES:
            ready = next_candidate(30)
            if ready:
                found = probe(ready)
                if found:
                    return found

        if depth >= max_depth:
            continue
        # 会議録ヒント優先で次階層を積む。
        scored = [(link_priority(a, t, home_host), a, t) for a, t in links]
        scored.sort(key=lambda item: item[0], reverse=True)
        added = 0
        for pr, absolute, text in scored:
            # 会議録ヒント(>=20) / 市政ハブ(同ホスト>=8) / ベンダ(>=100) だけを辿る。
            # 手掛かりの無い同ホストリンク(+2)は広げない。
            if pr < 6:
                break
            nn = absolute.split("#", 1)[0]
            if nn in visited:
                continue
            if is_minutes_entry_title(text):
                pr += 40
            heapq.heappush(frontier, (-pr, depth + 1, next(order), nn))
            added += 1
            if added >= 10:
                break
        if depth == 0 and added < 2:
            # 入口ページ: 「くらし・行政」「公式サイト」だけが並ぶ。手掛かりの語が無くても
            # 同ホストの先を少し開く（指宿市 /main/・室戸市 top.php・宿毛市）。
            extra = 0
            for absolute, _text in links:
                nn = absolute.split("#", 1)[0]
                if nn in visited or urlsplit(nn).path.lower().endswith(ASSET_SUFFIXES):
                    continue
                if urlsplit(nn).netloc.lower() not in hosts:
                    continue
                heapq.heappush(frontier, (-5, depth + 1, next(order), nn))
                extra += 1
                if extra >= ENTRANCE_FALLBACK_LINKS:
                    break

    if deep_probe:
        while len(probed) < MAX_PROBES:
            ready = next_candidate(20)
            if ready is None:
                break
            found = probe(ready)
            if found:
                return found

    strong = sorted(candidates.items(), key=lambda item: (-item[1], len(item[0])))
    if strong:
        url, _score = strong[0]
        note = "会議録ページはあるが会議録PDFを数えられず"
        if probe_notes:
            note += "（" + "; ".join(probe_notes)[:120] + "）"
        return Discovery(
            jis_code=code, name=name, homepage=homepage,
            candidate_url=url, system_type="", confidence="low",
            evidence="own-site minutes page", note=note, pages_fetched=result.pages_fetched,
        )
    video_note = f"録画配信のみ見つかった: {video_only[0]}" if video_only else ""
    if best_local is not None:
        best_local.pages_fetched = result.pages_fetched
        if video_note:
            best_local.note = f"{best_local.note}; {video_note}"
        return best_local
    result.note = video_note or "会議録システムを特定できず"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codes", nargs="*", default=None, help="対象 jis_code を明示（省略時は未調査全件）")
    parser.add_argument("--limit", type=int, default=0, help="対象件数の上限（0=無制限）")
    parser.add_argument("--max-pages", type=int, default=18, help="1自治体あたりの最大取得ページ数")
    parser.add_argument("--max-depth", type=int, default=3, help="ホームページからの最大探索階層")
    parser.add_argument("--timeout", type=float, default=15.0, help="1リクエストのタイムアウト秒")
    parser.add_argument("--page-delay", type=float, default=0.7, help="ページ取得間の待機秒")
    parser.add_argument("--muni-delay", type=float, default=1.5, help="自治体間の待機秒")
    parser.add_argument("--save-out", default="", help="候補CSVの出力先（省略時 work/gijiroku/discovery_<ts>.csv）")
    args = parser.parse_args()

    homepages = load_homepage_index()
    names = load_master_names()

    if args.codes:
        codes = [c.strip() for c in args.codes if c.strip()]
    else:
        codes = load_unresolved_targets()
    codes = [c for c in codes if c in homepages]  # ホームページがある対象だけ
    if args.limit > 0:
        codes = codes[: args.limit]

    if not codes:
        print("対象がありません（ホームページURLのある未調査自治体が0件）。", file=sys.stderr)
        return 1

    out_path = Path(args.save_out) if args.save_out else (
        WORKSPACE_ROOT / "work" / "gijiroku" / f"discovery_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.8"})

    results: list[Discovery] = []
    high = medium = low = none = 0
    for i, code in enumerate(codes, 1):
        name = names.get(code, "")
        homepage = homepages[code]
        try:
            d = discover_one(session, code, name, homepage,
                             args.max_pages, args.max_depth, args.timeout, args.page_delay)
        except Exception as exc:  # 1件の失敗で全体を止めない
            d = Discovery(jis_code=code, name=name, homepage=homepage, note=f"error: {exc}")
        results.append(d)
        tag = {"high": "○", "medium": "◎", "low": "△", "none": "×"}.get(d.confidence, "×")
        if d.confidence == "high":
            high += 1
        elif d.confidence == "medium":
            medium += 1
        elif d.confidence == "low":
            low += 1
        else:
            none += 1
        print(f"[{i}/{len(codes)}] {tag} {code} {name}  "
              f"{d.system_type or '-':16s} {d.candidate_url or d.note}", flush=True)
        if args.muni_delay:
            time.sleep(args.muni_delay)

    with open(out_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["jis_code", "name", "homepage", "candidate_url",
                         "system_type", "confidence", "evidence", "pages_fetched", "note"])
        for d in results:
            writer.writerow([d.jis_code, d.name, d.homepage, d.candidate_url,
                             d.system_type, d.confidence, d.evidence, d.pages_fetched, d.note])

    print(f"\n完了: {len(results)}件  高信頼={high} 低信頼={low} 不明={none}")
    print(f"候補CSV: {out_path}")
    print("※ 反映は doc/assembly-minutes-url-survey.md の手順で確認してから。"
          "新規URLは robots 監査が済むまで自動取得されない。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
