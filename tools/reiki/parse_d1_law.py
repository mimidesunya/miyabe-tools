#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

sys.path.append(str(Path(__file__).parent))
import reiki_io
import reiki_targets


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def is_opensearch_mokuji_source_url(source_url):
    parts = urlsplit(source_url.strip())
    return parts.netloc.lower().endswith("d1-law.com") and parts.path.lower().endswith("/opensearch/srmjf01/init")


def wareki_to_seireki(wareki_str):
    era_map = {
        "明治": 1867,
        "大正": 1911,
        "昭和": 1925,
        "平成": 1988,
        "令和": 2018,
    }

    wareki_str = wareki_str.replace("元", "1")
    wareki_str = wareki_str.translate(str.maketrans("０１２３４５６７８９", "0123456789"))

    match = re.search(r"(明治|大正|昭和|平成|令和)(\d+)年(\d+)月(\d+)日", wareki_str)
    if match:
        era, year, month, day = match.groups()
        seireki_year = era_map[era] + int(year)
        return f"{seireki_year:04d}-{int(month):02d}-{int(day):02d}"
    return "0000-00-00"


def extract_wareki_date(raw_text):
    match = re.search(r"(明治|大正|昭和|平成|令和)[0-9０-９元]+年[0-9０-９]+月[0-9０-９]+日", raw_text)
    return match.group(0) if match else ""


def derive_d1_law_base_url(source_url):
    parts = urlsplit(source_url)
    path = parts.path or "/"
    lower_path = path.lower()
    marker = "/d1w_reiki/"
    marker_index = lower_path.find(marker)
    if marker_index >= 0:
        base_path = path[: marker_index + len(marker)]
    elif is_opensearch_mokuji_source_url(source_url):
        base_path = "/opensearch/"
    elif lower_path.endswith(("/reiki.html", "/reiki.htm", "/index.html", "/index.htm")):
        base_path = path.rsplit("/", 1)[0] + "/"
    else:
        raise ValueError(f"Unsupported d1-law URL for this scraper: {source_url}")

    return urlunsplit((parts.scheme or "https", parts.netloc, base_path, "", ""))


def download_image(img_filename, kno, images_dir, base_url, stats=None):
    if img_filename.startswith("http") or img_filename.startswith("../"):
        return img_filename

    full_url = f"{base_url}{kno}/{img_filename}"
    local_path = images_dir / img_filename

    if local_path.exists() and local_path.stat().st_size > 0:
        if stats is not None:
            stats["images_skipped"] += 1
        return f"../images/{img_filename}"

    try:
        images_dir.mkdir(parents=True, exist_ok=True)
        response = requests.get(full_url, headers={"User-Agent": USER_AGENT}, timeout=15)
        response.raise_for_status()
        with open(local_path, "wb") as handle:
            handle.write(response.content)

        if stats is not None:
            stats["images_downloaded"] += 1
        time.sleep(0.3)
        return f"../images/{img_filename}"
    except Exception as exc:
        if stats is not None:
            stats["images_failed"] += 1
        print(f"  Failed to download image {img_filename}: {exc}")
        return img_filename


