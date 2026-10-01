#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""会議録システムの代表URLを未調査自治体について再探索する。

`assembly_minutes_system_urls.tsv` で `crawl_status=unresolved` かつ URL 空の自治体を対象に、
`municipality_homepages.csv` の公式ホームページを起点として `議会` / `会議録` / `議事録`
系リンクを辿り、既知の会議録システム（ベンダ）を URL の指紋で判定する。辿るのは
3 階層までで、会議録の手掛かりがあるリンクだけは 6 階層まで降りる。

方針:
- ここは「候補の発見」だけを行う。正本 TSV は書き換えない。候補は work/ の CSV へ出す。
- 既知ベンダ（kaigiroku.net / dbsr / gijiroku.com(voices) / kensakusystem / amivoice /
  discussvision / voicetechno など）は URL の host/パスから高信頼で分類できる。
- 自治体サイト内で会議録ページに辿り着いたがベンダが特定できない場合は、
  代表 URL 候補として低信頼で記録し、`system_type` は空（人手で 独自 / site-gikai-pdf /
  static-kaigiroku-dir を判断）にする。
- 自治体サイトの会議録は、取得側（gikai_pdf）と同じ巡回で PDF を数え、見本の PDF を
  1〜2 件開いて本文が議会の会議録かを確かめてから `独自` の中信頼にする。議決結果・日程・
  議会だより・委員会の報告・教育委員会などの議事録は会議録に数えない。
- 公式サイトのどこからもリンクされていないベンダのテナント（kensakusystem の長野の村など）は、
  自治体名のローマ字からテナントを推して開き、自治体名と公式サイトへのリンクで確かめる。
  同じローマ字の別の自治体のテナント（木祖村と木曽町の kiso）を掴まないため。
- 反映は運用者が doc/assembly-minutes-url-survey.md の手順で確認してから行う。
  取得の対象にするには、台帳の crawl_status を enabled にする。

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
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

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
    # 会議録の一覧を別の名前で呼ぶ中継ページ。八丈町「会議の記録」、北島町
    # 「会議結果の閲覧」、豊富町「議事結果等一覧」、浦河町「審議の結果」。
    # どれも会議録の語を含まない。
    r"会議の記録|会議記録|会議結果|議事結果|審議の結果|"
    r"gikai|giji|kaigiroku|minutes|council|assembly",
    re.I,
)
# ナビで頻出だが会議録本体でないものを軽く抑制する。
# 議会だより・名簿・政務活動費は議会の中にあるが、その先に会議録は無い。
NEGATIVE_HINT_RE = re.compile(
    r"選挙|傍聴|請願|中継|ライブ|youtube|議員report|なり手|だより|便り|議会報|名簿|政務活動",
    re.I,
)

