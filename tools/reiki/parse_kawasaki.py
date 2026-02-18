import os
import re
import time
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import urljoin

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data" / "reiki" / "kawasaki"
OUTPUT_DIR = BASE_DIR / "data" / "reiki" / "kawasaki_md"
HTML_OUTPUT_DIR = BASE_DIR / "data" / "reiki" / "kawasaki_html"
IMAGES_DIR = BASE_DIR / "data" / "reiki" / "kawasaki_images"
BASE_URL = "http://www.reiki.city.kawasaki.jp/kawasaki/d1w_reiki/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

def wareki_to_seireki(wareki_str):
    """
    Converts Japanese Wareki date to YYYY-MM-DD.
    Example: 大正13年７月１日 -> 1924-07-01
    """
    era_map = {
        "明治": 1867,
        "大正": 1911,
        "昭和": 1925,
        "平成": 1988,
        "令和": 2018
    }
    
    # Normalize numbers and "元"
    wareki_str = wareki_str.replace("元", "1")
    # Convert full-width numbers to half-width
    wareki_str = wareki_str.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    
    pattern = r"(明治|大正|昭和|平成|令和)(\d+)年(\d+)月(\d+)日"
    match = re.search(pattern, wareki_str)
    if match:
        era, year, month, day = match.groups()
        seireki_year = era_map[era] + int(year)
        return f"{seireki_year:04d}-{int(month):02d}-{int(day):02d}"
    return "0000-00-00"


def download_image(img_filename, kno, images_dir, stats=None):
    """
    Download image from kawasaki reiki site and save to images directory.
    Returns local relative path if successful, original filename if failed.
    """
    # Skip background images or external URLs
    if img_filename.startswith("http") or img_filename.startswith("../"):
        return img_filename
    
    # Construct full URL: http://www.reiki.city.kawasaki.jp/kawasaki/d1w_reiki/H214902500038/S-20100304-005Z0001.gif
    full_url = f"{BASE_URL}{kno}/{img_filename}"
    
    # Save path
    local_path = images_dir / img_filename
    
    # Skip if already downloaded
    if local_path.exists() and local_path.stat().st_size > 0:
        if stats is not None:
            stats['images_skipped'] += 1
        return f"../kawasaki_images/{img_filename}"
    
    # Download
    try:
        images_dir.mkdir(parents=True, exist_ok=True)
        headers = {"User-Agent": USER_AGENT}
        response = requests.get(full_url, headers=headers, timeout=15)
        response.raise_for_status()
        
        with open(local_path, "wb") as f:
            f.write(response.content)
        
        if stats is not None:
            stats['images_downloaded'] += 1
        time.sleep(0.3)  # Rate limiting
        return f"../kawasaki_images/{img_filename}"
    except Exception as e:
        if stats is not None:
            stats['images_failed'] += 1
        print(f"  Failed to download image {img_filename}: {e}")
        return img_filename