def parse_opensearch_html(soup, *, base_url, image_public_url):
    content_div = soup.find("div", id="result")
    if content_div is None:
        return None

    content_soup = BeautifulSoup(str(content_div), "html.parser")
    normalized_content = content_soup.find("div", id="result")
    if normalized_content is None:
        return None

    for button_area in normalized_content.select("div.btnlistarea"):
        button_area.decompose()

    title = ""
    title_div = normalized_content.find("div", string=re.compile(r"^○"))
    if title_div is not None:
        title = title_div.get_text(" ", strip=True).lstrip("○").strip()
    if title == "":
        title_tag = soup.find("title")
        if title_tag is not None:
            raw_title = title_tag.get_text(" ", strip=True)
            title = re.sub(r"\s+[^\s]*例規.*$", "", raw_title).strip() or raw_title

    raw_date_text = "不明"
    date_str = "0000-00-00"
    date_div = normalized_content.find("div", string=re.compile(r"(明治|大正|昭和|平成|令和).+日"))
    if date_div is not None:
        raw_date_text = date_div.get_text(" ", strip=True)
        wareki_date = extract_wareki_date(raw_date_text)
        if wareki_date != "":
            date_str = wareki_to_seireki(wareki_date)

    markdown_content = []
    for child in normalized_content.children:
        if getattr(child, "name", None) == "br":
            if markdown_content and markdown_content[-1] != "":
                markdown_content.append("")
            continue
        if getattr(child, "name", None) not in {"div", "table"}:
            continue
        text = child.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()
        if text == "":
            continue
        if text == f"○{title}" or text == title:
            continue
        if raw_date_text != "不明" and text == raw_date_text:
            continue
        if markdown_content and markdown_content[-1] == text:
            continue
        markdown_content.append(text)

    while markdown_content and markdown_content[-1] == "":
        markdown_content.pop()

    full_markdown = f"# {title}\n\n"
    if raw_date_text != "不明":
        full_markdown += f"**日付:** {raw_date_text} ({date_str})\n\n"
    full_markdown += "---\n\n"
    full_markdown += "\n\n".join(markdown_content)

    for img in normalized_content.find_all("img"):
        src = str(img.get("src", "")).strip()
        if src == "":
            continue
        if src.startswith("../images/") or src.startswith("../kawasaki_images/"):
            img["src"] = f"{image_public_url.rstrip('/')}/{Path(src).name}"
        elif not src.startswith("http://") and not src.startswith("https://"):
            img["src"] = urljoin(base_url, src)

    html_parts = [f'<div class="law-title">{title}</div>']
    if raw_date_text != "不明":
        html_parts.append(f'<div class="law-date">{raw_date_text} ({date_str})</div>')
    html_parts.append('<div class="law-content">')
    for child in normalized_content.children:
        if getattr(child, "name", None) == "br":
            html_parts.append("<br/>")
            continue
        if getattr(child, "name", None) in {"div", "table"}:
            html_parts.append(str(child))
    html_parts.append("</div>")

    return raw_date_text, title, full_markdown, "\n".join(html_parts)


def parse_html(file_path, *, base_url, images_dir, image_public_url, stats=None):
    soup = BeautifulSoup(reiki_io.read_text_auto(file_path), "html.parser")

    opensearch_parsed = parse_opensearch_html(
        soup,
        base_url=base_url,
        image_public_url=image_public_url,
    )
    if opensearch_parsed is not None:
        return opensearch_parsed

    logical_source = reiki_io.logical_path(Path(file_path))
    kno = logical_source.stem.replace("_j", "")

    for img in soup.find_all("img"):
        src = img.get("src")
        if src:
            img["src"] = download_image(src, kno, images_dir, base_url, stats=stats)

    title = ""
    title_div = soup.find("div", class_="danraku-normal", string=re.compile(r"^○"))
    if title_div:
        title = title_div.get_text().strip().lstrip("○")
    else:
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text().strip()

    date_str = "0000-00-00"
    raw_date_text = "不明"
    date_div = soup.find("div", class_="danraku-normal", style=re.compile(r"text-align:\s*right"))
    if date_div:
        raw_date_text = date_div.get_text().strip()
        date_str = wareki_to_seireki(raw_date_text)

    content_div = soup.find("div", class_="USER-SET-STYLE")
    markdown_content = []
    if content_div:
        for element in content_div.find_all(["div", "table"]):
            if element.name == "table" or not element.find(["div", "table"]):
                img_tag = element.find("img")
                if img_tag:
                    img_src = img_tag.get("src", "")
                    img_alt = img_tag.get("alt", "image")
                    markdown_content.append(f"![{img_alt}]({img_src})")
                    continue

                text = element.get_text().strip()
                if not text:
                    continue
                if text == f"○{title}" or text == title:
                    continue
                if date_div and text == date_div.get_text().strip():
                    continue
                if markdown_content and markdown_content[-1] == text:
                    continue

                markdown_content.append(text)

    full_markdown = f"# {title}\n\n"
    if date_div:
        full_markdown += f"**日付:** {date_div.get_text().strip()} ({date_str})\n\n"
    full_markdown += "---\n\n"
    full_markdown += "\n\n".join(markdown_content)

    html_parts = [f'<div class="law-title">{title}</div>']
    if date_div:
        html_parts.append(f'<div class="law-date">{date_div.get_text().strip()} ({date_str})</div>')
    html_parts.append('<div class="law-content">')

    if content_div:
        for img in content_div.find_all("img"):
            src = img.get("src", "")
            # Keep supporting old municipality-specific image folders while normalizing new output.
            if src.startswith("../images/") or src.startswith("../kawasaki_images/"):
                img["src"] = f"{image_public_url.rstrip('/')}/{Path(src).name}"

        for element in content_div.find_all(["div", "table"]):
            if element.name == "table" or not element.find(["div", "table"]):
                html_parts.append(str(element))

    html_parts.append("</div>")
    clean_html = "\n".join(html_parts)

    return raw_date_text, title, full_markdown, clean_html