# 会議録キーワードは無いが、そこを経由しないと議会へ辿り着けないハブ（市政ポータル等）。
# 東川町は議会を「町の紹介」の下に置いている。`about` は /aboutus/ の下の全ページに
# 当たって予算を食う（上松町）ので、ハブそのもの（/about/ で終わる URL）だけを見る。
HUB_HINT_RE = re.compile(
    r"市政|区政|町政|村政|県政|行政|組織|市の(組織|しくみ)|市役所|"
    r"(?:市|町|村|まち|むら)の(?:紹介|概要|情報|しくみ)|"
    r"/about(?:us)?/?(?:index\.html?)?$|gov(/|$)|shisei|soshiki",
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
# 「議事日程・本文」は議事日程から始まる会議録そのもの（勝浦市）。取得側
# （minutes_kind の SKIP_LABEL_RE）もこれは落とさないので、日程の語だけでは除かない。
ITEM_NEG_RE = re.compile(
    r"だより|便り|広報|dayori|日程(?![・･、\s]*(?:本文|会議録|議事録))|予定|順序|通告|要旨|項目|一覧|議案書|説明資料|"
    r"結果|賛否|名簿|"
    r"提言|報告会|傍聴|請願|陳情|意見書|表紙|目次|予算書|決算書|招集告示|子ども議会|こども議会|中学生議会|模擬議会",
    re.I,
)
# 会議録のページに並ぶ PDF の題名が、会議（の日）を指しているか。
# 会議録は 1 日分を「第1号」と数え、日付を「R7.12.15」と書く取得元がある（八丈町
# 「1号 R7.12.15」、室戸市「第１号（開会から閉会）」）。「本文」は勝浦市の「議事日程・本文」。
MEETING_LABEL_RE = re.compile(
    r"定例会|臨時会|委員会|本会議|会議|初日|最終日|\d+日目|第\d+日|\d{1,2}月\d{1,2}日|開会|閉会|審査|質疑|一般質問|"
    r"本文|^\s*第?\s*\d{1,2}\s*号|(?:令和|平成|[RH])\s*\d{1,2}\s*[.．]\s*\d{1,2}\s*[.．]\s*\d{1,2}",
    re.I,
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


# ファイル名が会議録を名乗る PDF。一覧ページの題名が「令和8年9月定例会」でも、
# 置かれている PDF が gijiroku_teireikai2026.pdf なら会議録である（和束町）。
# 日本語のファイル名（北島町「会議録HP用 R8-2.pdf」）は URL の中で %E4%BC%9A… に
# なっているので、戻してから見る。
MINUTES_FILE_RE = re.compile(r"kaigiroku|gijiroku|kaigi[-_]?roku|gijirok|minutes|会議録|議事録", re.I)


# 会議録の棚に置かれていても会議の記録ではないファイル名。
MINUTES_FILE_NEG_RE = re.compile(
    r"dayori|tayori|kouhou|koho|nittei|meibo|kekka|sanpi|junjo|junnjo|tsuukoku|tuukoku|"
    r"youshi|yoshi|gian|shiryo|siryo|ichiran|seigan|chinjo|"
    r"だより|便り|日程|結果|名簿|通告|一覧|目次",
    re.I,
)


def file_name_names_minutes(url: str) -> bool:
    path = unquote(urlsplit(str(url or "")).path)
    if MINUTES_FILE_NEG_RE.search(path):
        return False
    return bool(MINUTES_FILE_RE.search(path))


# 「会議記録」を名乗っていても、中身が議決結果や日程だけの頁がある（剣淵町
# 「会議記録 | 議決結果」）。会議録・議事録の語が無く、結果・日程を名乗る題名は、
# 会議録の頁として扱わない。
RESULTS_TITLE_RE = re.compile(r"議決結果|審議結果|採決結果|会議結果|結果一覧|日程|予定")
PLAIN_MINUTES_RE = re.compile(r"会議録|議事録|kaigiroku|gijiroku", re.I)


def title_names_minutes(text: str) -> bool:
    blob = str(text or "")
    if not MINUTES_STRONG_RE.search(blob):
        return False
    return bool(PLAIN_MINUTES_RE.search(blob)) or not RESULTS_TITLE_RE.search(blob)


# ファイルの種類と大きさだけの名前（「[248KB pdf]」「(PDF：1.2MB)」）。
FILE_NOTE_RE = re.compile(
    r"pdf|ｐｄｆ|ファイル|形式|サイズ|kbyte|mbyte|kb|mb|キロバイト|メガバイト|バイト|"
    r"[0-9０-９.,，:：/／|｜\s\[\]()（）［］【】<>＜＞]",
    re.I,
)


def is_file_note_only(label: str) -> bool:
    text = str(label or "").strip()
    return text != "" and FILE_NOTE_RE.sub("", text) == "" and re.search(r"pdf|ｐｄｆ|kb|mb|バイト", text, re.I) is not None


def pdf_item_kind(label: str, page_title: str, context: str = "", url: str = "",
                  page_url: str = "") -> str:
    """PDF の題名と置き場から、会議録かを 3 段で返す。

    - "minutes": 題名・ファイル名・一覧ページのどれかが会議録を名乗る
    - "meeting": 議会の会議の名前・日付で並ぶが、どこも会議録を名乗らない
      （白川町「定例会・臨時会」の「第1回定例会（第1日）」）。議決結果や日程の
      PDF も同じ形で並ぶので、本文を見るまで会議録とは決めない
    - "": 会議録ではない
    """
    label = re.sub(r"\s+", " ", str(label or "")).strip().translate(FULLWIDTH_DIGITS)
    page_title = re.sub(r"\s+", " ", str(page_title or "")).strip()
    if is_file_note_only(label):
        # 名前がファイルの注記だけ（三沢市「[248KB pdf]」）。取得側と同じく頁の名前で呼ぶ。
        label = page_title.translate(FULLWIDTH_DIGITS)
    if label == "":
        return ""
    if CANDIDATE_NEG_RE.search(label) or CANDIDATE_NEG_RE.search(page_title):
        return ""
    if ITEM_NEG_RE.search(label):
        return ""
    if is_non_assembly(label) or is_non_assembly(page_title):
        return ""
    if not ASSEMBLY_RE.search(f"{label} {page_title} {context} {url}"):
        return ""
    if MINUTES_STRONG_RE.search(label):
        return "minutes"
    if file_name_names_minutes(url):
        # ファイル名が会議録を名乗る。一覧ページの題名は当てにしない。
        return "minutes"
    if not MEETING_LABEL_RE.search(label):
        return ""
    # 一覧ページが会議録を名乗るか。題名を持たない頁（昭和村）や、題名が自治体名
    # だけの頁（勝浦町の年別一覧）があるので、頁の URL の名前も見る。
    if title_names_minutes(page_title) or file_name_names_minutes(page_url):
        return "minutes"
    if SECTION_NEG_RE.search(page_title):
        # 「議事日程・結果」「議決結果」と名乗る頁・節に並ぶ会議の PDF は、会議録の候補にもしない。
        return ""
    return "meeting"


def is_minutes_pdf_item(label: str, page_title: str, context: str = "", url: str = "",
                        page_url: str = "") -> bool:
    """会議録の PDF として数えてよいか。

    題名が会議録を名乗るか、会議録を名乗るページに会議の名前・日付で並んでいるか。
    会期日程・一般質問の通告・議決結果・議会だよりは、会議録のページに並んでいても数えない。
    村田町の「会期日程・各種行事」ページ（開催予定表・一般質問項目）を、題名の
    「定例会」だけで会議録と判定して取得対象にしていた。

    `context` は一覧ページへ辿ってきた経路（リンク元の頁題名・URL・リンク文字列）。
    題名にも一覧ページにも議会の語が無い取得元（湧別町「会議結果・会議録」）でも、
    議会のページから辿ってきたなら議会の会議録と分かる。
    """
    return pdf_item_kind(label, page_title, context, url, page_url) == "minutes"


def is_minutes_entry_title(text: str) -> bool:
    """ページ題名やリンク文字列が、会議録の入口を指しているか。"""
    blob = re.sub(r"\s+", " ", str(text or ""))
    return title_names_minutes(blob) and not CANDIDATE_NEG_RE.search(blob) and not is_non_assembly(blob)


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


# スプラッシュ型のトップが JavaScript で本体へ送る書き方。
SCRIPT_REDIRECT_RE = re.compile(
    r"location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]|location\.replace\(\s*['\"]([^'\"]+)['\"]",
    re.I,
)


def script_redirect_links(soup: BeautifulSoup, base_url: str) -> list[tuple[str, str]]:
    """インラインの script が送る先。入口ページだけで使う（本文中のスクリプトは追わない）。"""
    out: list[tuple[str, str]] = []
    for script in soup.find_all("script"):
        if script.get("src"):
            continue
        for match in SCRIPT_REDIRECT_RE.finditer(script.get_text() or ""):
            target = (match.group(1) or match.group(2) or "").strip()
            if not target or target.startswith(("#", "javascript:")):
                continue
            absolute = urljoin(base_url, target)
            if urlsplit(absolute).scheme in ("http", "https"):
                out.append((absolute, "redirect"))
    return out[:3]


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
                       context: str = "", start_title: str = "") -> dict | None:
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
        return {"items": 0, "minutes": 0, "meetings": 0, "pages": 0, "examples": [], "samples": []}
    route = f"{context} {start_url}"
    # 取得側は頁の名前を h1 から採る。CMS によっては h1 が自治体名で、頁の名前は
    # <title> にしかない（仁淀川町の「令和８年 仁淀川町議会 会議録｜仁淀川町」）。
    # 入口の頁だけは探索側が読んだ題名（<title> と h1）を使う。
    start_key = start_url.split("#", 1)[0]
    def page_name(item) -> str:
        on_start = item.page_url.split("#", 1)[0] == start_key
        if start_title and (on_start or not item.page_title.strip()):
            # 題名を持たない頁は、辿ってきた入口の題名を引き継ぐ。取得側の巡回は
            # 会議録らしい頁しか辿らないので、入口の性格がそのまま当てはまる。
            return start_title
        if start_title and is_year_only_title(item.page_title):
            # 年だけの頁（吉富町「令和7年1月～」・鮫川村「令和7年」）は入口の年別の棚。
            return f"{item.page_title} {start_title}"
        return item.page_title

    minutes: list = []
    meetings: list = []
    for item in items:
        kind = pdf_item_kind(item.title, page_name(item), route, item.url, item.page_url or start_url)
        if kind == "minutes":
            minutes.append(item)
        elif kind == "meeting":
            meetings.append(item)
    # 例と本文の見本は、会議の名前で呼ばれた PDF を先にする。会議録の棚に議会だより
    # （「第210号（令和８年８月１日）」）が混じっていても、そちらを見本にしない。
    ranked = sorted(minutes, key=lambda item: -sample_rank(item.title)) + \
        sorted(meetings, key=lambda item: -sample_rank(item.title))
    return {
        "items": len(items),
        "minutes": len(minutes),
        "meetings": len(meetings),
        "pages": int(walk.get("visited_pages", 0) or 0),
        "examples": [item.title for item in ranked[:3]],
        "samples": [(item.title, item.url) for item in ranked[:MAX_BODY_SAMPLES]],
    }


# 会議そのものを指す題名（本文の見本に向く）。
MEETING_NAME_RE = re.compile(r"会議録|議事録|定例会|臨時会|委員会|本会議|日目|第\s*\d+\s*日|本文|開会")


def sample_rank(title: str) -> int:
    return 1 if MEETING_NAME_RE.search(str(title or "").translate(FULLWIDTH_DIGITS)) else 0


# 年だけの頁の題名。「令和7年」「2026年(令和8年)」「令和7年1月～」。
YEAR_ONLY_TITLE_RE = re.compile(
    r"^\s*(?:(?:令和|平成|昭和)\s*(?:\d{1,2}|元)\s*年(?:度)?|(?:19|20)\d{2}\s*年(?:度)?)"
    r"\s*(?:[（(][^）)]*[）)])?\s*(?:\d{1,2}\s*月\s*[～~〜ー－\-]?\s*(?:\d{1,2}\s*月)?)?\s*$"
)
# 年で始まるリンク。会議録の棚から年別の頁へ降りるときの形
# （藤崎町「令和7年 定例会・臨時会」、室戸市「２０２５(令和７)年 会議録」）。
YEAR_LEAD_RE = re.compile(r"^\s*(?:(?:令和|平成|昭和)\s*(?:\d{1,2}|元)|(?:19|20)\d{2})\s*(?:[（(][^）)]*[）)])?\s*年")


def is_year_only_title(title: str) -> bool:
    text = str(title or "").translate(FULLWIDTH_DIGITS)
    # <title> は「令和7年 | 鮫川村公式ホームページ」の形。先頭の区切りまでを見る。
    head = re.split(r"\s*[|｜]\s*|\s+[-–—]\s+", text, maxsplit=1)[0]
    return bool(YEAR_ONLY_TITLE_RE.match(head))


def is_year_link(text: str) -> bool:
    return bool(YEAR_LEAD_RE.match(str(text or "").translate(FULLWIDTH_DIGITS)))


def scraper_follows_from_entry(text: str, url: str) -> bool:
    """取得側（gikai_pdf）が入口の頁からこのリンクを辿るか。

    年の棚の見本は、取得側が辿れる年のリンクだけで取る。月形町の「令和7年 会議結果」の
    ように取得側が辿らない年のリンクで数えると、探索では会議録が見えても取得では 0 件になる。
    """
    try:
        gikai_pdf = _load_gikai_pdf()
    except Exception:
        return False
    label = gikai_pdf.clean_label(text)
    return bool(gikai_pdf.looks_like_generic_minutes_page(text, url) or gikai_pdf.YEAR_ONLY_LINK.match(label))


def year_link_name(text: str) -> str:
    """年のリンクから年を除いた名前。「令和7年 定例会・臨時会」なら「定例会・臨時会」。

    「2026年(令和8年)」のように年を 2 通り書くものは、名前が空になる。
    """
    return re.sub(r"[0-9０-９]+|令和|平成|昭和|元|年度|年|[()（）\s]", "", str(text or ""))


# 1 回の会議を指す「第2回」「9月定例会」。年の棚と 1 回の会議の頁を分ける。
ORDINAL_RE = re.compile(
    r"第\s*[0-9０-９一二三四五六七八九十]+\s*回|[0-9０-９]{1,2}\s*月\s*(?:定例|臨時|会議)"
)


def is_year_shelf_title(title: str) -> bool:
    """年の棚の頁か（「令和7年 定例会・臨時会」「2026年(令和8年)」）。1 回の会議の頁は除く。"""
    head = re.split(r"\s*[|｜]\s*|\s+[-–—]\s+", str(title or ""), maxsplit=1)[0]
    return is_year_link(head) and not ORDINAL_RE.search(head)


# 本文を確かめる PDF の数。1 件目が会議録なら 2 件目は開かない。
MAX_BODY_SAMPLES = 2
# 本文の確認で読む PDF の大きさの上限。会議録 1 日分は数 MB に収まる。
MAX_SAMPLE_BYTES = 15 * 1024 * 1024


def _load_minutes_kind():
    for path in (str(SCRAPER_DIR), str(SCRAPER_DIR / "scrapers")):
        if path not in sys.path:
            sys.path.append(path)
    import minutes_kind  # noqa: WPS433
    from kami_city_pdf import extract_pdf_text  # noqa: WPS433

    return minutes_kind, extract_pdf_text


# 会議録と認める発言者の行の数。
MIN_SPEAKER_LINES = 3
# 会議が開かれた記録にしか出てこない語（会議録の冒頭の出席者・署名・議事の進行）。
HELD_MEETING_MARKERS = (
    "出席議員", "欠席議員", "会議録署名", "開議", "散会", "これより会議を開", "会議に付した事件",
    "出席委員", "欠席委員", "説明のため出席", "説明員",
)


def judge_minutes_body(title: str, text: str, url: str = "") -> tuple[bool | None, str]:
    """PDF の本文が議会の会議録か。(判定, 理由)。判定できない本文は None。

    剣淵町の「会議記録」は議決結果だった。題名や頁の名前が会議録らしくても、
    本文が日程・議決結果・議会だより・農業委員会の議事録なら会議録ではない。
    取得側が採用直前に使う判定（minutes_kind.non_minutes_reason）と同じものを使い、
    そのうえで発言者の行か会議の進行の語があることを求める。
    """
    try:
        minutes_kind, _extract = _load_minutes_kind()
    except Exception:
        return None, "本文の判定を読み込めない"
    body = str(text or "").strip()
    if len(body) < 50:
        return None, "本文の文字を読めない"
    reason = minutes_kind.non_minutes_reason(title, body, url=url)
    if reason:
        return False, reason
    # 本文が名乗る会議の名前。開成町の「会議結果・会議要旨」の議事録は教育委員会の
    # 会議録で、議会の頁から辿れても議会の会議録ではない。
    meeting_title = minutes_kind.extract_meeting_title_from_text(body) or body[:80]
    if is_non_assembly(meeting_title) and "議会" not in meeting_title:
        return False, "議会以外の会議の会議録"
    # 「委員長」「開会」は委員会の報告書や日程にも出る（大島町の特別委員会報告）。
    # 会議が開かれた記録にしか出ない語か、発言者の行を求める。
    squeezed = re.sub(r"[\s\u3000]+", "", body[:8000])
    held = [marker for marker in HELD_MEETING_MARKERS if marker in squeezed]
    # 発言者の行は 1 行だけなら名簿の「議長（オブザーバー） 坂上長一」でも当たる（大島町）。
    speakers = sum(
        1 for line in body.splitlines()
        if minutes_kind.SPEAKER_LINE_RE.search(minutes_kind.normalize_space(line))
    )
    if len(held) >= 2 or speakers >= MIN_SPEAKER_LINES:
        return True, "本文に会議の記録の語・発言者"
    # 要旨の形の会議録（北島町「会議録 北島町議会」: 議案の概要と結果、一般質問の質問と答弁）。
    # 冒頭が会議録を名乗り、質問と答弁が並ぶものは会議録として扱う。
    if PLAIN_MINUTES_RE.search(squeezed[:300]) and "質問" in squeezed and "答弁" in squeezed:
        return True, "本文が要旨の形の会議録（質問と答弁）"
    return False, "本文に会議の記録の語が無い"


def _read_limited(resp, limit: int) -> bytes:
    """応答の本文を上限まで読む。大きすぎる PDF は最後まで落とさない。"""
    iter_content = getattr(resp, "iter_content", None)
    if iter_content is None:
        return (getattr(resp, "content", b"") or b"")[: limit + 1]
    chunks: list[bytes] = []
    size = 0
    try:
        for chunk in iter_content(65536):
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                break
    finally:
        close = getattr(resp, "close", None)
        if close is not None:
            close()
    return b"".join(chunks)


def check_minutes_body(session: requests.Session, samples: list[tuple[str, str]],
                       timeout: float, page_delay: float) -> tuple[bool | None, str]:
    """見本の PDF を開いて本文を確かめる。(会議録か, 根拠)。読めなければ None。"""
    try:
        _minutes_kind, extract_pdf_text = _load_minutes_kind()
    except Exception:
        return None, "本文の判定を読み込めない"
    verdicts: list[tuple[bool | None, str]] = []
    for title, url in samples[:MAX_BODY_SAMPLES]:
        if page_delay:
            time.sleep(page_delay)
        try:
            resp = session.get(url, timeout=max(timeout, 30.0), stream=True)
            raw = _read_limited(resp, MAX_SAMPLE_BYTES)
        except requests.RequestException:
            continue
        if resp.status_code != 200 or raw[:5] != b"%PDF-" or len(raw) > MAX_SAMPLE_BYTES:
            continue
        try:
            text = extract_pdf_text(raw)
        except Exception:
            continue
        verdict, reason = judge_minutes_body(title, text, url)
        name = re.sub(r"\s+", " ", str(title or ""))[:30]
        if verdict:
            return True, f"本文確認: {name}"
        verdicts.append((verdict, f"{name}: {reason}"))
    negatives = [note for verdict, note in verdicts if verdict is False]
    if negatives:
        return False, "本文が会議録でない（" + "; ".join(negatives)[:120] + "）"
    return None, "本文を読めない"


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
            if u.lower().split("?", 1)[0].endswith(".pdf") and is_minutes_pdf_item(t, title, f"{context} {start_url} {final}", u, final)
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


_MASTER_ROWS: dict[str, dict[str, str]] | None = None


def master_rows() -> dict[str, dict[str, str]]:
    """自治体マスタ（名前・都道府県・ローマ字）。テナントの推測と名前の照合に使う。"""
    global _MASTER_ROWS
    if _MASTER_ROWS is None:
        rows: dict[str, dict[str, str]] = {}
        try:
            with open(MUNI_DIR / "municipality_master.tsv", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle, delimiter="\t"):
                    code = (row.get("jis_code") or "").strip()
                    if code:
                        rows[code] = {str(k): str(v or "").strip() for k, v in row.items() if k}
        except OSError:
            rows = {}
        _MASTER_ROWS = rows
    return _MASTER_ROWS


def short_municipal_name(code: str, name: str = "") -> str:
    """「上松町」のような自治体名。マスタに無ければ渡された名前の末尾を使う。"""
    row = master_rows().get(str(code), {})
    if row.get("name"):
        return row["name"]
    parts = str(name or "").split()
    return parts[-1] if parts else ""


def municipal_name_is_unique(code: str) -> bool:
    """同じ名前の市町村が他に無いか（池田町・美郷町は全国に複数ある）。"""
    rows = master_rows()
    own = rows.get(str(code), {}).get("name", "")
    if own == "":
        return False
    return sum(1 for row in rows.values() if row.get("name") == own) == 1


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


def homepage_variants(url: str) -> list[str]:
    """開けなかったトップの言い換え。www の有無と http/https を試す。

    登録簿のホームページが古い自治体がある（吉岡町 www.town.yoshioka.gunma.jp は
    名前が引けず、town.yoshioka.gunma.jp が現行の www.town.yoshioka.lg.jp へ送る）。
    """
    parts = urlsplit(url)
    host = parts.netloc
    hosts = [host[4:]] if host.startswith("www.") else [f"www.{host}"]
    out: list[str] = []
    for scheme in (parts.scheme or "https", "http"):
        for candidate in hosts:
            variant = urlunsplit((scheme, candidate, parts.path or "/", parts.query, ""))
            if variant != url and variant not in out:
                out.append(variant)
    return out


def describe_fetch_failure(session: requests.Session, url: str, timeout: float) -> str:
    """トップが開けない理由を短く書く。人が登録簿を直すときの手掛かりにする。"""
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
    except requests.exceptions.SSLError:
        return "証明書を検証できない"
    except requests.exceptions.ConnectionError:
        return "ホスト名を引けない/接続できない"
    except requests.exceptions.Timeout:
        return "応答がない"
    except requests.RequestException as error:
        return f"取得できない({type(error).__name__})"
    if resp.status_code != 200:
        return f"HTTP {resp.status_code}"
    return "HTML として読めない"


def fetch(session: requests.Session, url: str, timeout: float) -> BeautifulSoup | None:
    soup, _final = fetch_page(session, url, timeout)
    return soup


def anchor_label(element) -> str:
    """リンクの名前。文字が無ければ画像の alt、title、aria-label の順に読む。

    議会へのリンクが画像だけのサイトがある（粕屋町 `<img alt="粕屋町議会">`）。
    文字列だけを見ると名前が空になり、議会のリンクだと分からなかった。
    `<area>`（イメージマップ）は alt が名前である。
    """
    if element.name == "area":
        text = str(element.get("alt", "") or element.get("title", "") or "")
    else:
        text = element.get_text(" ", strip=True)
        if not text:
            alts = [str(img.get("alt", "") or "").strip() for img in element.find_all("img")]
            text = " ".join(alt for alt in alts if alt)
        if not text:
            text = str(element.get("title", "") or element.get("aria-label", "") or "")
    return re.sub(r"\s+", " ", text).strip()[:80]


def iter_links(soup: BeautifulSoup, base_url: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    base_host = urlsplit(base_url).netloc.lower()
    for a in soup.find_all(["a", "area"], href=True):
        href = str(a["href"]).strip()
        if not href or href.lower().startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        text = anchor_label(a)
        try:
            absolute = urljoin(base_url, href)
        except Exception:
            continue
        if urlsplit(absolute).scheme not in ("http", "https"):
            continue
        out.append((absolute, text))
    # フレームで組んだトップ（frameset）はリンクが 1 本も無く、中身が frame の中にある。
    # iframe は地図・動画・計測の埋め込みが多いので、同じホストのものだけを辿る。
    for frame in soup.find_all(["frame", "iframe"], src=True):
        src = str(frame["src"]).strip()
        if not src or src.lower().startswith(("javascript:", "about:", "data:")):
            continue
        try:
            absolute = urljoin(base_url, src)
        except Exception:
            continue
        if urlsplit(absolute).scheme not in ("http", "https"):
            continue
        if frame.name == "iframe" and urlsplit(absolute).netloc.lower() != base_host:
            continue
        out.append((absolute, str(frame.get("title", "") or frame.get("name", "") or "")[:80]))
    return out


# 見出し（h2〜h4）。会議録の PDF は「議会」の頁の中の「会議記録」「議事録」の節に
# 並ぶことがある（東川町・豊富町）。頁の題名では会議録と分からない。
SECTION_TAGS = ("h2", "h3", "h4")
# 会議録でない PDF が並ぶ節の見出し。豊富町は「議事録」の節の隣に「議事日程・結果」
# 「議員の出欠状況」の節があり、そこにも「第1回定例会」の PDF が並ぶ。
SECTION_NEG_RE = re.compile(
    r"日程|議決結果|審議結果|採決結果|結果一覧|賛否|だより|便り|広報|議会報|意見書|決議|請願|陳情|名簿|出欠|通告|予定|議案"
)


def pdf_links_with_sections(soup: BeautifulSoup, base_url: str) -> list[tuple[str, str, str]]:
    """頁の PDF リンクを (URL, 名前, 直前の見出し) で返す。"""
    out: list[tuple[str, str, str]] = []
    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        if not href or href.lower().startswith(("#", "javascript:", "mailto:")):
            continue
        try:
            absolute = urljoin(base_url, href)
        except Exception:
            continue
        if not absolute.lower().split("?", 1)[0].endswith(".pdf"):
            continue
        heading = a.find_previous(list(SECTION_TAGS))
        section = re.sub(r"\s+", " ", heading.get_text(" ", strip=True))[:60] if heading is not None else ""
        out.append((absolute, anchor_label(a), section))
    return out


def section_page_title(section: str, page_title: str) -> str:
    """PDF の置き場の名前。節の見出しが会議録か会議録以外を名乗るなら、それを使う。"""
    if section and (is_minutes_entry_title(section) or SECTION_NEG_RE.search(section)):
        return section
    return page_title


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
    if CANDIDATE_NEG_RE.search(text):
        # 議会だより・子ども議会は「議会」の語があっても、その先に会議録は無い。
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


ASSEMBLY_HOST_RE = re.compile(r"gikai|gicho|council|assembly", re.I)


def municipal_token(host: str) -> str:
    """ホスト名に出る自治体の名前。city.nikaho.akita.jp なら nikaho。"""
    match = MUNICIPAL_HOST_RE.search(host.lower())
    if match:
        return match.group(2)
    labels = [label for label in host.lower().split(".") if label not in ("www", "jp", "lg", "ne", "or", "co", "com", "net", "go")]
    return labels[0] if labels else ""


def is_assembly_site(url: str, hosts: set[str]) -> bool:
    """議会だけ別のホストに置いている自治体か（にかほ市 nikahoshigikai.akita.jp）。

    同じ自治体の名前を含み、かつホスト名が議会を名乗るときだけ通す。
    """
    host = urlsplit(url).netloc.lower()
    if host == "" or not ASSEMBLY_HOST_RE.search(host):
        return False
    tokens = {municipal_token(known) for known in hosts}
    tokens.discard("")
    return any(token and token in host for token in tokens)


# 議会・会議録のリンクの行き先でも、自治体のサイトとして扱わないホスト。
PLATFORM_HOST_RE = re.compile(
    r"youtube|youtu\.be|facebook|twitter|(?:^|\.)x\.com$|instagram|line\.me|google|yahoo|"
    r"wikipedia|note\.com|ameblo|translate\.goog",
    re.I,
)
ABSOLUTE_URL_RE = re.compile(r"https?://[^\s\"'<>()\\]+", re.I)


def _host_base(host: str) -> str:
    return re.sub(r"^www\d*\.", "", str(host or "").lower())


def same_registered_site(url: str, hosts: set[str]) -> bool:
    """ホスト名そのもの（www の有無を除く）かその下か。`is_same_site` より狭い。

    `is_same_site` は `town.<名前>.` が同じなら同じ自治体とみなすので、同じ名前の
    別の町（朝日町: town.asahi.yamagata.jp と town.asahi.mie.jp）も通してしまう。
    """
    host = _host_base(urlsplit(str(url or "")).netloc)
    if host == "":
        return False
    return any(host == base or host.endswith("." + base)
               for base in (_host_base(known) for known in hosts) if base)


def links_back_to_site(text: str, hosts: set[str], strict: bool = False) -> bool:
    """頁（スクリプトを含む）が、自治体の公式サイトへのリンクを持っているか。

    同じ名前の自治体が他にあるときは `strict` で、公式サイトのホストそのものへの
    リンクだけを数える。
    """
    check = same_registered_site if strict else is_same_site
    return any(check(url, hosts) for url in ABSOLUTE_URL_RE.findall(str(text or "")))


def trusts_external_page(url: str, heading: str, link_text: str, short_name: str,
                         page_text: str, hosts: set[str], code: str) -> bool:
    """公式サイトから辿った別ホストの頁を、その自治体のサイトとして扱ってよいか。

    議会が独自のドメインに置かれる自治体がある。公式サイトから議会・会議録の
    リンクで来て、頁の題名が自治体の名前を名乗るときだけ通す。同じ名前の
    自治体が他にあるなら、公式サイトへのリンクがあることも求める。
    """
    host = urlsplit(url).netloc.lower()
    if host == "" or short_name == "" or PLATFORM_HOST_RE.search(host):
        return False
    if not MINUTES_HINT_RE.search(link_text or "") or NEGATIVE_HINT_RE.search(link_text or ""):
        return False
    if short_name not in str(heading or ""):
        return False
    # 議会の頁であること。「会議録」のリンクの先が協議会のサイトのことがある
    # （つるぎ町から辿った美馬市・つるぎ町の自立支援協議会）。
    names_assembly = "議会" in f"{link_text} {heading}" or bool(ASSEMBLY_HOST_RE.search(url))
    if is_non_assembly(heading) or not names_assembly:
        return False
    return municipal_name_is_unique(code) or links_back_to_site(page_text, hosts, strict=True)


# 公式サイトからリンクされていないことがあるベンダのテナント。
# (system_type, 確かめに開く URL, 登録する URL)。kaigiroku.net の頁は名前を
# スクリプトで差し込むので、自治体名と公式サイトへのリンクが入った message.js を見る。
TENANT_GUESSES = (
    ("kensakusystem", "https://www.kensakusystem.jp/{t}/index.html",
     "https://www.kensakusystem.jp/{t}/index.html"),
    ("kaigiroku.net", "https://ssp.kaigiroku.net/tenant/{t}/js/message.js",
     "https://ssp.kaigiroku.net/tenant/{t}/SpMinuteSearch.html"),
)
# 1 自治体で試すテナント名の数。1 つにつきベンダごとに 1 回開く。
MAX_TENANT_TOKENS = 2
ROMAJI_TYPE_SUFFIXES = ("-shi", "-ku", "-cho", "-machi", "-mura", "-son")


def prefecture_romaji() -> set[str]:
    out: set[str] = set()
    for row in master_rows().values():
        if row.get("entity_type") == "prefecture":
            token = re.sub(r"-(?:ken|fu|to|do)$", "", row.get("name_romaji", "").lower())
            if token:
                out.add(token)
    return out


def tenant_tokens(code: str) -> list[str]:
    """テナント名の候補。マスタのローマ字から市町村の種別を外したもの。

    マスタのローマ字にはホスト名から採った形が混じる（minamimakimura-mura、
    town-kyogoku-cho、hokkaido-kamikawa-cho）。種別の重なり・town/vill の前置き・
    都道府県名の前置きを外す。テナントは別の自治体のことがあるので、開いて確かめる。
    """
    row = master_rows().get(str(code), {})
    if row.get("entity_type") != "municipality":
        return []
    romaji = row.get("name_romaji", "").lower()
    base = romaji
    kind = ""
    for suffix in ROMAJI_TYPE_SUFFIXES:
        if base.endswith(suffix):
            base, kind = base[: -len(suffix)], suffix[1:]
            break
    head, _sep, rest = base.partition("-")
    if rest and head in prefecture_romaji():
        base = rest
    base = re.sub(r"^(?:town|vill|city)-?", "", base)
    base = re.sub(r"(?:-?town|-v|-vill)$", "", base)
    if kind in ("mura", "cho", "machi", "son") and base.endswith(kind) and len(base) > len(kind) + 2:
        base = base[: -len(kind)]
    tokens: list[str] = []
    for token in (base, base.replace("-", "")):
        if re.fullmatch(r"[a-z][a-z0-9-]{1,30}", token) and token not in tokens:
            tokens.append(token)
    return tokens[:MAX_TENANT_TOKENS]


def kensakusystem_is_empty(session: requests.Session, soup: BeautifulSoup, top_url: str,
                           timeout: float, page_delay: float) -> bool:
    """kensakusystem のテナントの閲覧の頁（See.exe）に会議が無いか。"""
    link = soup.find("a", href=re.compile(r"See\.exe", re.I))
    if link is None:
        return True
    if page_delay:
        time.sleep(page_delay)
    try:
        resp = session.get(urljoin(top_url, str(link["href"])), timeout=timeout, headers={"Referer": top_url})
    except requests.RequestException:
        return False
    if resp.status_code != 200:
        return True
    text = decode_html(getattr(resp, "content", b"") or b"", resp.headers.get("Content-Type", ""))
    return "登録されていません" in text


def guess_vendor_tenant(session: requests.Session, code: str, short_name: str, hosts: set[str],
                        timeout: float, page_delay: float) -> tuple[dict | None, list[str]]:
    """リンクされていないベンダのテナントを推して確かめる。(見つけたもの, 退けた理由)。

    頁が自治体名を名乗り、公式サイトへリンクしていれば medium。名前だけなら low
    （同じ名前の自治体が他にあるなら採らない）。名前が違うテナントは退ける
    （中川町に対する kensakusystem の nakagawa は中川村のもの）。
    """
    rejected: list[str] = []
    if short_name == "":
        return None, rejected
    for token in tenant_tokens(code):
        for system_type, check_template, entry_template in TENANT_GUESSES:
            check_url = check_template.format(t=token)
            if page_delay:
                time.sleep(page_delay)
            try:
                resp = session.get(check_url, timeout=timeout, allow_redirects=True)
            except requests.RequestException:
                continue
            if resp.status_code != 200:
                continue
            raw = getattr(resp, "content", b"") or b""
            text = decode_html(raw, resp.headers.get("Content-Type", ""))
            if system_type == "kensakusystem":
                soup = BeautifulSoup(text, "html.parser")
                heading = page_heading(soup)
                if "会議録" not in heading or re.search(r"映像|録画|中継", heading):
                    rejected.append(f"{check_url}: 会議録の頁でない（{heading[:30]}）")
                    continue
                named = short_name in heading
                shown = heading[:40]
            else:
                named = short_name in text
                shown = "message.js"
            if named and system_type == "kensakusystem" and kensakusystem_is_empty(
                    session, soup, check_url, timeout, page_delay):
                # 頁はあるが会議録が登録されていない（十津川村: 閲覧の頁が「議会名が登録されていません。」）。
                rejected.append(f"{check_url}: 閲覧の頁に会議が無い")
                continue
            if not named:
                # 別の自治体のテナントか、自治体名を書いていないテナント（どちらとも確かめられない）。
                rejected.append(f"{check_url}: {short_name}を名乗らない（{shown}）")
                continue
            entry = entry_template.format(t=token)
            # 同じ名前の自治体が他にあるなら、公式サイトのホストそのものへのリンクを求める
            # （kensakusystem の asahi は三重県朝日町のもので、山形県朝日町ではない）。
            if links_back_to_site(text, hosts, strict=not municipal_name_is_unique(code)):
                return {"url": entry, "system_type": system_type, "confidence": "medium",
                        "evidence": f"tenant-guess: {token} は{short_name}を名乗り公式サイトへリンクする"}, rejected
            if municipal_name_is_unique(code):
                return {"url": entry, "system_type": system_type, "confidence": "low",
                        "evidence": f"tenant-guess: {token} は{short_name}を名乗るが公式サイトへのリンクが無い"}, rejected
            rejected.append(f"{check_url}: 同名の自治体があり公式サイトへのリンクが無い")
    return None, rejected


ASSET_SUFFIXES = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip", ".doc", ".docx",
                  ".xls", ".xlsx", ".ppt", ".pptx", ".mp4", ".mp3", ".css", ".js")
# 入口ページで行政の側へ進むリンク。
ENTRANCE_ADMIN_RE = re.compile(
    r"役場|市役所|公式|行政|くらし|暮らし|市政|町政|村政|kurashi|gyosei|gyousei|shisei|chosei|"
    r"official|/main|/top|/index|hokkaido\.",
    re.I,
)
# 入口ページから先に進まない方がよいリンク。
NEGATIVE_ENTRANCE_RE = re.compile(
    r"観光|kanko|kankou|tourism|移住|iju|ijyu|ふるさと納税|furusato|facebook|twitter|instagram|youtube|line\.me",
    re.I,
)
NEWS_URL_RE = re.compile(r"news|topics|oshirase|whatsnew|/info/|information|/blog/|/event", re.I)
# 会議録の頁から検索システムへ渡るリンク。
SEARCH_SYSTEM_RE = re.compile(r"検索システム|会議録検索|会議録の検索")
# 1 自治体で会議録の入口候補を確かめる数。候補ごとに取得側の巡回を走らせる。
MAX_PROBES = 3
# 入口ページ（くらし・行政／観光の振り分けだけのトップ）で辿る同ホストリンクの数。
ENTRANCE_FALLBACK_LINKS = 5
# 会議録の手掛かりがあるリンクだけを辿る深さの上限（ホームページが 0）。
# 一覧がトップから 4〜6 段にある自治体がある（藤崎町・城里町）。手掛かりの無い
# リンクは max_depth で止めるので、ページ数の上限はそのまま効く。
DEEP_MAX_DEPTH = 6
# max_depth より深い階層で辿るリンクの優先度の下限（議会・会議録の語があるもの）。
DEEP_LINK_PRIORITY = 20
# 行き先が尽きたときに足す、トップと 1 段目のメニューのリンク。東川町は議会を
# 「町の紹介」の下に置き、トップのどのリンクにも手掛かりの語が無かった。
MENU_RESERVE_LINKS = 16
MENU_REFILL = 3
MENU_LABEL_MAX = 16
# 年別の棚（入口 → 年 → 会議 → PDF）の見本として開く年の数と、1 つあたりのページ数。
# 取得側の巡回を少ないページ数で走らせると、年の頁だけで上限を使い切る（御代田町）。
YEAR_SAMPLES = 2
YEAR_SAMPLE_PAGES = 5


def is_menu_link(url: str, text: str, hosts: set[str]) -> bool:
    """トップや 1 段目に並ぶメニューらしいリンクか（短い名前・同じサイト・お知らせでない）。"""
    label = str(text or "").strip()
    if not (2 <= len(label) <= MENU_LABEL_MAX):
        return False
    if not is_same_site(url, hosts):
        return False
    path = urlsplit(url).path.lower()
    if path.endswith(ASSET_SUFFIXES) or NEWS_URL_RE.search(url):
        return False
    return not NEGATIVE_ENTRANCE_RE.search(f"{label} {url}") and not NEGATIVE_HINT_RE.search(label)


def discover_one(session: requests.Session, code: str, name: str, homepage: str,
                 max_pages: int, max_depth: int, timeout: float,
                 page_delay: float, deep_probe: bool = True,
                 deep_depth: int = DEEP_MAX_DEPTH) -> Discovery:
    result = Discovery(jis_code=code, name=name, homepage=homepage)
    home_host = urlsplit(homepage).netloc.lower()
    hosts = _site_hosts(homepage)
    short_name = short_municipal_name(code, name)

    visited: set[str] = set()
    # 会議録ヒントの強いリンクを先に開く。幅優先だと、トップから並ぶ組織・部署の
    # ページで上限を使い切り、議会の会議録ページまで届かなかった（士別市・粕屋町）。
    frontier: list[tuple[int, int, int, str]] = []
    order = itertools.count()
    heapq.heappush(frontier, (-1000, 0, next(order), homepage))
    best_local: Discovery | None = None  # 自ホスト会議録ページの低信頼候補
    # 会議録の入口候補: URL -> 点数（題名が会議録を名乗る頁 30、リンク文字列だけ 20、
    # 会議の名前で並ぶ PDF の頁 22、その親の年別の棚は子の点数 + 1）
    candidates: dict[str, int] = {}
    homepage_problem = ""
    # 候補へ辿ってきた経路（リンク元の頁題名・URL・リンク文字列）。議会の会議録かの判断に使う。
    routes: dict[str, str] = {}
    # 候補の頁で読んだ題名（<title> と h1）。
    headings: dict[str, str] = {}
    # 候補を指したリンク文字列。頁を開いていない候補の題名の代わりにする（室戸市）。
    link_names: dict[str, str] = {}
    # 開いた頁の題名。候補になった親の棚（年別の頁の親）の題名に使う。
    page_titles: dict[str, str] = {}
    # 最初にリンクを見つけた頁と、そのリンク文字列。
    parents: dict[str, str] = {}
    link_texts: dict[str, str] = {}
    # 頁に並ぶ年別のリンクのうち取得側が辿るもの（年の棚の見本に使う）と、
    # 年別のリンクの年を除いた名前。
    year_links: dict[str, list[str]] = {}
    year_names: dict[str, list[str]] = {}
    # 頁の節の見出しで分かった会議録の PDF（本文の見本に使う）と、頁のリンク文字列・
    # ファイル名・節の見出しで会議録と分かる PDF の数。
    page_samples: dict[str, list[tuple[str, str]]] = {}
    own_minutes_links: dict[str, int] = {}
    # 頁にある年・会議録の棚へのリンク。年の頁から他の年へ辿れるかを見る。
    ladder_links: dict[str, list[tuple[str, str]]] = {}
    # 年の頁しか入口にできなかった候補（取得側が他の年へ辿れない）と、見本の本文を
    # 読めなかった候補。取得の対象にはせず、人が見る。
    partial: list[Discovery] = []
    # 頁にある「会議録検索システム」へのリンク。自ホストの会議録が古い年だけで、
    # 新しい年はベンダの検索システムにあることがある（城里町）。
    search_links: dict[str, list[str]] = {}
    # 行き先が尽きたときに足すメニューのリンク。
    reserve: list[tuple[int, str]] = []
    reserved: set[str] = set()
    probed: set[str] = set()
    probe_notes: list[str] = []
    video_only: list[str] = []
    trusted_notes: list[str] = []

    def medium(url: str, system_type: str, evidence: str) -> Discovery:
        note = "要確認(自ホスト会議録)"
        if urlsplit(url).netloc.lower() in trusted_notes:
            note = "要確認(公式サイトから辿った別ホストの会議録)"
        return Discovery(
            jis_code=code, name=name, homepage=homepage,
            candidate_url=url, system_type=system_type, confidence="medium",
            evidence=evidence[:200], note=note,
            pages_fetched=result.pages_fetched,
        )

    def year_sample(url: str, route: str, title: str, found: dict) -> None:
        """年別の棚の見本: 新しい年から順に開き、その下の会議録 PDF を数える。"""
        for year_url in year_links.get(url, [])[:YEAR_SAMPLES]:
            if found["minutes"] + found["meetings"] >= MIN_MINUTES_PDFS:
                return
            sub = probe_minutes_pdfs(session, year_url, timeout, page_delay,
                                     max_pages=YEAR_SAMPLE_PAGES,
                                     context=f"{route} {title} {url}", start_title=title)
            if not sub:
                continue
            for key in ("items", "minutes", "meetings", "pages"):
                found[key] += sub.get(key, 0)
            found["examples"] = (found["examples"] + sub.get("examples", []))[:3]
            found["samples"] = found["samples"] + sub.get("samples", [])

    def vendor_behind(url: str) -> Discovery | None:
        """会議録の頁から「会議録検索システム」の頁を 1 つ開き、ベンダのリンクを探す。"""
        for search_url in search_links.get(url, [])[:1]:
            if search_url in visited:
                continue
            visited.add(search_url)
            if page_delay:
                time.sleep(page_delay)
            soup, final = fetch_page(session, search_url, timeout)
            result.pages_fetched += 1
            if soup is None:
                continue
            for absolute, text in iter_links(soup, final):
                vendor = classify_vendor(absolute)
                if vendor and not is_video_only_vendor(absolute, vendor):
                    return Discovery(
                        jis_code=code, name=name, homepage=homepage,
                        candidate_url=absolute, system_type=vendor, confidence="high",
                        evidence=f"link[{text}] -> {vendor}（会議録の頁の検索システム）",
                        pages_fetched=result.pages_fetched,
                    )
        return None

    def single_year_entry(url: str) -> bool:
        """年の頁で、取得側がそこから他の年や棚へ辿れないか（月形町「令和8年 会議結果」）。"""
        if not is_year_shelf_title(page_titles.get(url, "")):
            return False
        return not any(key != url and scraper_follows_from_entry(text, key)
                       for key, text in ladder_links.get(url, []))

    def accept(url: str, evidence: str) -> Discovery | None:
        found = vendor_behind(url)
        if found:
            return found
        if single_year_entry(url):
            # 入口にすると、その年の分しか取れない。取得の対象にはせず、人に回す。
            partial.append(Discovery(
                jis_code=code, name=name, homepage=homepage,
                candidate_url=url, system_type="独自", confidence="low", evidence=evidence[:200],
                note="要人手判定(年の頁しか入口にできない。取得側が他の年へ辿れない)",
                pages_fetched=result.pages_fetched,
            ))
            return None
        return medium(url, "独自", evidence)

    def probe(url: str) -> Discovery | None:
        probed.add(url)
        route = routes.get(url, "")
        title = headings.get(url) or page_titles.get(url) or link_names.get(url, "")
        found = probe_minutes_pdfs(session, url, timeout, page_delay, context=route, start_title=title)
        if found is None:
            # 取得側を読み込めない環境。簡易判定で代える。
            simple = probe_own_site(session, url, urlsplit(url).netloc.lower(), timeout, page_delay,
                                    context=route)
            if simple and simple["system_type"] == "独自":
                return medium(simple["candidate_url"], simple["system_type"], f"own-site: {simple['evidence']}")
            if simple:
                probe_notes.append(simple["evidence"])
            return None
        found.setdefault("meetings", 0)
        found.setdefault("samples", [])
        if found["minutes"] + found["meetings"] < MIN_MINUTES_PDFS:
            year_sample(url, route, title, found)
        # 本文の見本は、頁の節の見出しで会議録と分かった PDF を先にする。
        samples: list[tuple[str, str]] = []
        for sample in page_samples.get(url, []) + list(found["samples"]):
            if sample[1] not in {seen for _t, seen in samples}:
                samples.append(sample)
        examples = " / ".join(found["examples"])
        if found["minutes"] >= MIN_MINUTES_PDFS:
            # 題名・頁の名前が会議録を名乗る。本文が議決結果・日程なら採らない（剣淵町）。
            verdict, body_note = check_minutes_body(session, samples, timeout, page_delay) if samples else (None, "")
            if verdict is False:
                probe_notes.append(body_note)
                return None
            evidence = f"own-site: 会議録PDF {found['minutes']}件（{found['pages']}頁を確認）{examples}"
            if verdict is None and samples and own_minutes_links.get(url, 0) < MIN_MINUTES_PDFS:
                # 見本の本文を 1 件も読めない（文字の無い PDF・取得の失敗）うえ、入口の頁の
                # リンク文字列・ファイル名・節の見出しでも会議録と分からない。名前の無い PDF は
                # 取得側が「会議録」と呼ぶので、その数だけでは日程や通告一覧と見分けられない
                # （香春町の定例会のお知らせ）。取得の対象にはせず、人に回す。
                partial.append(Discovery(
                    jis_code=code, name=name, homepage=homepage,
                    candidate_url=url, system_type="独自", confidence="low", evidence=evidence[:200],
                    note=f"要人手判定(見本の PDF の本文を読めない: {body_note})",
                    pages_fetched=result.pages_fetched,
                ))
                return None
            note = f"; {body_note}" if body_note else ""
            return accept(url, f"{evidence}{note}")
        if found["minutes"] + found["meetings"] >= MIN_MINUTES_PDFS:
            # 会議の名前で並ぶが会議録を名乗らない（白川町）。本文が会議録なら採る。
            verdict, body_note = check_minutes_body(session, samples, timeout, page_delay) if samples else (None, "")
            if verdict:
                return accept(url, f"own-site: 会議のPDF {found['minutes'] + found['meetings']}件"
                                   f"（{found['pages']}頁を確認）{examples}; {body_note}")
            probe_notes.append(f"会議のPDF{found['meetings']}件は会議録と確かめられず（{body_note}）")
            return None
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

    def refill_from_reserve() -> bool:
        added = 0
        while reserve and added < MENU_REFILL:
            depth_next, url = reserve.pop(0)
            if url in visited:
                continue
            heapq.heappush(frontier, (-1, depth_next, next(order), url))
            added += 1
        return added > 0

    while result.pages_fetched < max_pages:
        if not frontier and not refill_from_reserve():
            break
        _neg, depth, _order, url = heapq.heappop(frontier)
        norm = url.split("#", 1)[0]
        if norm in visited:
            continue
        visited.add(norm)

        if result.pages_fetched and page_delay:
            time.sleep(page_delay)
        soup, final = fetch_page(session, norm, timeout)
        result.pages_fetched += 1
        if soup is None and depth == 0:
            # トップが開けないとその先が全部見えない。言い換えを試してから諦める。
            for variant in homepage_variants(norm):
                if page_delay:
                    time.sleep(page_delay)
                soup, final = fetch_page(session, variant, timeout)
                result.pages_fetched += 1
                if soup is not None:
                    visited.add(variant.split("#", 1)[0])
                    break
            if soup is None:
                homepage_problem = describe_fetch_failure(session, norm, timeout)
        if soup is None:
            continue
        final = final.split("#", 1)[0]
        visited.add(final)
        heading = page_heading(soup)
        page_titles[final] = heading
        if depth == 0:
            # トップが別ドメインへ転送する自治体がある（砺波市 toyama.jp → lg.jp）。
            hosts |= _site_hosts(final)
            home_host = urlsplit(final).netloc.lower() or home_host
        elif not is_same_site(final, hosts) and not is_assembly_site(final, hosts):
            # 議会・会議録が別のホストにある（議会の独自ドメインなど）。公式サイトから
            # 議会のリンクで来て、頁が自治体の名前を名乗るなら、その自治体のサイトとして扱う。
            if trusts_external_page(final, heading, link_texts.get(norm, ""), short_name,
                                    str(soup), hosts, code):
                hosts.add(urlsplit(final).netloc.lower())
                trusted_notes.append(urlsplit(final).netloc.lower())

        links = meta_refresh_links(soup, final) + iter_links(soup, final)
        if depth == 0:
            # スプラッシュ型のトップが JavaScript で本体へ送る。
            links = script_redirect_links(soup, final) + links

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

        own_page = is_same_site(final, hosts) or is_assembly_site(final, hosts)
        if depth >= 1 and own_page and is_minutes_entry_title(heading):
            # 「会議録を掲載しました」のお知らせ記事は、その回の分しか並ばない。
            # 常設の会議録ページ（湧別町 content=516）を先に確かめる。
            score = 25 if NEWS_URL_RE.search(final) else 30
            candidates[final] = max(candidates.get(final, 0), score)
            headings[final] = heading
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

        # 年で始まるリンク（年別の棚）。
        page_years: list[str] = []
        for absolute, text in links:
            key = absolute.split("#", 1)[0]
            if is_year_link(text) and is_same_site(key, hosts) and key not in page_years \
                    and not urlsplit(key).path.lower().endswith(ASSET_SUFFIXES):
                page_years.append(key)
                year_names.setdefault(final, []).append(year_link_name(text))
                if scraper_follows_from_entry(text, key):
                    year_links.setdefault(final, []).append(key)
        searches = [
            absolute.split("#", 1)[0] for absolute, text in links
            if SEARCH_SYSTEM_RE.search(text) and is_same_site(absolute, hosts)
            and not urlsplit(absolute).path.lower().endswith(ASSET_SUFFIXES)
        ]
        if searches:
            search_links[final] = searches
        ladder_links[final] = [
            (absolute.split("#", 1)[0], text) for absolute, text in links
            if (is_year_link(text) or is_minutes_entry_title(text)) and is_same_site(absolute, hosts)
            and not urlsplit(absolute).path.lower().endswith(ASSET_SUFFIXES)
        ]

        # 会議録の PDF が直に並ぶページ。題名が「令和8年9月定例会」で会議録を
        # 名乗らないことがある（和束町）。並んでいる PDF の方で見分ける。頁の中の
        # 節の見出し（東川町「会議記録」・豊富町「議事録」）も PDF の置き場の名前として読む。
        route_here = f"{routes.get(norm, '')[-200:]} {heading} {final}"
        strong_hits: list[tuple[str, str, str]] = []
        weak_hits: list[tuple[str, str, str]] = []
        for u, t, section in pdf_links_with_sections(soup, final):
            kind = pdf_item_kind(t, section_page_title(section, heading), route_here, u, final)
            if kind == "minutes":
                strong_hits.append((t, u, section))
            elif kind == "meeting":
                weak_hits.append((t, u, section))
        own_minutes_links[final] = len(strong_hits)
        if depth >= 1 and own_page and len(strong_hits) + len(weak_hits) >= MIN_MINUTES_PDFS:
            score = 30 if len(strong_hits) >= MIN_MINUTES_PDFS else 22
            candidates[final] = max(candidates.get(final, 0), score)
            sections = sorted({s for _t, _u, s in strong_hits if s and is_minutes_entry_title(s)})
            headings[final] = " ".join([heading] + sections).strip()
            routes[final] = route_here
            page_samples[final] = [(t, u) for t, u, _s in strong_hits + weak_hits][:MAX_BODY_SAMPLES]
            # 年や 1 回の会議の頁なら、全年が並ぶ棚を先に確かめる。取得の入口は棚の方がよい。
            if is_year_link(heading):
                parent = parents.get(norm) or parents.get(final)
                own_name = year_link_name(link_texts.get(norm, ""))
                if (is_year_shelf_title(heading) and parent and parent != final
                        and year_names.get(parent, []).count(own_name) >= 2):
                    # 年別の頁の親（藤崎町・白川町の「定例会・臨時会」）。親に同じ名前の年の
                    # リンクが並んでいるときだけ（粕屋町の議会トップの「令和8年 一般質問通告書」
                    # 「令和8年 議会会議録」は年の棚ではない）。
                    candidates[parent] = max(candidates.get(parent, 0), score + 1)
                # 年や会議の頁が載せる、年の付かない会議録の棚（勝浦市「会議録」・粕屋町「会議録」）。
                shelves = [
                    absolute.split("#", 1)[0] for absolute, text in links
                    if is_minutes_entry_title(text) and not is_year_link(text) and not ORDINAL_RE.search(text)
                    and is_same_site(absolute, hosts) and absolute.split("#", 1)[0] not in (norm, final)
                    and not urlsplit(absolute).path.lower().endswith(ASSET_SUFFIXES)
                ]
                for shelf_url in shelves[:2]:
                    candidates[shelf_url] = max(candidates.get(shelf_url, 0), score + 1)

        entry_links: set[str] = set()
        for absolute, text in links:
            key = absolute.split("#", 1)[0]
            path = urlsplit(absolute).path.lower()
            if key not in parents:
                parents[key] = final
            if key not in link_texts or (MINUTES_HINT_RE.search(text) and not MINUTES_HINT_RE.search(link_texts[key])):
                link_texts[key] = text
            if path.endswith(ASSET_SUFFIXES):
                continue
            if not is_same_site(absolute, hosts) and not is_assembly_site(absolute, hosts):
                if MINUTES_HINT_RE.search(text) and key not in routes:
                    routes[key] = f"{routes.get(norm, '')[-200:]} {heading} {final} {text}"
                continue
            if is_minutes_entry_title(text):
                candidates.setdefault(key, 20)
                link_names.setdefault(key, text)
                entry_links.add(key)
            if key not in routes:
                routes[key] = f"{routes.get(norm, '')[-200:]} {heading} {final} {text}"
        if depth >= 1 and own_page and len(entry_links - {norm, final}) >= MIN_MINUTES_PDFS:
            # 会議録の記事が 1 回分ずつ並ぶ一覧（五木村「令和8年第2回定例会会議録の公開」）。
            # 記事 1 つには PDF が 1 件しかないので、一覧の方を入口として確かめる。
            candidates[final] = max(candidates.get(final, 0), 26)
            headings.setdefault(final, heading)
            routes.setdefault(final, route_here)

        # 題名が会議録を名乗る頁に着いたら、その場で確かめる。見つかれば残りを開かない。
        if deep_probe and len(probed) < MAX_PROBES:
            ready = next_candidate(30)
            if ready:
                found = probe(ready)
                if found:
                    return found

        if depth <= 1:
            # メニューのリンクを控える。手掛かりの語が無い中継ページ（東川町「町の紹介」）は、
            # 行き先が尽きたときにここから開く。
            for absolute, text in links:
                key = absolute.split("#", 1)[0]
                if len(reserve) >= MENU_RESERVE_LINKS:
                    break
                if key in visited or key in reserved or not is_menu_link(key, text, hosts):
                    continue
                reserve.append((depth + 1, key))
                reserved.add(key)

        if depth >= deep_depth:
            continue
        # 会議録・議会の棚にある年のリンク（白川町「2026年(令和8年)」）は手掛かりの語が
        # 無くても辿る。
        shelf = bool(MINUTES_HINT_RE.search(heading)) and not CANDIDATE_NEG_RE.search(heading)
        scored: list[tuple[int, str, str]] = []
        for absolute, text in links:
            if urlsplit(absolute).path.lower().endswith(ASSET_SUFFIXES):
                # PDF・Excel を「ページ」として開くと予算を食う（豊富町は 18 頁中 8 頁）。
                continue
            priority = link_priority(absolute, text, home_host)
            if shelf and is_year_shelf_title(text) and is_same_site(absolute, hosts):
                priority += DEEP_LINK_PRIORITY
            if is_minutes_entry_title(text):
                priority += 40
            scored.append((priority, absolute, text))
        scored.sort(key=lambda item: item[0], reverse=True)
        added = 0
        for pr, absolute, text in scored:
            # 会議録ヒント(>=20) / 市政ハブ(同ホスト>=8) / ベンダ(>=100) だけを辿る。
            # 手掛かりの無い同ホストリンク(+2)は広げない。
            if pr < 6:
                break
            if depth >= max_depth and pr < DEEP_LINK_PRIORITY:
                # 深い階層では会議録・議会の手掛かり（リンク文字列か URL）があるリンクだけを辿る。
                continue
            nn = absolute.split("#", 1)[0]
            if nn in visited:
                continue
            heapq.heappush(frontier, (-pr, depth + 1, next(order), nn))
            added += 1
            if added >= 10:
                break
        if depth == 0 and added < 2:
            # 入口ページ: 「くらし・行政」「公式サイト」だけが並ぶ。手掛かりの語が無くても
            # その先を少し開く（指宿市 /main/・室戸市 top.php・宿毛市）。
            # 観光の案内が先に並ぶ入口（土佐清水市）では行政側を先に選び、
            # 役場のサイトが別ホストの入口（標茶町 hokkaido.shibecha.jp）も辿る。
            fallback: list[tuple[int, str]] = []
            for absolute, text in links:
                nn = absolute.split("#", 1)[0]
                if nn in visited or urlsplit(nn).path.lower().endswith(ASSET_SUFFIXES):
                    continue
                if nn in {seen for _rank, seen in fallback} or NEWS_URL_RE.search(nn):
                    continue
                host = urlsplit(nn).netloc.lower()
                same = host in hosts or is_same_site(nn, hosts)
                blob = f"{text} {nn}"
                official = bool(ENTRANCE_ADMIN_RE.search(blob))
                if not same and not official:
                    continue
                if NEGATIVE_ENTRANCE_RE.search(blob):
                    continue
                fallback.append((2 if official else 1, nn))
            fallback.sort(key=lambda item: -item[0])
            for _rank, nn in fallback[:ENTRANCE_FALLBACK_LINKS]:
                heapq.heappush(frontier, (-5, depth + 1, next(order), nn))

    if deep_probe:
        while len(probed) < MAX_PROBES:
            ready = next_candidate(20)
            if ready is None:
                break
            found = probe(ready)
            if found:
                return found

    tenant_note = ""
    if deep_probe:
        # 公式サイトからリンクされていないテナント（kensakusystem の長野の村など）。
        guessed, rejected = guess_vendor_tenant(session, code, short_name, hosts, timeout, page_delay)
        if guessed and guessed["confidence"] == "medium":
            # 名乗りと公式サイトへのリンクを確かめても、推したテナントは low に留める。
            # medium は定期の探索で人の確認なしに取得の対象になる。同じ名前・同じ
            # ローマ字の別の自治体の会議録を載せると害が大きい（泊村・奈良県川上村、977 件）。
            return Discovery(
                jis_code=code, name=name, homepage=homepage,
                candidate_url=guessed["url"], system_type=guessed["system_type"], confidence="low",
                evidence=guessed["evidence"][:200], note="要確認(推測したテナント・名乗りと公式サイトへのリンクあり)",
                pages_fetched=result.pages_fetched,
            )
        if guessed:
            return Discovery(
                jis_code=code, name=name, homepage=homepage,
                candidate_url=guessed["url"], system_type=guessed["system_type"], confidence="low",
                evidence=guessed["evidence"][:200], note="要人手判定(推測したテナント)",
                pages_fetched=result.pages_fetched,
            )
        if rejected:
            tenant_note = "退けたテナント: " + "; ".join(rejected)[:160]

    if partial:
        found = partial[0]
        if tenant_note:
            found.note = f"{found.note}; {tenant_note}"
        found.pages_fetched = result.pages_fetched
        return found
    strong = sorted(candidates.items(), key=lambda item: (-item[1], len(item[0])))
    if strong:
        url, _score = strong[0]
        note = "会議録ページはあるが会議録PDFを数えられず"
        if probe_notes:
            note += "（" + "; ".join(probe_notes)[:120] + "）"
        if tenant_note:
            note += f"; {tenant_note}"
        return Discovery(
            jis_code=code, name=name, homepage=homepage,
            candidate_url=url, system_type="", confidence="low",
            evidence="own-site minutes page", note=note, pages_fetched=result.pages_fetched,
        )
    video_note = f"録画配信のみ見つかった: {video_only[0]}" if video_only else ""
    if homepage_problem:
        video_note = f"公式ホームページを開けない（{homepage_problem}）"
    if tenant_note:
        video_note = f"{video_note}; {tenant_note}" if video_note else tenant_note
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
    # 全件を回すと 1 時間を超える。途中で止まっても結果が残るよう、1 件ごとに書く。
    with open(out_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["jis_code", "name", "homepage", "candidate_url",
                         "system_type", "confidence", "evidence", "pages_fetched", "note"])
        for i, code in enumerate(codes, 1):
            name = names.get(code, "")
            homepage = homepages[code]
            try:
                d = discover_one(session, code, name, homepage,
                                 args.max_pages, args.max_depth, args.timeout, args.page_delay)
            except Exception as exc:  # 1件の失敗で全体を止めない
                d = Discovery(jis_code=code, name=name, homepage=homepage, note=f"error: {exc}")
            results.append(d)
            writer.writerow([d.jis_code, d.name, d.homepage, d.candidate_url,
                             d.system_type, d.confidence, d.evidence, d.pages_fetched, d.note])
            handle.flush()
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

    print(f"\n完了: {len(results)}件  高信頼={high} 中信頼={medium} 低信頼={low} 不明={none}")
    print(f"候補CSV: {out_path}")
    print("※ 反映は doc/assembly-minutes-url-survey.md の手順で確認してから。"
          "取得の対象にするには台帳の crawl_status を enabled にする。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
