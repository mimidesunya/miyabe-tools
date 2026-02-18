#!/usr/bin/env python3
"""
Initialize ordinance database from JSON metadata files.
Creates data/reiki/ordinances.sqlite
"""
import json
import sqlite3
import re
from pathlib import Path

def normalize_kana(kana):
    """Normalize reading kana for sorting. Removes common prefixes like 'かわさきし'."""
    if not kana:
        return ""
    
    # Remove "かわさき(し)" prefix if present at start
    # Many ordinances start with "Kawasaki City ..."
    normalized = kana
    if normalized.startswith("かわさきし"):
        normalized = normalized[5:]
    elif normalized.startswith("かわさき"):
        normalized = normalized[4:]
        
    return normalized

def init_db(root: Path):
    db_path = root / "data" / "reiki" / "ordinances.sqlite"
    json_dir = root / "data" / "reiki" / "kawasaki_json"
    
    print(f"Creating database at {db_path}...")
    
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
    
    files = list(json_dir.glob("*.json"))
    print(f"Found {len(files)} JSON files. Importing...")
    
    count = 0
    for json_file in files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            filename = json_file.stem  # e.g., "H213902500001_j"
            
            # Extract fields
            title = data.get("title", "")
            reading_kana = data.get("readingKana", "")
            if not reading_kana:
                # Fallback if no reading provided (though most should have it)
                reading_kana = title 
                
            sortable_kana = normalize_kana(reading_kana)
            
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
            clean_html_path = root / "data" / "reiki" / "kawasaki_html" / (filename + ".html")
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
    current_dir = Path(__file__).parent.resolve()
    # Assuming script is in tools/reiki/, root is up 2 levels
    project_root = current_dir.parent.parent
    init_db(project_root)
