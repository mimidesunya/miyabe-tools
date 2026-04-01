#!/usr/bin/env python3
"""
Initialize ordinance database from JSON metadata files.
Creates data/reiki/{slug}/ordinances.sqlite
"""
import argparse
import html
import json
import sqlite3
import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))
import reiki_io

def project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent

def load_config(root: Path) -> dict:
    for candidate in (root / "data" / "config.json", root / "data" / "config.example.json"):
        if candidate.exists():
            try:
                data = json.loads(reiki_io.read_text_auto(candidate))
            except Exception:
                return {}
            return data if isinstance(data, dict) else {}
    return {}

def data_path(root: Path, relative: str) -> Path:
    return root / "data" / Path(relative.replace("\\", "/"))

def default_slug(root: Path) -> str:
    config = load_config(root)
    value = str(config.get("DEFAULT_SLUG", "")).strip()
    if value:
        return value
    municipalities = config.get("MUNICIPALITIES", {})
    if isinstance(municipalities, dict) and municipalities:
        first = next(iter(municipalities.keys()), "")
        if isinstance(first, str):
            return first.strip()
    return ""

def municipality_reiki_paths(root: Path, slug: str) -> tuple[Path, Path, Path]:
    config = load_config(root)
    municipalities = config.get("MUNICIPALITIES", {})
    entry = municipalities.get(slug, {}) if isinstance(municipalities, dict) else {}
    feature = entry.get("reiki", {}) if isinstance(entry, dict) else {}
    if not isinstance(feature, dict):
        feature = {}

    default_json_dir = f"reiki/{slug}/json"
    default_clean_html = f"reiki/{slug}/html"
    default_db = f"reiki/{slug}/ordinances.sqlite"

    json_dir_rel = str(feature.get("classification_dir", default_json_dir)).strip()
    clean_html_rel = str(feature.get("clean_html_dir", default_clean_html)).strip()
    db_rel = str(feature.get("db_path", default_db)).strip()

    return (
        data_path(root, json_dir_rel),
        data_path(root, clean_html_rel),
        data_path(root, db_rel),
    )

def sortable_prefixes(root: Path, slug: str) -> list[str]:
    config = load_config(root)
    municipalities = config.get("MUNICIPALITIES", {})
    entry = municipalities.get(slug, {}) if isinstance(municipalities, dict) else {}
    feature = entry.get("reiki", {}) if isinstance(entry, dict) else {}
    if isinstance(feature, dict):
        raw = feature.get("sortable_prefixes", [])
        if isinstance(raw, list):
            values = [str(v).strip() for v in raw if str(v).strip()]
            if values:
                return values
    return []

def normalize_kana(kana, prefixes=None):
    """Normalize reading kana for sorting."""
    if not kana:
        return ""
    
    normalized = kana
    for prefix in prefixes or []:
        if prefix and normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break
        
    return normalized

