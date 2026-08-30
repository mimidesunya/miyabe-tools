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


def get_hno_list(base_url, data_dir, force=False, check_updates=False, walk=None):
    """目録を辿って例規 ID を集める。

    `walk` を渡すと、開けなかった目録ページの数を控える。枝が落ちると
    その先の例規がまるごと見えなくなるのに、これまで数えていなかった。
    """
    hno_set = set()
    missed_pages: list[str] = []

    print("Fetching index pages...")
    download_file(base_url + "mokuji_index_index.html", data_dir / "mokuji_index_index.html", force=force, check_updates=check_updates)
    download_file(base_url + "mokuji_bunya_index.html", data_dir / "mokuji_bunya_index.html", force=force, check_updates=check_updates)

    to_scan = ["mokuji_index_index.html", "mokuji_bunya_index.html"]
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
            # 目録の枝が開けない。その先の例規はまるごと見えなくなる。
            missed_pages.append(current)
            continue

        try:
            content = reiki_io.read_text_auto(stored_path)
        except Exception as exc:
            print(f"Error reading {stored_path}: {exc}")
            missed_pages.append(current)
            continue

        for link in re.findall(r"(index_\d+\.html|bunya_\d+\.html)", content):
            if link not in scanned:
                to_scan.append(link)

        for hno in re.findall(r"OpenResDataWin\('([^']+)'\)", content):
            hno_set.add(hno)

        # 例規を JavaScript ではなく普通のリンクで並べる取得元がある
        # （京都市・留寿都村）。H…/H…_j.html の形なので、ディレクトリ名を
        # そのまま例規 ID として拾う。
        for hno in re.findall(r'href="([A-Za-z0-9]+)/\1_j\.html"', content):
            hno_set.add(hno)

    if walk is not None:
        walk.update(
            {
                "scanned_pages": len(scanned),
                "missed_pages": len(missed_pages),
                "missed_examples": missed_pages[:10],
            }
        )
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
            filename = f"{code}_j.html"
            url = f"{base_url}{code}/{filename}"
            session = None

        dest_path = source_dir / filename
        existing_source_path = reiki_io.existing_path(dest_path)
        source_file_path = existing_source_path or reiki_io.gzip_path(dest_path)
        logical_source = reiki_io.logical_path(source_file_path)
        html_output = html_dir / f"{logical_source.stem}.html"
        markdown_output = reiki_io.existing_path(markdown_dir / f"{logical_source.stem}.md")
        has_source = existing_source_path is not None and existing_source_path.stat().st_size > 0
        is_incomplete = not has_source or not html_output.exists() or markdown_output is None
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
                "previous_manifest": previous_manifest_by_source.get(filename),
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
    resume_mode = not force and incomplete_count > 0
    update_mode = not force and not resume_mode and check_updates and catalog_changed is not False
    for plan in plans:
        plan["should_work"] = bool(force or plan["is_incomplete"] or update_mode)
    work_count = sum(1 for plan in plans if plan["should_work"])
    return {
        "total": total,
        "incomplete_count": incomplete_count,
        "resume_mode": resume_mode,
        "update_mode": update_mode,
        "catalog_changed": catalog_changed,
        "work_count": work_count,
        "progress_base": max(0, total - work_count),
    }


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

    progress_base = int(work_mode["progress_base"])
    emit_progress(progress_base, total_regulations, state_path)

    downloaded_count = 0
    checked_count = 0
    not_modified_count = 0
    conditional_count = 0
    parsed_count = 0
    skipped_count = 0
    processed_work_count = 0
    manifest_entries = []
    for index, plan in enumerate(plans):
        source_item = plan["source_item"]
        code = str(plan["code"])
        url = str(plan["url"])
        filename = str(plan["filename"])
        dest_path = plan["dest_path"]
        session = plan["session"]
        source_file_path = plan["source_file_path"]
        previous_manifest = plan["previous_manifest"] if isinstance(plan["previous_manifest"], dict) else {}
        should_work = bool(plan["should_work"])
        source_hash = str(previous_manifest.get("source_sha256") or "")
        metadata = {
            "status_code": "",
            "not_modified": False,
            "conditional": False,
            "etag": str(previous_manifest.get("source_etag") or ""),
            "last_modified": str(previous_manifest.get("source_last_modified") or ""),
        }

        if should_work:
            downloaded, source_file_path, source_hash, metadata = download_file(
                url,
                dest_path,
                force=args.force,
                check_updates=update_mode,
                session=session,
                previous_manifest=previous_manifest,
            )
            if metadata.get("conditional"):
                conditional_count += 1
            if metadata.get("not_modified"):
                not_modified_count += 1
            if downloaded:
                downloaded_count += 1
            elif update_mode:
                checked_count += 1
        else:
            downloaded = False
            skipped_count += 1
            if source_hash == "" and source_file_path.exists():
                source_hash = reiki_io.sha256_path(source_file_path)

        logical_source = reiki_io.logical_path(source_file_path)
        html_output = html_dir / f"{logical_source.stem}.html"
        markdown_output = reiki_io.existing_path(markdown_dir / f"{logical_source.stem}.md")

        if source_file_path.exists() and (
            downloaded
            or args.force
            or not html_output.exists()
            or markdown_output is None
        ):
            d1_parser.process_file(
                source_file_path,
                markdown_dir,
                html_dir,
                base_url=base_url,
                images_dir=images_dir,
                image_public_url=image_public_url,
                force=args.force,
            )
            parsed_count += 1

        manifest_entries.append(
            {
                "code": code,
                "detail_url": url,
                "source_file": logical_source.name,
                "stored_source_file": source_file_path.name,
                "source_sha256": source_hash or (reiki_io.sha256_path(source_file_path) if source_file_path.exists() else ""),
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
    # 目録を最後まで開けて、個票の取得にも失敗が無ければ取り切れたと言える。
    missed_pages = int(catalog_walk.get("missed_pages") or 0)
    # d1-law に --limit は無いので、目録を開けたかと個票の失敗で判断する。
    detail_failures = len(DOWNLOAD_FAILURES)
    walk_complete = missed_pages == 0 and detail_failures == 0
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
            "missed_pages": missed_pages,
            "missed_examples": catalog_walk.get("missed_examples") or [],
            "failed": detail_failures,
            "failed_examples": DOWNLOAD_FAILURES[:10],
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
    print(f"Finished. Downloaded {downloaded_count} files.")
    print(f"Checked existing: {checked_count}")
    print(f"Conditional requests: {conditional_count}")
    print(f"Not modified (304): {not_modified_count}")
    print(f"Skipped existing: {skipped_count}")
    print(f"Parsed outputs: {parsed_count}")
    print(f"Manifest: {manifest_path}")
    if opensearch_session is not None:
        opensearch_session.close()


if __name__ == "__main__":
    main()