def parse_html(file_path, stats=None):
    with open(file_path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")
    
    # Extract kno from filename (e.g., H214902500038_j.html -> H214902500038)
    kno = file_path.stem.replace("_j", "")
    
    # Process images before extracting content
    images = soup.find_all("img")
    if images:
        for img in images:
            src = img.get("src")
            if src:
                local_path = download_image(src, kno, IMAGES_DIR, stats=stats)
                img["src"] = local_path
    
    # Extract Title
    title = ""
    title_div = soup.find("div", class_="danraku-normal", string=re.compile(r"^○"))
    if title_div:
        title = title_div.get_text().strip().lstrip("○")
    else:
        # Fallback to <TITLE>
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text().strip()
    
    # Extract Date
    date_str = "0000-00-00"
    raw_date_text = "不明"
    date_div = soup.find("div", class_="danraku-normal", style=re.compile(r"text-align:\s*right"))
    if date_div:
        raw_date_text = date_div.get_text().strip()
        date_str = wareki_to_seireki(raw_date_text)
    
    # Extract Content
    content_div = soup.find("div", class_="USER-SET-STYLE")
    markdown_content = []
    if content_div:
        # Find all danraku-normal divs or tables
        # We use recursive=True but we want to avoid double counting
        # Actually, let's just get all text blocks and clean them
        for element in content_div.find_all(["div", "table"]):
            # Only process leaf-ish elements or tables
            if element.name == "table" or not element.find(["div", "table"]):
                # Check if element contains image
                img_tag = element.find("img")
                if img_tag:
                    img_src = img_tag.get("src", "")
                    img_alt = img_tag.get("alt", "image")
                    markdown_content.append(f"![{img_alt}]({img_src})")
                    continue
                
                text = element.get_text().strip()
                if not text:
                    continue
                
                # Skip redundant title and date
                if text == f"○{title}" or text == title:
                    continue
                if date_div and text == date_div.get_text().strip():
                    continue
                
                # Avoid duplicates if we already added this text
                if markdown_content and markdown_content[-1] == text:
                    continue
                    
                markdown_content.append(text)
    
    full_markdown = f"# {title}\n\n"
    if date_div:
        full_markdown += f"**日付:** {date_div.get_text().strip()} ({date_str})\n\n"
    full_markdown += "---\n\n"
    full_markdown += "\n\n".join(markdown_content)
    
    # Generate clean HTML for deployment
    html_parts = []
    html_parts.append(f'<div class="law-title">{title}</div>')
    if date_div:
        html_parts.append(f'<div class="law-date">{date_div.get_text().strip()} ({date_str})</div>')
    html_parts.append('<div class="law-content">')
    
    content_div = soup.find("div", class_="USER-SET-STYLE")
    if content_div:
        # Convert relative image paths to absolute paths for web deployment
        for img in content_div.find_all("img"):
            src = img.get("src", "")
            if src.startswith("../kawasaki_images/"):
                # Convert relative path to absolute path from web root
                filename = src.replace("../kawasaki_images/", "")
                img["src"] = f"/data/reiki/kawasaki_images/{filename}"
        
        # Already processed images in soup above
        for element in content_div.find_all(["div", "table"]):
            if element.name == "table" or not element.find(["div", "table"]):
                html_parts.append(str(element))
    
    html_parts.append('</div>')
    clean_html = '\n'.join(html_parts)
    
    return raw_date_text, title, full_markdown, clean_html

def process_file(file_path, md_output_dir, html_output_dir, stats=None, force=False):
    try:
        if not md_output_dir.exists():
            md_output_dir.mkdir(parents=True, exist_ok=True)
        if not html_output_dir.exists():
            html_output_dir.mkdir(parents=True, exist_ok=True)

        # Check if already processed and up-to-date
        html_filename = f"{file_path.stem}.html"
        html_output_path = html_output_dir / html_filename
        
        if not force and html_output_path.exists():
            source_mtime = file_path.stat().st_mtime
            output_mtime = html_output_path.stat().st_mtime
            if output_mtime >= source_mtime:
                # Already processed and up-to-date, skip silently
                return True
        
        date_text, title, markdown, clean_html = parse_html(file_path, stats=stats)
        
        # Write Markdown
        md_filename = f"{file_path.stem}.md"
        md_output_path = md_output_dir / md_filename
        with open(md_output_path, "w", encoding="utf-8") as f:
            f.write(markdown)
        
        # Write clean HTML
        with open(html_output_path, "w", encoding="utf-8") as f:
            f.write(clean_html)
        
        return True
    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        return False

def main():
    if not OUTPUT_DIR.exists():
        OUTPUT_DIR.mkdir(parents=True)
    if not HTML_OUTPUT_DIR.exists():
        HTML_OUTPUT_DIR.mkdir(parents=True)
    
    files = list(DATA_DIR.glob("*_j.html"))
    print(f"Found {len(files)} files to process.")
    
    processed = 0
    skipped = 0
    stats = {
        'images_downloaded': 0,
        'images_skipped': 0,
        'images_failed': 0
    }
    
    for i, file_path in enumerate(files):
        html_output = HTML_OUTPUT_DIR / f"{file_path.stem}.html"
        
        # Check if file will be skipped
        if html_output.exists():
            source_mtime = file_path.stat().st_mtime
            output_mtime = html_output.stat().st_mtime
            if output_mtime >= source_mtime:
                skipped += 1
                if (i + 1) % 100 == 0:
                    print(f"Progress: {i + 1}/{len(files)} checked ({processed} processed, {skipped} skipped)...")
                continue
        
        process_file(file_path, OUTPUT_DIR, HTML_OUTPUT_DIR, stats=stats)
        processed += 1
        
        if (i + 1) % 100 == 0:
            img_summary = f"{stats['images_downloaded']} downloaded, {stats['images_skipped']} skipped" if stats['images_downloaded'] > 0 else ""
            progress_msg = f"Progress: {i + 1}/{len(files)} checked ({processed} processed, {skipped} skipped)"
            if img_summary:
                progress_msg += f" | Images: {img_summary}"
            print(progress_msg)

    print(f"\nFinished conversion:")
    print(f"  Files processed: {processed}")
    print(f"  Files skipped: {skipped}")
    print(f"  Total files: {len(files)}")
    print(f"\nImages:")
    print(f"  Downloaded: {stats['images_downloaded']}")
    print(f"  Skipped (existing): {stats['images_skipped']}")
    if stats['images_failed'] > 0:
        print(f"  Failed: {stats['images_failed']}")
    print(f"\nOutput directories:")
    print(f"  Markdown: {OUTPUT_DIR}")
    print(f"  HTML: {HTML_OUTPUT_DIR}")
    print(f"  Images: {IMAGES_DIR}")

if __name__ == "__main__":
    main()
