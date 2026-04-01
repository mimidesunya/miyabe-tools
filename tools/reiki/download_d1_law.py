#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import requests

sys.path.append(str(Path(__file__).parent))
import build_ordinance_index as ordinance_index_builder
import parse_d1_law
import reiki_io
import reiki_targets


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
DELAY = 0.5


def emit_progress(current: int, total: int, state_path: Path | None = None) -> None:
    if state_path is not None:
        reiki_io.update_progress_state(state_path, current=current, total=total, unit="ordinance")
    print(f"[PROGRESS] unit=ordinance current={max(0, current)} total={max(0, total)}", flush=True)


def download_file(url, dest_path, force=False, check_updates=False):
    existing_path = reiki_io.existing_path(dest_path)
    if not force and existing_path and existing_path.stat().st_size > 0 and not check_updates:
        return False, existing_path, reiki_io.sha256_path(existing_path)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
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


def main():
    default_slug = reiki_targets.default_slug_for_system("d1-law")
    parser = argparse.ArgumentParser(description="Download ordinances from D1-Law systems.")
    parser.add_argument("--slug", default=default_slug, help="Municipality slug in data/config.json")
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
    output_db = Path(target["db_path"])

    print(f"Target: {target['name']} ({target['slug']}, {target['system_type']})")
    print(f"Source URL: {target['source_url']}")
    print(f"Base URL: {base_url}")
    print(f"Target directory: {source_dir}")
    source_dir.mkdir(parents=True, exist_ok=True)

    hno_list = get_hno_list(base_url, source_dir, force=args.force, check_updates=args.check_updates)
    print(f"Found {len(hno_list)} unique regulation IDs.")
    emit_progress(0, len(hno_list), state_path)
    ordinance_index_builder.ensure_output_db(output_db)

    downloaded_count = 0
    checked_count = 0
    parsed_count = 0
    manifest_entries = []
    for index, hno in enumerate(hno_list):
        filename = f"{hno}_j.html"
        url = f"{base_url}{hno}/{filename}"
        dest_path = source_dir / filename
        source_file_path = reiki_io.existing_path(dest_path) or reiki_io.gzip_path(dest_path)

        downloaded, source_file_path, source_hash = download_file(
            url,
            dest_path,
            force=args.force,
            check_updates=args.check_updates,
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
                "code": hno,
                "detail_url": url,
                "source_file": logical_source.name,
                "stored_source_file": source_file_path.name,
                "source_sha256": source_hash or (reiki_io.sha256_path(source_file_path) if source_file_path.exists() else ""),
                "checked_updates": bool(args.check_updates),
            }
        )

        logical_key = Path(logical_source.name).with_suffix("").as_posix()
        # 既存 HTML の再利用時も含め、検索 DB 側は 1 件ずつ取りこぼしなく更新する。
        ordinance_index_builder.upsert_source_key(
            slug=str(target["slug"]),
            clean_html_dir=html_dir,
            classification_dir=classification_dir,
            markdown_dir=markdown_dir,
            output_db=output_db,
            key=logical_key,
            manifest=manifest_entries[-1],
        )

        if ((index + 1) % 25) == 0 or (index + 1) == len(hno_list):
            # 途中停止しても後追い補完が source_url 等を復元できるよう、manifest を定期保存する。
            reiki_io.write_json(manifest_path, manifest_entries, compress=True)
        emit_progress(index + 1, len(hno_list), state_path)

    reiki_io.write_json(manifest_path, manifest_entries, compress=True)
    print(f"Finished. Downloaded {downloaded_count} files.")
    print(f"Checked existing: {checked_count}")
    print(f"Parsed outputs: {parsed_count}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
