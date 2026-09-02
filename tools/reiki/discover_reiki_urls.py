#!/usr/bin/env python3
"""例規集の入口 URL を探し直し、スクレイパが使える形で検証する。

台帳で「保存 0 件」の自治体は、登録 URL が死んでいるか、案内ページを指して
いるか、`独自` と書かれているだけで既知の製品を使っている。detect_system.py
は「登録 URL を開いて製品を見分ける」までで、**入口をどこにすればスクレイパが
動くか**までは出さない。ここはそこまでやる。

1. 登録 URL を開く。HTTP 状態と転送先を記録する。
2. 製品の印（HTML 文字列・リンク先のホスト）で系統を見分ける。
3. 見分けが付かなければ、公式ホームページから 例規／条例 のリンクを 2 段辿る。
4. 系統ごとに入口 URL を組み立て、**スクレイパが最初に開くページが実際に
   返ってくるか**を確かめる（joureikun なら aggregate/catalog/index.html、
   taikei なら reiki_taikei/taikei_default.html、など）。
5. 結果を CSV に出す。正本 TSV は書き換えない。

使い方:
    python3 tools/reiki/discover_reiki_urls.py --slugs 25202-hikone-shi,35211-nagato-shi
    python3 tools/reiki/discover_reiki_urls.py --codes 25202 35211 --save-out work/reiki/discovery.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parents[2]
for path in (str(REPO_ROOT), str(REPO_ROOT / "tools" / "reiki")):
    if path not in sys.path:
        sys.path.insert(0, path)

import reiki_targets  # noqa: E402

USER_AGENT = "Mozilla/5.0 (compatible; miyabe-tools/1.0; +https://tools.miya.be)"
TIMEOUT = 20
MUNI_DIR = REPO_ROOT / "data" / "municipalities"

# 例規集へのリンクらしさ。アンカー文言と href の両方に効かせる。
REIKI_HINT_RE = re.compile(r"例規|条例|規則|要綱|法規|reiki|jourei|jorei|hourei|horei|rules?", re.I)
NEGATIVE_RE = re.compile(r"パブリックコメント|意見公募|議会|会議録|入札|採用|イベント", re.I)

# 製品ごとの印。ホスト名・パス・HTML 文字列。前にあるものほど強い。
PRODUCT_URL_MARKERS: list[tuple[str, tuple[str, ...]]] = [
    ("legal-square", ("legal-square.com",)),
    ("d1-law", ("d1-law.com", "d1w_reiki", "/opensearch/Sr")),
    ("g-reiki", ("g-reiki.net",)),
    ("legalcrud", ("legalcrud.com",)),
    ("jourei-v5", ("JoureiV5HTMLContents", "joureiv5")),
    ("joureikun", ("aggregate/catalog", "/act/")),
    ("taikei", ("reiki_taikei/", "reiki_int/", "reiki_menu.html", "reiki_honbun/")),
]
PRODUCT_HTML_MARKERS: list[tuple[str, tuple[str, ...]]] = [
    ("legal-square", ("HAS-Shohin", "LegalSquare")),
    ("d1-law", ("Reiki-Base", "d1w_reiki", "OpenResDataWin")),
    ("g-reiki", ("GyoseiReiki",)),
    ("jourei-v5", ("JoureiV5",)),
    ("joureikun", ("joureikun", "aggregate/catalog"),),
    ("taikei", ("reiki_taikei", "reiki_honbun", "taikei_default")),
]


@dataclass
class Finding:
    slug: str
    code: str
    name: str
    registered_url: str
    registered_system: str
    http_status: str = ""
    final_url: str = ""
    detected_system: str = ""
    entry_url: str = ""
    verified: str = ""
    evidence: str = ""
    note: str = ""


def fetch(session: requests.Session, url: str, *, referer: str = "") -> tuple[int, str, str]:
    """(HTTP 状態, 最終 URL, 本文)。失敗は状態 0。"""
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.8"}
    if referer:
        headers["Referer"] = referer
    try:
        response = session.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
    except Exception as exc:
        return 0, url, f"__ERR_{type(exc).__name__}__"
    if not response.encoding or response.encoding.lower() in {"iso-8859-1", "ascii"}:
        response.encoding = response.apparent_encoding or "utf-8"
    return response.status_code, response.url, response.text if response.ok else ""


def detect_from_url(url: str) -> str:
    lowered = url.lower()
    for name, markers in PRODUCT_URL_MARKERS:
        if any(marker.lower() in lowered for marker in markers):
            return name
    return ""


def detect_from_html(html: str) -> str:
    for name, markers in PRODUCT_HTML_MARKERS:
        if any(marker in html for marker in markers):
            return name
    return ""


def links_of(html: str, base_url: str) -> list[tuple[str, str]]:
    """(絶対 URL, アンカー文言)。frame/iframe の src も含める。"""
    found: list[tuple[str, str]] = []
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return found
    for tag in soup.find_all(["a", "frame", "iframe", "area"]):
        href = tag.get("href") or tag.get("src") or ""
        href = str(href).strip()
        if not href or href.startswith(("javascript:", "mailto:", "#")):
            continue
        text = " ".join(tag.get_text(" ", strip=True).split()) if tag.name in ("a", "area") else ""
        found.append((urljoin(base_url, href), text))
    # onclick や meta refresh で飛ばす案内ページもある。
    for match in re.finditer(r"""(?:location\.href|window\.open|url)\s*=\s*['"]([^'"]+)['"]""", html, re.I):
        found.append((urljoin(base_url, match.group(1)), ""))
    return found


def product_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """製品の印を持つリンクだけ。"""
    return [(url, detect_from_url(url)) for url, _ in links_of(html, base_url) if detect_from_url(url)]


def reiki_links(html: str, base_url: str) -> list[str]:
    """例規集らしいリンク。製品の印が無いものだけ（あるものは product_links が拾う）。"""
    found: list[str] = []
    seen: set[str] = set()
    for url, text in links_of(html, base_url):
        blob = f"{text} {url}"
        if not REIKI_HINT_RE.search(blob) or NEGATIVE_RE.search(text):
            continue
        if url in seen or detect_from_url(url):
            continue
        seen.add(url)
        found.append(url)
    return found


def _dir_of(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path or "/"
    if not path.endswith("/"):
        path = path.rsplit("/", 1)[0] + "/"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def verify_entry(session: requests.Session, system: str, url: str) -> tuple[str, str, str]:
    """系統に合わせて入口 URL を組み立て、スクレイパが最初に開くページを確かめる。

    返すのは (入口 URL, 検証結果, 根拠)。検証結果は ok / ng。
    """
    if system == "joureikun" or system == "legalcrud":
        for base in _joureikun_bases(url):
            status, final, html = fetch(session, urljoin(base, "aggregate/catalog/index.html"))
            if status == 200 and re.search(r"act/[^\"'/]+\.html?", html):
                return base, "ok", f"catalog {status} act links"
        return url, "ng", "aggregate/catalog/index.html が返らない"
    if system == "jourei-v5":
        for base in _joureikun_bases(url):
            status, final, html = fetch(session, urljoin(base, "aggregate/catalog/result/catalog.htm"))
            if status == 200 and "act/frame/frame" in html:
                return base, "ok", f"catalog.htm {status}"
        return url, "ng", "aggregate/catalog/result/catalog.htm が返らない"
    if system in ("taikei", "g-reiki"):
        try:
            entry = reiki_targets.derive_taikei_entry_url(url)
        except Exception:
            entry = url
        candidates = [entry]
        base = _dir_of(url)
        candidates += [urljoin(base, "reiki_taikei/taikei_default.html"), urljoin(base, "reiki_menu.html")]
        for candidate in dict.fromkeys(candidates):
            status, final, html = fetch(session, candidate)
            if status == 200 and ("reiki_honbun" in html or "reiki_taikei" in html or "reiki_kana" in html):
                return url if candidate == entry else _dir_of(candidate) + ("reiki_menu.html" if "reiki_menu" in candidate else ""), "ok", f"{candidate.rsplit('/',1)[-1]} {status}"
        return url, "ng", "reiki_taikei / reiki_menu が返らない"
    if system in ("d1-law", "reiki.html"):
        status, final, html = fetch(session, url)
        if status == 200 and ("d1w_reiki" in html or "/opensearch/" in final or "mokujicd" in html or "Reiki-Base" in html):
            return final if "/opensearch/" in final else url, "ok", f"{status} d1-law markers"
        return url, "ng", f"{status} d1-law の印が無い"
    if system == "legal-square":
        status, final, html = fetch(session, url)
        if status == 200 and ("HAS-Shohin" in final or "HAS-Shohin" in html or "legal-square" in final):
            return final, "ok", f"{status}"
        return url, "ng", f"{status}"
    return url, "", "未対応の系統"


def _joureikun_bases(url: str) -> list[str]:
    """joureikun の base 候補。印を見つけた URL から上へ辿る。"""
    lowered = url.lower()
    bases: list[str] = []
    for marker in ("/aggregate/", "/act/"):
        index = lowered.find(marker)
        if index >= 0:
            bases.append(url[: index + 1])
    bases.append(_dir_of(url))
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    if path:
        bases.append(urlunsplit((parts.scheme, parts.netloc, path.rsplit("/", 1)[0] + "/", "", "")))
    return list(dict.fromkeys(bases))


def discover_one(session: requests.Session, finding: Finding, homepage: str, *, pause: float) -> Finding:
    visited: set[str] = set()
    queue: list[tuple[str, int]] = []
    if finding.registered_url:
        queue.append((finding.registered_url, 0))
    if homepage:
        queue.append((homepage, 1))

    best: tuple[str, str, str] | None = None  # (system, url, evidence)
    pages = 0
    while queue and pages < 14:
        url, depth = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        status, final, html = fetch(session, url, referer=homepage)
        pages += 1
        time.sleep(pause)
        if depth == 0:
            finding.http_status = str(status)
            finding.final_url = final
        if status != 200 or not html:
            continue
        # 1. 開いたページそのものが製品か
        system = detect_from_url(final) or detect_from_html(html)
        if system:
            candidate = (system, final, f"page:{'url' if detect_from_url(final) else 'html'}")
            if best is None:
                best = candidate
            entry, ok, evidence = verify_entry(session, system, final)
            if ok == "ok":
                finding.detected_system, finding.entry_url, finding.verified = system, entry, ok
                finding.evidence = f"{candidate[2]} / {evidence}"
                return finding
        # 2. 製品へのリンクがあるか
        for link, system in product_links(html, final):
            entry, ok, evidence = verify_entry(session, system, link)
            time.sleep(pause)
            if ok == "ok":
                finding.detected_system, finding.entry_url, finding.verified = system, entry, ok
                finding.evidence = f"link from {final} / {evidence}"
                return finding
            if best is None:
                best = (system, link, f"link from {final} ({evidence})")
        # 3. 例規らしいリンクを次に辿る
        if depth < 3:
            for link in reiki_links(html, final)[:8]:
                if link not in visited:
                    queue.append((link, depth + 1))
    if best is not None:
        finding.detected_system, finding.entry_url, finding.verified = best[0], best[1], "ng"
        finding.evidence = best[2]
    else:
        finding.note = "製品の印も例規集のリンクも見つからない"
    return finding


def load_homepages() -> dict[str, str]:
    with open(MUNI_DIR / "municipality_homepages.csv", encoding="utf-8-sig", newline="") as handle:
        return {row["jis_code"]: row["url"] for row in csv.DictReader(handle)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slugs", default="", help="カンマ区切り")
    parser.add_argument("--codes", nargs="*", default=None, help="jis_code")
    parser.add_argument("--pause", type=float, default=0.5)
    parser.add_argument("--save-out", default="")
    args = parser.parse_args()

    homepages = load_homepages()
    targets: list[dict] = []
    wanted_codes = set(args.codes or [])
    wanted_slugs = {s.strip() for s in args.slugs.split(",") if s.strip()}
    for target in reiki_targets.iter_reiki_targets():
        code = str(target.get("code") or "")
        slug = str(target.get("slug") or "")
        if code in wanted_codes or slug in wanted_slugs:
            targets.append(target)
    if not targets:
        print("対象がありません", file=sys.stderr)
        return 1

    session = requests.Session()
    results: list[Finding] = []
    for index, target in enumerate(targets, 1):
        finding = Finding(
            slug=str(target.get("slug") or ""),
            code=str(target.get("code") or ""),
            name=str(target.get("name") or ""),
            registered_url=str(target.get("source_url") or ""),
            registered_system=str(target.get("system_type") or ""),
        )
        try:
            finding = discover_one(session, finding, homepages.get(finding.code, ""), pause=args.pause)
        except Exception as exc:
            finding.note = f"error: {exc}"
        results.append(finding)
        mark = "○" if finding.verified == "ok" else ("△" if finding.detected_system else "×")
        print(
            f"[{index}/{len(targets)}] {mark} {finding.slug:26s} {finding.registered_system:10s}"
            f" -> {finding.detected_system or '-':11s} {finding.entry_url or finding.note}"
            f"  ({finding.http_status} {finding.evidence})",
            flush=True,
        )

    out = Path(args.save_out) if args.save_out else REPO_ROOT / "work" / "reiki" / f"discovery_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["slug", "code", "name", "registered_system", "registered_url", "http_status", "final_url",
             "detected_system", "entry_url", "verified", "evidence", "note"]
        )
        for f in results:
            writer.writerow([f.slug, f.code, f.name, f.registered_system, f.registered_url, f.http_status,
                             f.final_url, f.detected_system, f.entry_url, f.verified, f.evidence, f.note])
    print(f"saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
