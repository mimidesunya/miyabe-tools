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
import ssl
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

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


class _LegacyTlsAdapter(HTTPAdapter):
    """古い暗号スイートしか話さない取得元に繋ぐ。

    legal-square（`*.legal-square.com`）は OpenSSL 3 の既定（SECLEVEL 2）では
    `SSLV3_ALERT_HANDSHAKE_FAILURE` で繋がらない。ブラウザ（スクレイパの
    Playwright）は繋がるので、探索だけが入口を確かめられずにいた。
    """

    def init_poolmanager(self, *args, **kwargs):
        context = ssl.create_default_context()
        context.set_ciphers("DEFAULT:@SECLEVEL=1")
        context.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        kwargs["ssl_context"] = context
        return super().init_poolmanager(*args, **kwargs)


LEGACY_TLS_HOST_SUFFIXES = (".legal-square.com",)
_legacy_session: requests.Session | None = None


def session_for(session: requests.Session, url: str) -> requests.Session:
    global _legacy_session
    host = (urlsplit(url).hostname or "").lower()
    if not any(host.endswith(suffix) for suffix in LEGACY_TLS_HOST_SUFFIXES):
        return session
    if _legacy_session is None:
        _legacy_session = requests.Session()
        _legacy_session.mount("https://", _LegacyTlsAdapter())
    return _legacy_session


def fetch(session: requests.Session, url: str, *, referer: str = "") -> tuple[int, str, str]:
    """(HTTP 状態, 最終 URL, 本文)。失敗は状態 0。"""
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.8"}
    if referer:
        headers["Referer"] = referer
    session = session_for(session, url)
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


def looks_like_error_page(html: str) -> bool:
    """取得元が HTTP 200 で返すエラー画面。

    d1-law の opensearch は契約の切れた（移転した）自治体でも 200 で
    「システムエラーが発生しました」を返す。URL に `/opensearch/` があるだけで
    入口と認めると、死んだ入口を正しいと判定してしまう（御前崎市、2026-09）。
    """
    if not html:
        return False
    title = re.search(r"<title>\s*(.*?)\s*</title>", html, re.I | re.S)
    if title and title.group(1).strip() in ("エラー", "システムエラー"):
        return True
    return "システムエラーが発生しました" in html


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
        if looks_like_error_page(html):
            return url, "ng", f"{status} エラーページ"
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
    # 4. 公式サイトから辿れなければ RILG の全国例規集リンク集を引く。
    #    小さな町村や移転直後の自治体は、サイトに例規集へのリンクを置いていない。
    for link in rilg_candidates(session, finding.code, finding.name):
        system = detect_from_url(link)
        if not system:
            continue
        other = registered_elsewhere(link, finding.code)
        if other:
            # RILG には同名の別自治体の URL が載っていることがある（奈良県川上村に
            # 長野県川上村の例規集、など）。他の自治体で使っている入口は採らない。
            if best is None:
                best = (system, link, f"rilg: {other} と同じ URL")
            continue
        entry, ok, evidence = verify_entry(session, system, link)
        time.sleep(pause)
        if ok == "ok":
            finding.detected_system, finding.entry_url, finding.verified = system, entry, ok
            finding.evidence = f"rilg / {evidence}"
            return finding
        if best is None:
            best = (system, link, f"rilg ({evidence})")
    # 5. g-reiki は置き場の名前がローマ字の自治体名で決まる。公式サイトにも
    #    RILG にも載っていない村の例規集がここにあった（姫島村、2026-09）。
    for link in guessed_g_reiki_urls(finding.code):
        if registered_elsewhere(link, finding.code):
            continue
        entry, ok, evidence = verify_entry(session, "g-reiki", link)
        time.sleep(pause)
        if ok != "ok":
            continue
        # 推測した URL は同じ読みの別の自治体を掴みうる。体系目次に名前が出るかを見る。
        if finding.name and not names_municipality(session, link, finding.name):
            if best is None:
                best = ("g-reiki", link, f"guess: {finding.name} の名前が体系目次に無い")
            continue
        finding.detected_system, finding.entry_url, finding.verified = "g-reiki", entry, ok
        finding.evidence = f"guess / {evidence}"
        return finding
    if best is not None:
        finding.detected_system, finding.entry_url, finding.verified = best[0], best[1], "ng"
        finding.evidence = best[2]
    else:
        finding.note = "製品の印も例規集のリンクも見つからない"
    return finding


