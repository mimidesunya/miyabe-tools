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


def probe_own_site(session: requests.Session, start_url: str, home_host: str,
                   timeout: float, page_delay: float, max_probe_pages: int = 6) -> dict | None:
    """議会ページ起点で同一ホストを浅く辿り、PDF会議録 / 日付HTML一覧を判定する。

    戻り値: {system_type, candidate_url, evidence} または None。
    - PDF会議録が集まる → "独自"（gikai_pdf の汎用クロールが受ける）
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
        soup = fetch(session, norm, timeout)
        fetched += 1
        if page_delay:
            time.sleep(page_delay)
        if soup is None:
            continue

        links = iter_links(soup, norm)
        pdf_hits = [(u, t) for (u, t) in links if looks_like_minutes_pdf(u, t)]
        html_dated = [(u, t) for (u, t) in links if looks_like_dated_minutes_html(u, t)]

        if len(pdf_hits) >= 3:
            return {
                "system_type": "独自",
                "candidate_url": norm,
                "evidence": f"pdf会議録 {len(pdf_hits)}件",
            }
        if len(html_dated) >= 5 and MINUTES_PATH_RE.search(norm):
            return {
                "system_type": "static-kaigiroku-dir",
                "candidate_url": norm,
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
    confidence: str = "none"   # high / low / none
    evidence: str = ""
    pages_fetched: int = 0
    note: str = ""


def fetch(session: requests.Session, url: str, timeout: float) -> BeautifulSoup | None:
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        return None
    if resp.status_code != 200:
        return None
    ctype = resp.headers.get("Content-Type", "")
    if "html" not in ctype.lower() and "<html" not in resp.text[:2000].lower():
        return None
    resp.encoding = resp.encoding or resp.apparent_encoding
    try:
        return BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return None


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


def discover_one(session: requests.Session, code: str, name: str, homepage: str,
                 max_pages: int, max_depth: int, timeout: float,
                 page_delay: float, deep_probe: bool = True) -> Discovery:
    result = Discovery(jis_code=code, name=name, homepage=homepage)
    home_host = urlsplit(homepage).netloc.lower()

    # まずリンクを見て（fetch 前に）ベンダURLが直接あれば確定。
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(homepage, 0)])
    best_local: Discovery | None = None  # 自ホスト会議録ページの低信頼候補

    while queue and result.pages_fetched < max_pages:
        url, depth = queue.popleft()
        norm = url.split("#", 1)[0]
        if norm in visited:
            continue
        visited.add(norm)

        soup = fetch(session, norm, timeout)
        result.pages_fetched += 1
        if page_delay:
            time.sleep(page_delay)
        if soup is None:
            continue

        links = iter_links(soup, norm)

        # このページ上のリンクからベンダを検出（fetch せずに確定できる）。
        for absolute, text in links:
            vendor = classify_vendor(absolute)
            if vendor:
                result.candidate_url = absolute
                result.system_type = vendor
                result.confidence = "high"
                result.evidence = f"link[{text}] -> {vendor}"
                return result

        # 現在ページ自体がベンダなら確定。
        vendor_self = classify_vendor(norm)
        if vendor_self:
            result.candidate_url = norm
            result.system_type = vendor_self
            result.confidence = "high"
            result.evidence = f"page -> {vendor_self}"
            return result

        # 自ホストで会議録らしいページに来ていれば低信頼候補として控える。
        if best_local is None and depth >= 1 and MINUTES_HINT_RE.search(norm + " " + (soup.title.get_text() if soup.title else "")):
            if re.search(r"gikai|giji|kaigiroku|kaigi|minutes", norm, re.I):
                best_local = Discovery(
                    jis_code=code, name=name, homepage=homepage,
                    candidate_url=norm, system_type="", confidence="low",
                    evidence="own-site minutes-like page", note="要人手判定(独自/PDF/静的)",
                )

        if depth >= max_depth:
            continue
        # 会議録ヒント優先で次階層を積む。
        ranked = sorted(links, key=lambda lt: link_priority(lt[0], lt[1], home_host), reverse=True)
        added = 0
        for absolute, text in ranked:
            pr = link_priority(absolute, text, home_host)
            # 会議録ヒント(>=20) / 市政ハブ(同ホスト>=8) / ベンダ(>=100) だけを辿る。
            # 手掛かりの無い同ホストリンク(+2)は広げない。
            if pr < 6:
                break
            nn = absolute.split("#", 1)[0]
            if nn not in visited:
                queue.append((absolute, depth + 1))
                added += 1
            if added >= 10:
                break

    if best_local is not None:
        # 自ホスト会議録ページを深掘りし、独自(PDF) / static-kaigiroku-dir を判定する。
        if deep_probe:
            probe = None
            try:
                probe = probe_own_site(session, best_local.candidate_url, home_host,
                                       timeout, page_delay)
            except Exception:
                probe = None
            if probe:
                best_local.system_type = probe["system_type"]
                best_local.candidate_url = probe["candidate_url"]
                best_local.confidence = "medium"
                best_local.evidence = f"own-site: {probe['evidence']}"
                best_local.note = "要確認(自ホスト会議録)"
        best_local.pages_fetched = result.pages_fetched
        return best_local
    result.note = "会議録システムを特定できず"
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
    high = low = none = 0
    for i, code in enumerate(codes, 1):
        name = names.get(code, "")
        homepage = homepages[code]
        try:
            d = discover_one(session, code, name, homepage,
                             args.max_pages, args.max_depth, args.timeout, args.page_delay)
        except Exception as exc:  # 1件の失敗で全体を止めない
            d = Discovery(jis_code=code, name=name, homepage=homepage, note=f"error: {exc}")
        results.append(d)
        tag = {"high": "○", "low": "△", "none": "×"}.get(d.confidence, "×")
        if d.confidence == "high":
            high += 1
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