def process_file(
    file_path,
    md_output_dir,
    html_output_dir,
    *,
    base_url,
    images_dir,
    image_public_url,
    stats=None,
    force=False,
):
    try:
        md_output_dir.mkdir(parents=True, exist_ok=True)
        html_output_dir.mkdir(parents=True, exist_ok=True)

        logical_source = reiki_io.logical_path(Path(file_path))
        html_output_path = html_output_dir / f"{logical_source.stem}.html"
        if not force and html_output_path.exists():
            if html_output_path.stat().st_mtime >= file_path.stat().st_mtime:
                return True

        _, _, markdown, clean_html = parse_html(
            file_path,
            base_url=base_url,
            images_dir=images_dir,
            image_public_url=image_public_url,
            stats=stats,
        )

        reiki_io.write_text(md_output_dir / f"{logical_source.stem}.md", markdown, compress=True)
        with open(html_output_path, "w", encoding="utf-8") as handle:
            handle.write(clean_html)
        return True
    except Exception as exc:
        print(f"Error processing {file_path}: {exc}")
        return False


def main():
    default_slug = reiki_targets.default_slug_for_system("d1-law")
    parser = argparse.ArgumentParser(description="Process D1-Law ordinance HTML files.")
    parser.add_argument("--slug", default=default_slug, help="Municipality slug resolved from data/municipalities")
    parser.add_argument("--force", action="store_true", help="Rebuild outputs even when unchanged")
    args = parser.parse_args()

    target = reiki_targets.load_reiki_target(args.slug, expected_system="d1-law")
    base_url = derive_d1_law_base_url(target["source_url"])
    source_dir = target["source_dir"]
    markdown_dir = target["markdown_dir"]
    html_dir = target["html_dir"]
    images_dir = target["image_dir"]
    image_public_url = target["image_public_url"]

    files = reiki_io.collect_matching_files(source_dir, ["*_j.html", "*_j.html.gz"])
    print(f"Target: {target['name']} ({target['slug']}, {target['system_type']})")
    print(f"Found {len(files)} files to process.")

    processed = 0
    skipped = 0
    stats = {
        "images_downloaded": 0,
        "images_skipped": 0,
        "images_failed": 0,
    }

    for index, file_path in enumerate(files):
        logical_source = reiki_io.logical_path(file_path)
        html_output = html_dir / f"{logical_source.stem}.html"
        if not args.force and html_output.exists():
            if html_output.stat().st_mtime >= file_path.stat().st_mtime:
                skipped += 1
                if (index + 1) % 100 == 0:
                    print(f"Progress: {index + 1}/{len(files)} checked ({processed} processed, {skipped} skipped)...")
                continue

        process_file(
            file_path,
            markdown_dir,
            html_dir,
            base_url=base_url,
            images_dir=images_dir,
            image_public_url=image_public_url,
            stats=stats,
            force=args.force,
        )
        processed += 1

        if (index + 1) % 100 == 0:
            img_summary = ""
            if stats["images_downloaded"] > 0:
                img_summary = f"{stats['images_downloaded']} downloaded, {stats['images_skipped']} skipped"
            progress_msg = f"Progress: {index + 1}/{len(files)} checked ({processed} processed, {skipped} skipped)"
            if img_summary:
                progress_msg += f" | Images: {img_summary}"
            print(progress_msg)

    print("\nFinished conversion:")
    print(f"  Files processed: {processed}")
    print(f"  Files skipped: {skipped}")
    print(f"  Total files: {len(files)}")
    print("\nImages:")
    print(f"  Downloaded: {stats['images_downloaded']}")
    print(f"  Skipped (existing): {stats['images_skipped']}")
    if stats["images_failed"] > 0:
        print(f"  Failed: {stats['images_failed']}")
    print("\nOutput directories:")
    print(f"  Markdown: {markdown_dir}")
    print(f"  HTML: {html_dir}")
    print(f"  Images: {images_dir}")


if __name__ == "__main__":
    main()