RILG_LINK_URL = "https://www.rilg.or.jp/htdocs/main/zenkoku_reiki/zenkoku_link.html"
_rilg_cache: dict[str, dict[str, list[str]]] = {}


def parse_rilg_links(html: str) -> dict[str, dict[str, list[str]]]:
    """RILG のリンク集を {都道府県コード: {自治体名: [URL]}} にする。

    都道府県ごとに `<a name="29">` のアンカーで区切られている。コメントアウト
    された旧リンクは読まない。リンクの無い自治体（名前だけの td）は載せない。
    """
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    sections: dict[str, dict[str, list[str]]] = {}
    parts = re.split(r"""<a\s+name=["']?(\d{2})["']?\s*>\s*</a>""", html, flags=re.I)
    # parts = [前置き, "01", 本文, "02", 本文, ...]
    for index in range(1, len(parts) - 1, 2):
        pref = parts[index]
        names: dict[str, list[str]] = {}
        for href, text in re.findall(r"""<a\s[^>]*href=["']([^"']+)["'][^>]*>(.*?)</a>""", parts[index + 1], flags=re.I | re.S):
            label = re.sub(r"<[^>]+>|\s+", "", text)
            if not label or not href.startswith("http"):
                continue
            names.setdefault(label, []).append(href.strip())
        sections[pref] = names
    return sections


def rilg_candidates(session: requests.Session, code: str, name: str) -> list[str]:
    """RILG のリンク集に載っている、その自治体の例規集 URL。"""
    if not code or not name:
        return []
    if "sections" not in _rilg_cache:
        status, _final, html = fetch(session, RILG_LINK_URL)
        _rilg_cache["sections"] = parse_rilg_links(html) if status == 200 else {}
    return list(_rilg_cache["sections"].get(code[:2], {}).get(name, []))


_romaji_cache: dict[str, str] = {}
ROMAJI_SUFFIX_RE = re.compile(r"-(shi|ku|machi|cho|mura|son)$")
G_REIKI_PREFIX = {"shi": "city.", "ku": "city.", "machi": "town.", "cho": "town.", "mura": "vill.", "son": "vill."}


def guessed_g_reiki_urls(code: str) -> list[str]:
    """ローマ字の自治体名から、g-reiki の置き場になりうる URL を作る。

    実例は `himeshima/`・`vill.ginoza/`・`town.heguri.nara/`・`city.beppu/` など。
    県名付き（`town.heguri.nara`）は組み合わせが増えるので試さない。
    """
    if not _romaji_cache:
        try:
            with open(MUNI_DIR / "municipality_master.tsv", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle, delimiter="\t"):
                    _romaji_cache[str(row.get("jis_code") or "")] = str(row.get("name_romaji") or "").strip().lower()
        except OSError:
            return []
    romaji = _romaji_cache.get(code, "")
    match = ROMAJI_SUFFIX_RE.search(romaji)
    if not romaji or not match:
        return []
    base = romaji[: match.start()].replace("-", "")
    if not re.fullmatch(r"[a-z]+", base):
        return []
    return [
        f"https://www1.g-reiki.net/{base}/reiki_menu.html",
        f"https://www1.g-reiki.net/{G_REIKI_PREFIX[match.group(1)]}{base}/reiki_menu.html",
    ]


def names_municipality(session: requests.Session, url: str, name: str) -> bool:
    """体系目次の最初の編（総則）に自治体名が出るか。"""
    first = urljoin(_dir_of(url), "reiki_taikei/r_taikei_01.html")
    status, _final, html = fetch(session, first)
    return status == 200 and name in html


def _url_key(url: str) -> str:
    parts = urlsplit(url.strip())
    return f"{parts.netloc.lower()}{parts.path.rstrip('/')}?{parts.query}"


def registered_elsewhere(url: str, code: str) -> str:
    """同じ URL を別の自治体が台帳で使っていれば、その jis_code。"""
    key = _url_key(url)
    try:
        with open(MUNI_DIR / "reiki_system_urls.tsv", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                other = str(row.get("jis_code") or "")
                registered = str(row.get("url") or "")
                if other and other != code and registered and _url_key(registered) == key:
                    return other
    except OSError:
        return ""
    return ""


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