def init_db(root: Path, slug: str):
    json_dir, clean_html_dir, db_path = municipality_reiki_paths(root, slug)
    
    print(f"Creating database at {db_path}...")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Create table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ordinances (
        filename TEXT PRIMARY KEY,
        title TEXT,
        reading_kana TEXT,
        sortable_kana TEXT,
        primary_class TEXT,
        secondary_tags TEXT,
        necessity_score INTEGER,
        fiscal_impact_score REAL,
        regulatory_burden_score REAL,
        policy_effectiveness_score REAL,
        lens_tags TEXT,
        lens_a_stance TEXT,
        lens_b_stance TEXT,
        combined_stance TEXT,
        combined_reason TEXT,
        document_type TEXT,
        responsible_department TEXT,
        reason TEXT,
        enactment_date TEXT,
        analyzed_at TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # Simple index for common searches
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sortable_kana ON ordinances(sortable_kana)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_class ON ordinances(primary_class)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_necessity ON ordinances(necessity_score)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_date ON ordinances(enactment_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_combined_stance ON ordinances(combined_stance)")
    
    files = reiki_io.collect_matching_files(json_dir, ["*.json", "*.json.gz"])
    print(f"Found {len(files)} JSON files. Importing...")
    prefixes = sortable_prefixes(root, slug)
    
    count = 0
    for json_file in files:
        try:
            data = json.loads(reiki_io.read_text_auto(json_file))
                
            filename = reiki_io.logical_path(json_file).stem  # e.g., "H213902500001_j"
            
            # Extract fields
            title = html.unescape(data.get("title", ""))
            reading_kana = html.unescape(data.get("readingKana", ""))
            if not reading_kana:
                # Fallback if no reading provided (though most should have it)
                reading_kana = title 
                
            sortable_kana = normalize_kana(reading_kana, prefixes)
            
            primary_class = data.get("primaryClass", "")
            
            # secondaryTags is list, join by comma
            secondary_tags = ",".join(data.get("secondaryTags", []))
            
            # Lens Data Extraction
            lens_tags_list = data.get("lensTags", [])
            lens_tags = ",".join(str(x) for x in lens_tags_list) if isinstance(lens_tags_list, list) else ""
            
            lens_eval = data.get("lensEvaluation", {})
            lens_a = lens_eval.get("lensA", {})
            lens_b = lens_eval.get("lensB", {})
            combined = lens_eval.get("combined", {})
            
            lens_a_stance = lens_a.get("stance", "")
            lens_b_stance = lens_b.get("stance", "")
            combined_stance = combined.get("stance", "")
            combined_reason = combined.get("reason", "")
            
            necessity_score = data.get("necessityScore", 0)
            fiscal_impact_score = data.get("fiscalImpactScore", 0.0)
            regulatory_burden_score = data.get("regulatoryBurdenScore", 0.0)
            policy_effectiveness_score = data.get("policyEffectivenessScore", 0.0)
            
            document_type = data.get("documentType", "")
            responsible_department = data.get("responsibleDepartment", "")
            reason = data.get("reason", "")
            analyzed_at = data.get("analyzedAt", "")
            
            # Extract enactment date from clean HTML metadata
            # Format in clean HTML: <div class="law-date">昭和38年８月19日 (1963-08-19)</div>
            enactment_date = None
            clean_html_path = clean_html_dir / (filename + ".html")
            if clean_html_path.exists():
                try:
                    with open(clean_html_path, "r", encoding="utf-8") as hf:
                        html_content = hf.read()
                        # Extract date using regex
                        # <div class="law-date">... (YYYY-MM-DD)</div>
                        m = re.search(r'<div class="law-date">.*?\((\d{4}-\d{2}-\d{2})\)</div>', html_content)
                        if m:
                            enactment_date = m.group(1)
                except Exception as e:
                    pass
            
            cursor.execute("""
            INSERT OR REPLACE INTO ordinances (
                filename, title, reading_kana, sortable_kana,
                primary_class, secondary_tags, necessity_score,
                fiscal_impact_score, regulatory_burden_score, policy_effectiveness_score,
                lens_tags, lens_a_stance, lens_b_stance, combined_stance, combined_reason,
                document_type, responsible_department, reason, enactment_date, analyzed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                filename, title, reading_kana, sortable_kana,
                primary_class, secondary_tags, necessity_score,
                fiscal_impact_score, regulatory_burden_score, policy_effectiveness_score,
                lens_tags, lens_a_stance, lens_b_stance, combined_stance, combined_reason,
                document_type, responsible_department, reason, enactment_date, analyzed_at
            ))
            
            count += 1

            if count % 100 == 0:
                print(f"Processed {count} files...", end="\r")
                
        except Exception as e:
            print(f"\nError processing {json_file.name}: {e}")
            
    conn.commit()
    conn.close()
    print(f"\nSuccessfully imported {count} records.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="自治体ごとの例規 JSON から SQLite を初期化します。")
    parser.add_argument("--slug", default=None, help="自治体slug。未指定時は config の DEFAULT_SLUG または先頭の自治体を使います。")
    args = parser.parse_args()

    root = project_root()
    slug = (args.slug or default_slug(root)).strip()
    if slug == "":
        raise SystemExit("自治体 slug を決定できませんでした。--slug を指定してください。")
    init_db(root, slug)
