import os
import re
from pathlib import Path
from bs4 import BeautifulSoup

DATA_DIR = Path("f:/dev/miyabe-tools/data/reiki/kawasaki")
OUTPUT_DIR = Path("f:/dev/miyabe-tools/data/reiki/kawasaki_md")

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

def parse_html(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")
    
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
    
    return raw_date_text, title, full_markdown

def main():
    if not OUTPUT_DIR.exists():
        OUTPUT_DIR.mkdir(parents=True)
    
    files = list(DATA_DIR.glob("*_j.html"))
    print(f"Found {len(files)} files to process.")
    
    for i, file_path in enumerate(files):
        try:
            date_text, title, markdown = parse_html(file_path)
            
            # Clean title and date for filename
            clean_date = re.sub(r'[\\/*?:"<>|]', "_", date_text)
            clean_title = re.sub(r'[\\/*?:"<>|]', "_", title)
            filename = f"{clean_date}_{clean_title}.md"
            output_path = OUTPUT_DIR / filename
            
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(markdown)
            
            if (i + 1) % 100 == 0:
                print(f"Processed {i + 1}/{len(files)} files...")
        except Exception as e:
            print(f"Error processing {file_path}: {e}")

    print("Finished conversion.")

if __name__ == "__main__":
    main()
