#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import requests

sys.path.append(str(Path(__file__).parent))
import parse_d1_law
import reiki_targets


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
DELAY = 0.5


def download_file(url, dest_path, force=False):
    if not force and dest_path.exists() and dest_path.stat().st_size > 0:
        return False

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        response.raise_for_status()
        with open(dest_path, "wb") as handle:
            handle.write(response.content)
        print(f"Downloaded: {url}")
        time.sleep(DELAY)
        return True
    except Exception as exc:
        print(f"Failed to download {url}: {exc}")
        return False


def get_hno_list(base_url, data_dir, force=False):
    hno_set = set()

    print("Fetching index pages...")
    download_file(base_url + "mokuji_index_index.html", data_dir / "mokuji_index_index.html", force=force)
    download_file(base_url + "mokuji_bunya_index.html", data_dir / "mokuji_bunya_index.html", force=force)

    to_scan = ["mokuji_index_index.html", "mokuji_bunya_index.html"]
    scanned = set()

    while to_scan:
        current = to_scan.pop(0)
        if current in scanned:
            continue
        scanned.add(current)

        file_path = data_dir / current
        if not file_path.exists():
            download_file(base_url + current, file_path)
        if not file_path.exists():
            continue

        try:
            with open(file_path, "r", encoding="cp932", errors="ignore") as handle:
                content = handle.read()
        except Exception as exc:
            print(f"Error reading {file_path}: {exc}")
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
    args = parser.parse_args()

    target = reiki_targets.load_reiki_target(args.slug, expected_system="d1-law")
    base_url = parse_d1_law.derive_d1_law_base_url(target["source_url"])
    source_dir = target["source_dir"]
    markdown_dir = target["markdown_dir"]
    html_dir = target["html_dir"]
    images_dir = target["image_dir"]
    image_public_url = target["image_public_url"]

    print(f"Target: {target['name']} ({target['slug']}, {target['system_type']})")
    print(f"Source URL: {target['source_url']}")
    print(f"Base URL: {base_url}")
    print(f"Target directory: {source_dir}")
    source_dir.mkdir(parents=True, exist_ok=True)

    hno_list = get_hno_list(base_url, source_dir, force=args.force)
    print(f"Found {len(hno_list)} unique regulation IDs.")

    downloaded_count = 0
    for index, hno in enumerate(hno_list):
        filename = f"{hno}_j.html"
        url = f"{base_url}{hno}/{filename}"
        dest_path = source_dir / filename

        downloaded = download_file(url, dest_path, force=args.force)
        if downloaded:
            downloaded_count += 1

        if dest_path.exists():
            parse_d1_law.process_file(
                dest_path,
                markdown_dir,
                html_dir,
                base_url=base_url,
                images_dir=images_dir,
                image_public_url=image_public_url,
                force=args.force,
            )

        if (index + 1) % 10 == 0:
            print(f"Progress: {index + 1}/{len(hno_list)} IDs processed...")

    print(f"Finished. Downloaded {downloaded_count} files.")


if __name__ == "__main__":
    main()
