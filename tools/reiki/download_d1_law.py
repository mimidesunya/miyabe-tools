#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import requests

sys.path.append(str(Path(__file__).parent))
import parse_d1_law
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


def emit_progress(current: int, total: int, state_path: Path | None = None) -> None:
    if state_path is not None:
        reiki_io.update_progress_state(state_path, current=current, total=total, unit="ordinance")
    print(f"[PROGRESS] unit=ordinance current={max(0, current)} total={max(0, total)}", flush=True)


def download_file(url, dest_path, force=False, check_updates=False, session: requests.Session | None = None):
    existing_path = reiki_io.existing_path(dest_path)
    if not force and existing_path and existing_path.stat().st_size > 0 and not check_updates:
        return False, existing_path, reiki_io.sha256_path(existing_path)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        requester = session or requests
        response = requester.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        response.raise_for_status()
        source_hash = reiki_io.sha256_bytes(response.content)
        if existing_path and reiki_io.sha256_path(existing_path) == source_hash and not force:
            return False, existing_path, source_hash
        written_path = reiki_io.write_bytes(dest_path, response.content, compress=True)
        print(f"Downloaded: {url}")
        time.sleep(DELAY)
        return True, written_path, source_hash
    except Exception as exc:
        print(f"Failed to download {url}: {exc}")
        return False, existing_path or dest_path, ""


def get_hno_list(base_url, data_dir, force=False, check_updates=False):
    hno_set = set()

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
            _, stored_path, _ = download_file(base_url + current, file_path, check_updates=check_updates)
        if stored_path is None or not stored_path.exists():
            continue

        try:
            content = reiki_io.read_text_auto(stored_path)
        except Exception as exc:
            print(f"Error reading {stored_path}: {exc}")
            continue

        for link in re.findall(r"(index_\d+\.html|bunya_\d+\.html)", content):
            if link not in scanned:
                to_scan.append(link)

        for hno in re.findall(r"OpenResDataWin\('([^']+)'\)", content):
            hno_set.add(hno)

    return sorted(hno_set)


def normalize_source_url(source_url: str) -> str:
    parts = urlsplit(source_url.strip())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


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
    parts = urlsplit(source_url)
    query_params = parse_qs(parts.query)
    jctcd = str(query_params.get("jctcd", [""])[0]).strip()
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
    if jctcd != "":
        search_url += f"?{urlencode({'jctcd': jctcd})}"
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
    normalized_source_url = normalize_source_url(source_url)
    parts = urlsplit(normalized_source_url)
    site_root = urlunsplit((parts.scheme or "https", parts.netloc, "", "", "")).rstrip("/")
    session = requests.Session()
    init_response = session.get(normalized_source_url, headers={"User-Agent": USER_AGENT}, timeout=15)
    init_response.raise_for_status()

    top_level_codes = []
    seen_codes = set()
    # top-level tree categories partition the current ordinances, so we can avoid
    # walking every nested node while still covering the full catalog.
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


def main():
    default_slug = reiki_targets.default_slug_for_system("d1-law")
    parser = argparse.ArgumentParser(description="Download ordinances from D1-Law systems.")
    parser.add_argument("--slug", default=default_slug, help="Municipality slug resolved from data/municipalities")
    parser.add_argument("--force", action="store_true", help="Redownload source HTML and rebuild outputs")
    parser.add_argument("--check-updates", action="store_true", help="既存条例も再取得して更新を確認する")
    args = parser.parse_args()

    target = reiki_targets.load_reiki_target(args.slug, expected_system="d1-law")
    base_url = parse_d1_law.derive_d1_law_base_url(target["source_url"])
    source_dir = target["source_dir"]
    markdown_dir = target["markdown_dir"]
    html_dir = target["html_dir"]
    images_dir = target["image_dir"]
    image_public_url = target["image_public_url"]
    work_root = Path(target["work_root"])
    manifest_path = work_root / "source_manifest.json.gz"
    state_path = work_root / "scrape_state.json"
    classification_dir = target["classification_dir"]

    print(f"Target: {target['name']} ({target['slug']}, {target['system_type']})")
    print(f"Source URL: {target['source_url']}")
    print(f"Base URL: {base_url}")
    print(f"Target directory: {source_dir}")
    source_dir.mkdir(parents=True, exist_ok=True)

    opensearch_session: requests.Session | None = None
    hno_list: list[str] = []
    opensearch_entries: list[dict[str, str]] = []
    if parse_d1_law.is_opensearch_mokuji_source_url(str(target["source_url"])):
        opensearch_session, opensearch_entries = collect_opensearch_entries(str(target["source_url"]))
        print(f"Found {len(opensearch_entries)} unique opensearch regulations.")
    else:
        hno_list = get_hno_list(base_url, source_dir, force=args.force, check_updates=args.check_updates)
        print(f"Found {len(hno_list)} unique regulation IDs.")

    total_regulations = len(opensearch_entries) if opensearch_entries else len(hno_list)
    source_items = opensearch_entries if opensearch_entries else hno_list
    if total_regulations <= 0:
        print("[WARN] No regulations found.", flush=True)
    emit_progress(0, total_regulations, state_path)

    downloaded_count = 0
    checked_count = 0
    parsed_count = 0
    manifest_entries = []
    for index, source_item in enumerate(source_items):
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
        source_file_path = reiki_io.existing_path(dest_path) or reiki_io.gzip_path(dest_path)

        downloaded, source_file_path, source_hash = download_file(
            url,
            dest_path,
            force=args.force,
            check_updates=args.check_updates,
            session=session,
        )
        if downloaded:
            downloaded_count += 1
        elif args.check_updates:
            checked_count += 1

        logical_source = reiki_io.logical_path(source_file_path)
        html_output = html_dir / f"{logical_source.stem}.html"
        markdown_output = reiki_io.existing_path(markdown_dir / f"{logical_source.stem}.md")

        if source_file_path.exists() and (
            downloaded
            or args.force
            or not html_output.exists()
            or markdown_output is None
        ):
            parse_d1_law.process_file(
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
                "checked_updates": bool(args.check_updates),
            }
        )
        if isinstance(source_item, dict):
            manifest_entries[-1]["mokujicd"] = str(source_item.get("mokujicd", ""))

        if ((index + 1) % 25) == 0 or (index + 1) == total_regulations:
            # 途中停止しても後追い補完が source_url 等を復元できるよう、manifest を定期保存する。
            reiki_io.write_json(manifest_path, manifest_entries, compress=True)
        emit_progress(index + 1, total_regulations, state_path)

    reiki_io.write_json(manifest_path, manifest_entries, compress=True)
    print(f"Finished. Downloaded {downloaded_count} files.")
    print(f"Checked existing: {checked_count}")
    print(f"Parsed outputs: {parsed_count}")
    print(f"Manifest: {manifest_path}")
    if opensearch_session is not None:
        opensearch_session.close()


if __name__ == "__main__":
    main()
