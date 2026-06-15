#!/usr/bin/env python3
"""空欄(system_type 未確定)の会議録 URL を、自治体公式サイトの深掘りクロールで発見する。

既存の build_assembly_minutes_system_urls_tsv.ps1（App Mints + 浅い再探索）でも
URL を確定できなかった自治体が約320件ある。ここでは公式 HP から最大4階層まで
会議録/議事録/議会 リンクを辿り、(1) 既知システムの外部ホストへのリンク or
(2) 会議録ページ上の同一サイト PDF を検出して system_type と代表URLを推定する。

既定はドライラン（収量と分類分布を表示するだけ）。--write で TSV を更新する。
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import re
import sys
import warnings
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import urllib3
import requests

warnings.filterwarnings("ignore")
urllib3.disable_warnings()

ROOT = Path(__file__).resolve().parents[2]
TSV = ROOT / "data" / "municipalities" / "assembly_minutes_system_urls.tsv"
HOMEPAGES = ROOT / "data" / "municipalities" / "municipality_homepages.csv"
MASTER = ROOT / "data" / "municipalities" / "municipality_master.tsv"
UA = {"User-Agent": "Mozilla/5.0 (compatible; miyabe-tools-discovery/1.0)"}

# 会議録リンクとして辿る/評価するためのシグナル。
MINUTES_TEXT = re.compile(r"会議録|議事録|会議の記録|本会議録")
GIKAI_TEXT = re.compile(r"議会")
MINUTES_HREF = re.compile(r"kaigiroku|gijiroku|giji|minutes|kaigi", re.I)

# 外部ホストでシステムを断定できるパターン（PS1 の Classify-MinutesSystem 準拠）。
HOST_SYSTEMS = [
    (re.compile(r"(^|\.)kaigiroku\.net$", re.I), "kaigiroku.net"),
    (re.compile(r"\.dbsr\.jp$", re.I), "dbsr"),
    (re.compile(r"db-search\.com$", re.I), "dbsr"),
    (re.compile(r"\.gijiroku\.com$", re.I), "gijiroku.com"),
    (re.compile(r"ami-search\.amivoice\.com$", re.I), "amivoice"),
    (re.compile(r"voicetechno\.net$", re.I), "voicetechno"),
    (re.compile(r"discussvision|discuss-net|gikai-portal", re.I), "discussvision"),
    (re.compile(r"kensakusystem", re.I), "kensakusystem"),
]
ASSET_RE = re.compile(r"\.(pdf|jpg|jpeg|png|gif|zip|docx?|xlsx?|pptx?|css|js|ico|mp4|mp3)$", re.I)


def load_rows(path: Path, delim: str = "\t") -> list[dict]:
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=delim))


def classify_host(url: str) -> str | None:
    host = urlsplit(url).netloc.lower()
    path = urlsplit(url).path.lower()
    for pat, name in HOST_SYSTEMS:
        if pat.search(host) or pat.search(path):
            return name
    if host.startswith("kaigiroku.") and re.search(r"/index\.php/?$", path):
        return "kaigiroku-indexphp"
    return None


def fetch(session: requests.Session, url: str, timeout: float) -> requests.Response | None:
    try:
        r = session.get(url, headers=UA, timeout=timeout, verify=False, allow_redirects=True)
        if r.status_code != 200:
            return None
        if "text/html" not in (r.headers.get("content-type", "") or "").lower():
            return None
        r.encoding = r.apparent_encoding or r.encoding or "utf-8"
        return r
    except Exception:
        return None


def discover(code: str, homepage: str, *, timeout: float, max_pages: int, max_depth: int) -> tuple[str, str, str]:
    """(jis_code, system_type, url) を返す。未解決なら ('', '')。"""
    session = requests.Session()
    start_netloc = urlsplit(homepage).netloc
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(homepage, 0)])
    pdf_on_minutes_page = ""  # 会議録ページ上で見つけた同一サイト PDF を持つページ

    while queue and len(visited) < max_pages:
        url, depth = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        resp = fetch(session, url, timeout)
        if resp is None:
            continue
        # 到達したページ自身が既知システムなら確定。
        sysname = classify_host(resp.url)
        if sysname:
            return (code, sysname, resp.url)

        html = resp.text
        page_is_minutes = bool(MINUTES_TEXT.search(html[:5000]))
        scored: list[tuple[int, str, str]] = []  # (score, abs_url, text)
        for m in re.finditer(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.I | re.S):
            href = m.group(1).strip()
            if not href or href.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
                continue
            text = re.sub(r"<[^>]+>", " ", m.group(2))
            text = re.sub(r"\s+", " ", text).strip()
            absolute = urljoin(resp.url, href).split("#", 1)[0]
            # 外部の既知システムへのリンクは即確定候補。
            ext = classify_host(absolute)
            if ext:
                return (code, ext, absolute)
            # 同一サイト PDF を会議録ページで見つけたら 独自(PDF) 候補として控える。
            if absolute.lower().endswith(".pdf"):
                if page_is_minutes or MINUTES_TEXT.search(text):
                    if not pdf_on_minutes_page:
                        pdf_on_minutes_page = resp.url
                continue
            if urlsplit(absolute).netloc != start_netloc or ASSET_RE.search(urlsplit(absolute).path):
                continue
            score = 0
            if MINUTES_TEXT.search(text):
                score += 5
            if MINUTES_HREF.search(absolute):
                score += 2
            if GIKAI_TEXT.search(text) or "/gikai" in absolute.lower():
                score += 1
            if score > 0 and absolute not in visited:
                scored.append((score, absolute, text))

        if depth < max_depth:
            scored.sort(reverse=True)
            for _score, absolute, _text in scored[:6]:
                queue.append((absolute, depth + 1))

    if pdf_on_minutes_page:
        return (code, "独自", pdf_on_minutes_page)
    return (code, "", "")


def main() -> int:
    parser = argparse.ArgumentParser(description="空欄会議録URLの深掘り発見")
    parser.add_argument("--write", action="store_true", help="解決結果を TSV に書き込む")
    parser.add_argument("--limit", type=int, default=0, help="処理件数上限（試験用）")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument("--max-depth", type=int, default=4)
    args = parser.parse_args()

    rows = load_rows(TSV)
    blanks = [r["jis_code"] for r in rows if not (r.get("system_type") or "").strip()]
    hp = {}
    for r in load_rows(HOMEPAGES, delim=","):
        c = (r.get("jis_code") or "").strip()
        u = (r.get("url") or "").strip()
        if c and u:
            hp[c] = u
    master = {r["jis_code"]: r for r in load_rows(MASTER)}
    targets = [(c, hp[c]) for c in blanks if c in hp]
    if args.limit > 0:
        targets = targets[: args.limit]
    print(f"blanks={len(blanks)} with_homepage={sum(1 for c in blanks if c in hp)} probing={len(targets)}")

    resolved: dict[str, tuple[str, str]] = {}
    from collections import Counter

    dist = Counter()
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(discover, c, u, timeout=args.timeout, max_pages=args.max_pages, max_depth=args.max_depth) for c, u in targets]
        for fut in cf.as_completed(futs):
            code, sysname, url = fut.result()
            if sysname:
                resolved[code] = (sysname, url)
                dist[sysname] += 1

    print(f"\n=== resolved {len(resolved)}/{len(targets)} ===")
    for k, v in dist.most_common():
        print(f"  {v:4d}  {k}")
    print("\n=== samples ===")
    for code, (sysname, url) in list(resolved.items())[:15]:
        print(f"  {code} {master.get(code,{}).get('name','')} -> {sysname} {url}")

    if args.write and resolved:
        for r in rows:
            c = r["jis_code"]
            if c in resolved:
                r["system_type"] = resolved[c][0]
                r["url"] = resolved[c][1]
        with open(TSV, "w", encoding="utf-8", newline="") as handle:
            w = csv.DictWriter(handle, fieldnames=rows[0].keys(), delimiter="\t")
            w.writeheader()
            w.writerows(rows)
        print(f"\n[WROTE] {len(resolved)} rows -> {TSV}")
    elif resolved:
        print("\n(dry-run; pass --write to apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
