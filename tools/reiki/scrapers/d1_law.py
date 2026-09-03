#!/usr/bin/env python3
"""D1-Law 形式の例規集を取得する downloader。

D1-Law には目次ツリー型のページと OpenSearch 連携型の入口がある。
このモジュールは設定された対象 URL を判定して source HTML を巡回し、
個別例規ページの解析は d1_parser へ渡す。
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

import requests

SCRAPER_DIR = Path(__file__).resolve().parent
MODULE_DIR = SCRAPER_DIR.parent
# batch runner はこのファイルを直接実行する。
# package install を前提にせず、reiki モジュールディレクトリ基準で import できるようにする。
sys.path.append(str(MODULE_DIR))
sys.path.append(str(SCRAPER_DIR))
import d1_parser
import reiki_io
import reiki_targets


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
DELAY = 0.5
# 原典が変わらない限り変換を飛ばす運用では、コードだけ直しても既存成果物へ届かない。
# d1_parser の抽出規則を変えたときは、この値を明示的に上げて保存済み source を変換し直す。
#
# 2 へ上げた理由: 本文を包む `USER-SET-STYLE` が無い取得元で `law-content` が
# 空のまま保存されていた。本番で 4 自治体・約 4,900 件（石狩市 1,246 /
# 京都市 1,156 / 江別市 1,019 / 留寿都村 655）。題名と日付は読めていたので
# 件数では出てこない。原典は変わっていないので、この値を上げないと直らない。
# 3: 新しい Reiki-Base の本文（`div#primary.joubun`）を取り出す。牛久市
#    1,001 件・福岡市 1,136 件が、題名と日付は読めるのに本文が空だった。
PARSER_VERSION = 3
UNPARSED_VERSION = 0
OPENSEARCH_TOP_LEVEL_RE = re.compile(r"mkjG\('([0-9]{3}:[0-9]{2}:[0-9]{2})'\)")
OPENSEARCH_RESULT_RE = re.compile(
    r"doViewJobunFromJsp\('(?P<jctcd>[^']+)',\s*'(?P<houcd>[^']+)',\s*"
    r"(?P<sedno>null|'[^']*'),\s*(?P<sededa>null|'[^']*'),\s*"
    r"'(?P<no>[^']+)',\s*'(?P<total_count>[^']+)',\s*"
    r"(?P<ichikey>null|'[^']*'),\s*'(?P<from_jsp>[^']+)'\)"
)
OPENSEARCH_PAGING_RE = re.compile(r"doPaging\('([0-9]+)'\)")
CATALOG_VERSION_RE = re.compile(
    r"内容現在\s*(?:[：:]\s*)?"
    r"((?:明治|大正|昭和|平成|令和)[0-9０-９元]+年[0-9０-９]+月[0-9０-９]+日)"
)
D1W_REIKI_LINK_RE = re.compile(
    r'''(?:href|src)\s*=\s*["']([^"']*d1w_reiki/[^"']*)["']''',
    re.I,
)


def emit_progress(current: int, total: int, state_path: Path | None = None) -> None:
    if state_path is not None:
        reiki_io.update_progress_state(state_path, current=current, total=total, unit="ordinance")
    print(f"[PROGRESS] unit=ordinance current={max(0, current)} total={max(0, total)}", flush=True)


def response_header(response: requests.Response, name: str) -> str:
    return str(response.headers.get(name, "") or "").strip()


def html_to_text_fragment(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def response_text_auto(response: requests.Response) -> str:
    if not response.encoding or response.encoding.lower() in {"iso-8859-1", "ascii"}:
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def extract_catalog_version_from_html(value: str) -> str:
    text = html_to_text_fragment(value)
    match = CATALOG_VERSION_RE.search(text)
    return match.group(1).strip() if match else ""


def fetch_catalog_version(source_url: str, session: requests.Session | None = None) -> str:
    requester = session or requests
    try:
        response = requester.get(source_url, headers={"User-Agent": USER_AGENT}, timeout=15)
        response.raise_for_status()
    except Exception as exc:
        print(f"[WARN] catalog version fetch failed: {exc}", flush=True)
        return ""
    version = extract_catalog_version_from_html(response_text_auto(response))
    if version == "":
        for href in re.findall(r"""(?:href|src)=["']([^"']*d1w_reiki/reiki\.html?)["']""", response.text, flags=re.I):
            candidate_url = urljoin(response.url, href)
            if candidate_url == response.url:
                continue
            try:
                candidate_response = requester.get(candidate_url, headers={"User-Agent": USER_AGENT}, timeout=15)
                candidate_response.raise_for_status()
            except Exception:
                continue
            version = extract_catalog_version_from_html(response_text_auto(candidate_response))
            if version:
                break
    if version:
        print(f"[INFO] catalog content current: {version}", flush=True)
    else:
        print("[INFO] catalog content current: not found", flush=True)
    return version


# 入口ページの HTML。目次のリンクを読むために使う。取得は 1 回で済ませる。
def fetch_entry_html(source_url: str, session: requests.Session | None = None) -> str:
    requester = session or requests
    try:
        response = requester.get(source_url, headers={"User-Agent": USER_AGENT}, timeout=15)
        response.raise_for_status()
    except Exception as exc:
        print(f"[WARN] entry page fetch failed: {exc}", flush=True)
        return ""
    return response_text_auto(response)


def discover_d1_law_base_url(source_url: str, source_html: str) -> str:
    """入口ページ内の d1w_reiki リンクから実際の静的目次ルートを見つける。"""
    for href in D1W_REIKI_LINK_RE.findall(source_html):
        candidate_url = urljoin(source_url, html.unescape(href))
        try:
            return d1_parser.derive_d1_law_base_url(candidate_url)
        except ValueError:
            continue
    return d1_parser.derive_d1_law_base_url(source_url)


def resolve_d1_law_base_url(source_url: str, session: requests.Session | None = None) -> str:
    direct_base_url = d1_parser.derive_d1_law_base_url(source_url)
    if "/d1w_reiki/" in direct_base_url.lower() or d1_parser.is_opensearch_mokuji_source_url(source_url):
        return direct_base_url

    requester = session or requests
    try:
        response = requester.get(source_url, headers={"User-Agent": USER_AGENT}, timeout=15)
        response.raise_for_status()
        discovered_base_url = discover_d1_law_base_url(response.url, response_text_auto(response))
        if discovered_base_url != direct_base_url:
            print(f"[INFO] Discovered D1-Law base URL: {discovered_base_url}", flush=True)
        return discovered_base_url
    except Exception as exc:
        print(f"[WARN] D1-Law base URL discovery failed; using configured URL: {exc}", flush=True)
        return direct_base_url


# 個票の取得に失敗した URL。失敗を握り潰したまま「確認済み」と数えると、
# 初回なら例規が欠け、更新なら古い本文を現行として固定する。
DOWNLOAD_FAILURES: list[str] = []


def _forget_download_failure(url: str) -> None:
    """無くてもよいページの取得失敗を、個票の失敗から外す。"""
    while url in DOWNLOAD_FAILURES:
        DOWNLOAD_FAILURES.remove(url)


def download_file(
    url,
    dest_path,
    force=False,
    check_updates=False,
    session: requests.Session | None = None,
    previous_manifest: dict | None = None,
):
    existing_path = reiki_io.existing_path(dest_path)
    if not force and existing_path and existing_path.stat().st_size > 0 and not check_updates:
        return (
            False,
            existing_path,
            reiki_io.sha256_path(existing_path),
            {
                "status_code": "",
                "not_modified": False,
                "conditional": False,
                "etag": str((previous_manifest or {}).get("source_etag") or ""),
                "last_modified": str((previous_manifest or {}).get("source_last_modified") or ""),
            },
        )

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        requester = session or requests
        previous_manifest = previous_manifest if isinstance(previous_manifest, dict) else {}
        headers = {"User-Agent": USER_AGENT}
        conditional = False
        if check_updates and not force and existing_path is not None:
            etag = str(previous_manifest.get("source_etag") or "").strip()
            last_modified = str(previous_manifest.get("source_last_modified") or "").strip()
            if etag != "":
                headers["If-None-Match"] = etag
                conditional = True
            if last_modified != "":
                headers["If-Modified-Since"] = last_modified
                conditional = True

        response = requester.get(url, headers=headers, timeout=15)
        if response.status_code == 304 and existing_path is not None:
            print(f"Not modified: {url}")
            return (
                False,
                existing_path,
                str(previous_manifest.get("source_sha256") or "") or reiki_io.sha256_path(existing_path),
                {
                    "status_code": 304,
                    "not_modified": True,
                    "conditional": conditional,
                    "etag": response_header(response, "ETag") or str(previous_manifest.get("source_etag") or ""),
                    "last_modified": response_header(response, "Last-Modified")
                    or str(previous_manifest.get("source_last_modified") or ""),
                },
            )
        response.raise_for_status()
        source_hash = reiki_io.sha256_bytes(response.content)
        metadata = {
            "status_code": response.status_code,
            "not_modified": False,
            "conditional": conditional,
            "etag": response_header(response, "ETag"),
            "last_modified": response_header(response, "Last-Modified"),
        }
        if existing_path and reiki_io.sha256_path(existing_path) == source_hash and not force:
            return False, existing_path, source_hash, metadata
        written_path = reiki_io.write_bytes(dest_path, response.content, compress=True)
        print(f"Downloaded: {url}")
        time.sleep(DELAY)
        return True, written_path, source_hash, metadata
    except Exception as exc:
        print(f"Failed to download {url}: {exc}")
        # 失敗と「既存をそのまま使った」を同じ形で返していたので、
        # 呼ぶ側が区別できず「確認済み」に数えていた。印を付ける。
        DOWNLOAD_FAILURES.append(str(url))
        return (
            False,
            existing_path or dest_path,
            "",
            {
                "download_failed": True,
                "status_code": "",
                "not_modified": False,
                "conditional": False,
                "etag": str((previous_manifest or {}).get("source_etag") or ""),
                "last_modified": str((previous_manifest or {}).get("source_last_modified") or ""),
            },
        )


def index_manifest_by_source(records) -> dict[str, dict]:
    indexed: dict[str, dict] = {}
    if not isinstance(records, list):
        return indexed
    for record in records:
        if not isinstance(record, dict):
            continue
        source_file = str(record.get("source_file") or "").strip()
        if source_file == "":
            stored_source_file = str(record.get("stored_source_file") or "").strip()
            if stored_source_file != "":
                source_file = reiki_io.logical_path(Path(stored_source_file)).name
        if source_file != "":
            indexed[source_file] = record
    return indexed


def first_manifest_catalog_version(records) -> str:
    if not isinstance(records, list):
        return ""
    for record in records:
        if not isinstance(record, dict):
            continue
        value = str(record.get("catalog_content_current") or "").strip()
        if value:
            return value
    return ""


def is_parser_version_current(record: dict | None) -> bool:
    if not isinstance(record, dict):
        return False
    return str(record.get("parser_version", "")).strip() == str(PARSER_VERSION)


def parser_version_for_manifest(
    previous_manifest: dict | None,
    *,
    parse_required: bool,
    parse_succeeded: bool,
) -> int | str:
    previous_manifest = previous_manifest if isinstance(previous_manifest, dict) else {}
    previous_version = previous_manifest.get("parser_version", UNPARSED_VERSION)
    if not parse_required:
        return PARSER_VERSION if is_parser_version_current(previous_manifest) else previous_version
    if parse_succeeded:
        return PARSER_VERSION
    # 新しい source の変換に失敗した場合、前回が現行世代でもその印を残せない。
    # 未変換へ戻しておけば、次の周期が同じ保存 source を必ず再処理する。
    return UNPARSED_VERSION if is_parser_version_current(previous_manifest) else previous_version


# 入口ページが並べる目次への相対リンク。
# 新しい Reiki-Base は `reiki_kana/kana_default.html` のような下位ディレクトリに
# 目次を置く。古い版の `mokuji_index_index.html` を決め打ちしていたので、
# 牛久市・福岡市のように入口は 200 でも目録が 1 件も開けない自治体があった。
# 古い静的版は `href=mokuji_bunya.html` と引用符なしで書く（猿払村）。
MENU_LINK_RE = re.compile(
    r'href=["\']?((?:reiki_[a-z]+/)?[a-z_]*(?:default|index|bunya|kana|taikei|miseko)[a-z_]*\.html)["\'\s>]',
    re.IGNORECASE,
)
# 決め打ちの目次。入口ページからリンクが読めないときの控え。
FALLBACK_MENU_PAGES = ("mokuji_index_index.html", "mokuji_bunya_index.html")


def menu_pages_from_entry(entry_html: str) -> list[str]:
    """入口ページが指している目次ページを、書かれている順で返す。"""
    found: list[str] = []
    for link in MENU_LINK_RE.findall(str(entry_html or "")):
        if link.startswith(("http://", "https://", "/")):
            continue
        if link not in found:
            found.append(link)
    return found


def get_hno_list(base_url, data_dir, force=False, check_updates=False, walk=None, entry_html=""):
    """目録を辿って例規 ID を集める。

    `walk` を渡すと、開けなかった目録ページの数を控える。枝が落ちると
    その先の例規がまるごと見えなくなるのに、これまで数えていなかった。
    """
    hno_set = set()
    # 新しい Reiki-Base の本文。ID ではなく目次からの相対パスで持つ。
    honbun_paths: set[str] = set()
    missed_pages: list[str] = []

    print("Fetching index pages...")
    # 入口ページが目次を指しているなら、それを使う。指していなければ決め打ち。
    declared_menus = menu_pages_from_entry(entry_html)
    to_scan = list(declared_menus)
    # 決め打ちの名前は必ず試す。入口が指している `mokuji_bunya.html` が中身の無い
    # 334 バイトで、実体は `mokuji_bunya_index.html` にある取得元がある（川内村）。
    # ただし**無いこと自体は失敗ではない**。数えてしまうと、石狩・江別・京都のように
    # 1267/1267 取れているのに毎周回失敗になる。
    optional_menus = {name for name in FALLBACK_MENU_PAGES if name not in to_scan}
    to_scan.extend(sorted(optional_menus))
    # 推測に頼ったかどうかを残す。取得元が名前を変えたとき、次に壊れるのは
    # 推測で拾えている自治体である。事前に一覧で見えるようにしておく。
    if not declared_menus:
        print("[WARN] 入口ページに目次リンクがありません。決め打ちの名前で辿ります。")
    optional_missing: set[str] = set()
    for name in list(to_scan):
        download_file(base_url + name, data_dir / name, force=force, check_updates=check_updates)

    scanned = set()

    while to_scan:
        current = to_scan.pop(0)
        if current in scanned:
            continue
        scanned.add(current)

        file_path = data_dir / current
        stored_path = reiki_io.existing_path(file_path)
        if stored_path is None:
            _, stored_path, _, _ = download_file(base_url + current, file_path, check_updates=check_updates)
        if stored_path is None or not stored_path.exists():
            if current in optional_menus:
                # 決め打ちの名前が無かっただけ。個票の失敗にも数えない。
                _forget_download_failure(base_url + current)
                optional_missing.add(current)
                continue
            # 目録の枝が開けない。その先の例規はまるごと見えなくなる。
            missed_pages.append(current)
            continue

        try:
            content = reiki_io.read_text_auto(stored_path)
        except Exception as exc:
            print(f"Error reading {stored_path}: {exc}")
            missed_pages.append(current)
            continue

        # 目次の枝。名前は取得元ごとに違う（`bunya_01.html` `r_taikei_01.html`
        # `r_50_a.html`）。決め打ちせず、目次に書かれている相対リンクを辿る。
        # 同じディレクトリの html だけを見る。上位や別サイトへは出ない。
        prefix = current.rsplit("/", 1)[0] + "/" if "/" in current else ""
        for link in re.findall(r'href="([A-Za-z0-9_.-]+\.html)"', content):
            branch = prefix + link
            if branch in scanned or branch in to_scan:
                continue
            # 本文ページは目次ではない。目録として開くと無駄に取りに行く。
            if link.endswith("_j.html"):
                continue
            to_scan.append(branch)

        for hno in re.findall(r"OpenResDataWin\('([^']+)'\)", content):
            hno_set.add(hno)

        # 例規を JavaScript ではなく普通のリンクで並べる取得元がある
        # （京都市・留寿都村）。H…/H…_j.html の形なので、ディレクトリ名を
        # そのまま例規 ID として拾う。
        for hno in re.findall(r'href="([A-Za-z0-9]+)/\1_j\.html"', content):
            hno_set.add(hno)

        # 新しい Reiki-Base は本文を `../reiki_honbun/x000RG….html` に置く。
        # `{id}/{id}_j.html` とは形が違うので、組み立て直さず相対パスで控える。
        for path in re.findall(r'href="([^"]*reiki_honbun/[A-Za-z0-9]+\.html)"', content):
            honbun_paths.add(urljoin(prefix, path))

    if walk is not None:
        walk.update(
            {
                "scanned_pages": len(scanned),
                # 決め打ちの名前が全部無くても、宣言された目次から集められていれば
                # 取りこぼしではない。1 件も集まらなかったときだけ数える。
                "missed_pages": len(missed_pages) + (len(optional_missing) if not hno_set and not honbun_paths else 0),
                "missed_examples": (missed_pages + (sorted(optional_missing) if not hno_set and not honbun_paths else []))[:10],
                # 目次を入口ページから読めたか。読めていないなら、決め打ちの
                # 名前に頼っている。取得元が名前を変えれば次に壊れる。
                "menus_declared": len(declared_menus),
                "guessed_menu": not declared_menus,
            }
        )
    # 相対パス形が見つかったなら、そちらが本文の正しい場所である。
    if honbun_paths:
        return sorted(honbun_paths)
    return sorted(hno_set)


def normalize_source_url(source_url: str) -> str:
    parts = urlsplit(source_url.strip())
    path = parts.path
    if d1_parser.is_opensearch_mokuji_source_url(source_url):
        path = re.sub(r"/opensearch/Sr[A-Za-z0-9]+/init$", "/opensearch/SrMjF01/init", path)
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def normalize_js_value(raw_value: str) -> str:
    value = str(raw_value).strip()
    if value.lower() == "null":
        return ""
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    return value


def build_opensearch_detail_url(site_root: str, entry: dict[str, str]) -> str:
    query = {
        "jctcd": entry["jctcd"],
        "houcd": entry["houcd"],
        "no": entry["no"],
        "totalCount": entry["total_count"],
        "fromJsp": entry["from_jsp"],
    }
    if entry["sedno"] != "":
        query["sedno"] = entry["sedno"]
    if entry["sededa"] != "":
        query["sededa"] = entry["sededa"]
    if entry["ichikey"] != "":
        query["ichikey"] = entry["ichikey"]
    return f"{site_root}/opensearch/SrJbF01/init?{urlencode(query)}"


def fetch_opensearch_pages(
    session: requests.Session,
    *,
    source_url: str,
    site_root: str,
    mokujicd: str,
) -> list[str]:
    referer = source_url
    search_data = {
        "typeSearch": "SrMj_Genko",
        "typeSearchFacet": "SrMj_Genko",
        "mokujicd": mokujicd,
        "haishiyear": "",
        "saveHistory": "false",
        "initialLevel": "1",
        "listSort": "D",
        "mishikouJbnHide": "false",
        "downloadname": "",
    }
    search_url = f"{site_root}/opensearch/SrMjF01/search"
    response = session.post(search_url, headers={"User-Agent": USER_AGENT, "Referer": referer}, data=search_data, timeout=15)
    response.raise_for_status()
    pages = [response.text]

    for offset in sorted({int(value) for value in OPENSEARCH_PAGING_RE.findall(response.text)}):
        paging_url = f"{site_root}/opensearch/SrMjF01/paging?{urlencode({'offset': offset})}"
        paging_response = session.get(
            paging_url,
            headers={"User-Agent": USER_AGENT, "Referer": search_url},
            timeout=15,
        )
        paging_response.raise_for_status()
        pages.append(paging_response.text)

    return pages


def collect_opensearch_entries(source_url: str) -> tuple[requests.Session, list[dict[str, str]]]:
    # OpenSearch 型 D1-Law は静的な目次ページではなく、ページングされた検索結果から例規を拾う。
    # 詳細取得でも探索時の cookie を使えるよう、確立済み session も返す。
    normalized_source_url = normalize_source_url(source_url)
    parts = urlsplit(normalized_source_url)
    site_root = urlunsplit((parts.scheme or "https", parts.netloc, "", "", "")).rstrip("/")
    session = requests.Session()
    init_response = session.get(normalized_source_url, headers={"User-Agent": USER_AGENT}, timeout=15)
    init_response.raise_for_status()

    top_level_codes = []
    seen_codes = set()
    # 最上位カテゴリだけで現行例規の範囲を分割できるため、
    # 全ての入れ子 node を歩かなくても catalog 全体を覆える。
    for mokujicd in OPENSEARCH_TOP_LEVEL_RE.findall(init_response.text):
        if mokujicd in seen_codes:
            continue
        seen_codes.add(mokujicd)
        top_level_codes.append(mokujicd)

    if not top_level_codes:
        raise ValueError(f"No top-level mokujicd found in opensearch page: {source_url}")

    print(f"Discovered {len(top_level_codes)} top-level opensearch categories.", flush=True)

    entries_by_houcd: dict[str, dict[str, str]] = {}
    for index, mokujicd in enumerate(top_level_codes, start=1):
        pages = fetch_opensearch_pages(
            session,
            source_url=normalized_source_url,
            site_root=site_root,
            mokujicd=mokujicd,
        )
        page_entry_count = 0
        for page in pages:
            for match in OPENSEARCH_RESULT_RE.finditer(page):
                entry = {
                    "jctcd": normalize_js_value(match.group("jctcd")),
                    "houcd": normalize_js_value(match.group("houcd")),
                    "sedno": normalize_js_value(match.group("sedno")),
                    "sededa": normalize_js_value(match.group("sededa")),
                    "no": normalize_js_value(match.group("no")),
                    "total_count": normalize_js_value(match.group("total_count")),
                    "ichikey": normalize_js_value(match.group("ichikey")),
                    "from_jsp": normalize_js_value(match.group("from_jsp")),
                    "mokujicd": mokujicd,
                }
                entry["detail_url"] = build_opensearch_detail_url(site_root, entry)
                entries_by_houcd.setdefault(entry["houcd"], entry)
                page_entry_count += 1
        print(
            f"[INFO] opensearch category {index}/{len(top_level_codes)} {mokujicd}: "
            f"{page_entry_count} hits / {len(entries_by_houcd)} unique",
            flush=True,
        )

    return session, list(entries_by_houcd.values())


def build_source_plan(
    *,
    source_items,
    base_url: str,
    source_dir: Path,
    html_dir: Path,
    markdown_dir: Path,
    opensearch_session: requests.Session | None,
    previous_manifest_by_source: dict[str, dict],
) -> tuple[list[dict], int]:
    # source ファイル名にはタイトルではなく、D1-Law 側の安定識別子を使う。
    # タイトルは変わることがあるが、この識別子は詳細ページ参照と再開判定に使われる。
    plans = []
    incomplete_count = 0
    for source_item in source_items:
        if isinstance(source_item, dict):
            code = str(source_item["houcd"])
            url = str(source_item["detail_url"])
            filename = f"{code}_j.html"
            session = opensearch_session
        else:
            code = str(source_item)
            if code.endswith(".html"):
                # 目次から拾った相対パス（`reiki_honbun/z500RG00000122.html`）。
                # 保存名は末尾の識別子だけにして、従来と同じ形に揃える。
                url = urljoin(base_url, code)
                filename = f"{Path(code).stem}_j.html"
            else:
                filename = f"{code}_j.html"
                url = f"{base_url}{code}/{filename}"
            session = None

        dest_path = source_dir / filename
        existing_source_path = reiki_io.existing_path(dest_path)
        source_file_path = existing_source_path or reiki_io.gzip_path(dest_path)
        logical_source = reiki_io.logical_path(source_file_path)
        html_output = html_dir / f"{logical_source.stem}.html"
        markdown_output = reiki_io.existing_path(markdown_dir / f"{logical_source.stem}.md")
        previous_manifest = previous_manifest_by_source.get(filename)
        previous_manifest = previous_manifest if isinstance(previous_manifest, dict) else {}
        has_source = existing_source_path is not None and existing_source_path.stat().st_size > 0
        outputs_missing = not html_output.exists() or markdown_output is None
        parser_outdated = not is_parser_version_current(previous_manifest)
        is_incomplete = not has_source or outputs_missing
        if is_incomplete:
            incomplete_count += 1
        plans.append(
            {
                "source_item": source_item,
                "code": code,
                "url": url,
                "filename": filename,
                "session": session,
                "dest_path": dest_path,
                "source_file_path": source_file_path,
                "html_output": html_output,
                "markdown_output": markdown_output,
                "previous_manifest": previous_manifest,
                "has_source": has_source,
                "outputs_missing": outputs_missing,
                "parser_outdated": parser_outdated,
                "is_incomplete": is_incomplete,
            }
        )
    return plans, incomplete_count


def assign_work_mode(
    plans: list[dict],
    *,
    force: bool,
    check_updates: bool,
    catalog_changed: bool | None = None,
) -> dict[str, int | bool]:
    total = len(plans)
    incomplete_count = sum(1 for plan in plans if bool(plan["is_incomplete"]))
    parser_outdated_count = sum(1 for plan in plans if bool(plan["parser_outdated"]))
    resume_mode = not force and incomplete_count > 0
    update_mode = not force and not resume_mode and check_updates and catalog_changed is not False
    for plan in plans:
        # parser 世代だけが古いときに個票へ問い合わせると、原典不変の全件を無駄に取得する。
        # 保存 source がある計画は変換だけを仕事にし、取得は source 不在か更新確認時に限る。
        plan["should_fetch"] = bool(force or not plan["has_source"] or update_mode)
        plan["should_work"] = bool(
            plan["should_fetch"] or plan["outputs_missing"] or plan["parser_outdated"]
        )
    work_count = sum(1 for plan in plans if plan["should_work"])
    parser_reparse_only_count = sum(
        1 for plan in plans if plan["parser_outdated"] and not plan["should_fetch"]
    )
    return {
        "total": total,
        "incomplete_count": incomplete_count,
        "parser_outdated_count": parser_outdated_count,
        "parser_reparse_only_count": parser_reparse_only_count,
        "resume_mode": resume_mode,
        "update_mode": update_mode,
        "catalog_changed": catalog_changed,
        "work_count": work_count,
        "progress_base": max(0, total - work_count),
    }


def fetch_source_for_plan(
    plan: dict,
    *,
    force: bool,
    update_mode: bool,
) -> tuple[bool, Path, str, dict]:
    previous_manifest = plan["previous_manifest"] if isinstance(plan.get("previous_manifest"), dict) else {}
    source_file_path = Path(plan["source_file_path"])
    source_hash = str(previous_manifest.get("source_sha256") or "")
    metadata = {
        "status_code": "",
        "not_modified": False,
        "conditional": False,
        "etag": str(previous_manifest.get("source_etag") or ""),
        "last_modified": str(previous_manifest.get("source_last_modified") or ""),
    }
    if not bool(plan["should_fetch"]):
        if source_hash == "" and source_file_path.exists():
            source_hash = reiki_io.sha256_path(source_file_path)
        return False, source_file_path, source_hash, metadata
    return download_file(
        str(plan["url"]),
        plan["dest_path"],
        force=force,
        check_updates=update_mode,
        session=plan["session"],
        previous_manifest=previous_manifest,
    )


def parse_source_for_plan(
    plan: dict,
    source_file_path: Path,
    *,
    downloaded: bool,
    force: bool,
    markdown_dir: Path,
    html_dir: Path,
    base_url: str,
    images_dir: Path,
    image_public_url: str,
) -> tuple[bool, bool]:
    parse_required = bool(
        downloaded or force or plan["outputs_missing"] or plan["parser_outdated"]
    )
    if not parse_required:
        return False, False
    if not source_file_path.exists():
        return True, False
    # process_file 自身にも mtime による省略がある。世代不一致でここへ来たのに
    # 旧成果物を再利用しないよう、必要と判断した変換は常に強制する。
    parse_succeeded = d1_parser.process_file(
        source_file_path,
        markdown_dir,
        html_dir,
        base_url=base_url,
        images_dir=images_dir,
        image_public_url=image_public_url,
        force=True,
    )
    return True, bool(parse_succeeded)


# d1_law.py が扱える system_type。reiki.html は D1-Law の静的書き出し版で、
# 目次（OpenResDataWin/bunya_*.html）も本文（{hno}/{hno}_j.html）も同一構造のため
# 同じ downloader/parser で処理できる。
SUPPORTED_D1_SYSTEMS = {"d1-law", "reiki.html", "reiki_menu", "h-chosonkai"}


def main():
    default_slug = reiki_targets.default_slug_for_system("d1-law")
    parser = argparse.ArgumentParser(description="Download ordinances from D1-Law (and reiki.html static) systems.")
    parser.add_argument("--slug", default=default_slug, help="Municipality slug resolved from data/municipalities")
    parser.add_argument("--system-type", default="", help="d1-law または reiki.html（未指定なら slug から判定）")
    parser.add_argument("--force", action="store_true", help="Redownload source HTML and rebuild outputs")
    parser.add_argument("--check-updates", action="store_true", help="既存条例も再取得して更新を確認する")
    args = parser.parse_args()

    expected = args.system_type.strip() or None
    target = reiki_targets.load_reiki_target(args.slug, expected_system=expected)
    if str(target.get("system_type")) not in SUPPORTED_D1_SYSTEMS:
        raise ValueError(
            f"d1_law.py は system_type={target.get('system_type')!r} を扱えません "
            f"(対応: {sorted(SUPPORTED_D1_SYSTEMS)})"
        )
    base_url = resolve_d1_law_base_url(str(target["source_url"]))
    source_dir = target["source_dir"]
    markdown_dir = target["markdown_dir"]
    html_dir = target["html_dir"]
    images_dir = target["image_dir"]
    image_public_url = target["image_public_url"]
    work_root = Path(target["work_root"])
    manifest_path = work_root / "source_manifest.json.gz"
    # 走っている最中の一覧は正本と分ける。1 件ごとに正本を上書きすると、
    # 走り始めた瞬間に縮み、途中で死ねばそのまま残る。
    partial_manifest_path = work_root / "source_manifest.partial.json.gz"
    state_path = work_root / "scrape_state.json"
    classification_dir = target["classification_dir"]

    print(f"Target: {target['name']} ({target['slug']}, {target['system_type']})")
    print(f"Source URL: {target['source_url']}")
    print(f"Base URL: {base_url}")
    print(f"Target directory: {source_dir}")
    source_dir.mkdir(parents=True, exist_ok=True)

    catalog_version = fetch_catalog_version(str(target["source_url"]))
    # 入口ページが目次を指している。決め打ちの `mokuji_index_index.html` は
    # 古い Reiki-Base のもので、牛久市・福岡市では 404 になる。
    entry_html = fetch_entry_html(str(target["source_url"]))
    opensearch_session: requests.Session | None = None
    hno_list: list[str] = []
    opensearch_entries: list[dict[str, str]] = []
    # 目録の歩き具合。OpenSearch 型は目録を歩かないので空のまま。分岐の中で
    # 作ると、OpenSearch 型が終了処理で未定義参照になって落ちる（179 自治体）。
    catalog_walk: dict = {}
    if d1_parser.is_opensearch_mokuji_source_url(str(target["source_url"])):
        opensearch_session, opensearch_entries = collect_opensearch_entries(str(target["source_url"]))
        print(f"Found {len(opensearch_entries)} unique opensearch regulations.")
        if not opensearch_entries:
            raise RuntimeError(
                "No opensearch regulations were collected; refusing to mark the target as successfully scraped."
            )
    else:
        hno_list = get_hno_list(
            base_url,
            source_dir,
            force=args.force,
            check_updates=args.check_updates,
            walk=catalog_walk,
            entry_html=entry_html,
        )
        print(f"Found {len(hno_list)} unique regulation IDs.")

    total_regulations = len(opensearch_entries) if opensearch_entries else len(hno_list)
    source_items = opensearch_entries if opensearch_entries else hno_list
    if total_regulations <= 0:
        raise RuntimeError(
            "No regulations were collected; refusing to mark the target as successfully scraped. "
            f"source={target['source_url']} base={base_url}"
        )

    previous_manifest_records = reiki_io.load_json(manifest_path, [])
    previous_catalog_version = first_manifest_catalog_version(previous_manifest_records)
    if catalog_version == "":
        catalog_changed: bool | None = None
    else:
        catalog_changed = previous_catalog_version == "" or previous_catalog_version != catalog_version
    if previous_catalog_version != "" and catalog_version != "":
        status_label = "changed" if catalog_changed else "unchanged"
        print(
            f"[INFO] catalog content current {status_label}: "
            f"previous={previous_catalog_version} current={catalog_version}",
            flush=True,
        )

    previous_manifest_by_source = index_manifest_by_source(previous_manifest_records)
    plans, _ = build_source_plan(
        source_items=source_items,
        base_url=base_url,
        source_dir=source_dir,
        html_dir=html_dir,
        markdown_dir=markdown_dir,
        opensearch_session=opensearch_session,
        previous_manifest_by_source=previous_manifest_by_source,
    )
    work_mode = assign_work_mode(
        plans,
        force=args.force,
        check_updates=args.check_updates,
        catalog_changed=catalog_changed,
    )
    incomplete_count = int(work_mode["incomplete_count"])
    parser_outdated_count = int(work_mode["parser_outdated_count"])
    parser_reparse_only_count = int(work_mode["parser_reparse_only_count"])
    resume_mode = bool(work_mode["resume_mode"])
    update_mode = bool(work_mode["update_mode"])
    work_count = int(work_mode["work_count"])
    if resume_mode:
        print(f"[MODE] resume missing ordinances only: {incomplete_count}/{total_regulations}", flush=True)
    elif update_mode:
        print(f"[MODE] update check: {total_regulations}/{total_regulations}", flush=True)
    elif args.force:
        print(f"[MODE] force rebuild: {total_regulations}/{total_regulations}", flush=True)
    elif args.check_updates and catalog_changed is False:
        print("[MODE] catalog unchanged; update check skipped.", flush=True)
    else:
        print("[MODE] complete; no update check requested.", flush=True)
    if parser_outdated_count:
        print(
            f"[MODE] parser generation {PARSER_VERSION}: rebuild "
            f"{parser_outdated_count}/{total_regulations}; "
            f"saved-source only {parser_reparse_only_count}",
            flush=True,
        )

    progress_base = int(work_mode["progress_base"])
    emit_progress(progress_base, total_regulations, state_path)

    downloaded_count = 0
    checked_count = 0
    not_modified_count = 0
    conditional_count = 0
    parsed_count = 0
    reparsed_saved_count = 0
    parse_failure_count = 0
    parse_failure_urls: list[str] = []
    skipped_count = 0
    processed_work_count = 0
    manifest_entries = []
    for index, plan in enumerate(plans):
        source_item = plan["source_item"]
        code = str(plan["code"])
        url = str(plan["url"])
        previous_manifest = plan["previous_manifest"] if isinstance(plan["previous_manifest"], dict) else {}
        should_work = bool(plan["should_work"])
        should_fetch = bool(plan["should_fetch"])
        downloaded, source_file_path, source_hash, metadata = fetch_source_for_plan(
            plan,
            force=args.force,
            update_mode=update_mode,
        )
        if should_fetch:
            if metadata.get("conditional"):
                conditional_count += 1
            if metadata.get("not_modified"):
                not_modified_count += 1
            if downloaded:
                downloaded_count += 1
            elif update_mode:
                checked_count += 1
        if not should_work:
            skipped_count += 1

        logical_source = reiki_io.logical_path(source_file_path)
        parse_required, parse_succeeded = parse_source_for_plan(
            plan,
            source_file_path,
            downloaded=downloaded,
            force=args.force,
            markdown_dir=markdown_dir,
            html_dir=html_dir,
            base_url=base_url,
            images_dir=images_dir,
            image_public_url=image_public_url,
        )
        if parse_required:
            if parse_succeeded:
                parsed_count += 1
                if plan["parser_outdated"] and not downloaded:
                    reparsed_saved_count += 1
            else:
                if url not in DOWNLOAD_FAILURES:
                    parse_failure_count += 1
                    parse_failure_urls.append(url)

        manifest_entries.append(
            {
                "code": code,
                "detail_url": url,
                "source_file": logical_source.name,
                "stored_source_file": source_file_path.name,
                "source_sha256": source_hash or (reiki_io.sha256_path(source_file_path) if source_file_path.exists() else ""),
                "parser_version": parser_version_for_manifest(
                    previous_manifest,
                    parse_required=parse_required,
                    parse_succeeded=parse_succeeded,
                ),
                "source_etag": str(metadata.get("etag") or ""),
                "source_last_modified": str(metadata.get("last_modified") or ""),
                "source_http_status": str(metadata.get("status_code") or ""),
                "source_not_modified": bool(metadata.get("not_modified")),
                "source_conditional_request": bool(metadata.get("conditional")),
                "catalog_content_current": catalog_version,
                "checked_updates": bool(args.check_updates),
            }
        )
        if isinstance(source_item, dict):
            manifest_entries[-1]["mokujicd"] = str(source_item.get("mokujicd", ""))

        if ((index + 1) % 25) == 0 or (index + 1) == total_regulations:
            # 途中停止しても後追い補完が source_url 等を復元できるよう、定期保存する。
            # 正本ではなく途中経過へ書く（正本を縮めないため）。
            reiki_io.write_json(partial_manifest_path, manifest_entries, compress=True)
        if should_work:
            processed_work_count += 1
            emit_progress(progress_base + processed_work_count, total_regulations, state_path)

    # 正本を書けたら途中経過は要らない。
    try:
        existing_partial = reiki_io.existing_path(partial_manifest_path)
        if existing_partial is not None:
            existing_partial.unlink()
    except Exception:
        pass
    # 目録・取得・変換のどこかが欠けた実行を完了扱いすると、索引更新が古い成果物を拾う。
    missed_pages = int(catalog_walk.get("missed_pages") or 0)
    # d1-law に --limit は無いので、目録を開けたかと個票・変換の失敗で判断する。
    detail_failures = len(DOWNLOAD_FAILURES)
    total_failures = detail_failures + parse_failure_count
    walk_complete = missed_pages == 0 and total_failures == 0
    manifest_result = reiki_io.write_manifest_guarded(
        manifest_path,
        manifest_entries,
        label=f"{target['name']}の例規一覧",
        walk_complete=walk_complete,
    )
    reiki_io.save_source_coverage(
        work_root,
        {
            "version": 2,
            "kind": "catalog",
            "declares": True,
            "observed_at": time.strftime("%Y%m%d_%H%M%S"),
            "declared_total": total_regulations,
            "scanned_pages": int(catalog_walk.get("scanned_pages") or 0),
            "menus_declared": int(catalog_walk.get("menus_declared") or 0),
            "guessed_menu": bool(catalog_walk.get("guessed_menu")),
            "missed_pages": missed_pages,
            "missed_examples": catalog_walk.get("missed_examples") or [],
            "failed": total_failures,
            "failed_examples": (DOWNLOAD_FAILURES + parse_failure_urls)[:10],
            "collected": len(manifest_entries),
            "manifest_shrunk": not manifest_result["written"],
            "manifest_previous": manifest_result["previous"],
            "complete": walk_complete and manifest_result["written"],
        },
    )
    if missed_pages:
        print(
            f"[WARN] 目録のページを {missed_pages} 件開けませんでした。"
            "その先の例規は見えていません。",
            flush=True,
        )
    if detail_failures:
        print(
            f"[WARN] 例規本体を {detail_failures} 件取得できませんでした。",
            flush=True,
        )
    if parse_failure_count:
        print(
            f"[WARN] 保存 source から {parse_failure_count} 件を変換できませんでした。"
            "parser_version は進めず、次の周期で再試行します。",
            flush=True,
        )
    print(f"Finished. Downloaded {downloaded_count} files.")
    print(f"Checked existing: {checked_count}")
    print(f"Conditional requests: {conditional_count}")
    print(f"Not modified (304): {not_modified_count}")
    print(f"Skipped existing: {skipped_count}")
    print(f"Parsed outputs: {parsed_count}")
    print(f"Reparsed saved sources: {reparsed_saved_count}")
    print(f"Parser generation: {PARSER_VERSION}")
    print(f"Manifest: {manifest_path}")
    if parsed_count and walk_complete and manifest_result["written"]:
        print(
            f"[INDEX-REQUIRED] slug={target['slug']} doc_type=reiki parsed={parsed_count} "
            "clean HTML/Markdown を再生成したため、例規索引を更新してください。",
            flush=True,
        )
    if opensearch_session is not None:
        opensearch_session.close()


if __name__ == "__main__":
    main()
