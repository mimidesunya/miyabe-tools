#!/usr/bin/env python3
"""
TSVファイルから掲示場データ（boards.sqlite）のみを再インポートします。
タスク状況（tasks.sqlite）は保持されます。

使用方法:
  python tools/boards/import_tsv.py <slug>
"""
from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path

def main() -> int:
    if len(sys.argv) < 2:
        print("使用法: python tools/boards/import_tsv.py <slug>")
        return 1

    slug = sys.argv[1]
    root = Path(__file__).resolve().parents[2]
    
    tsv_file = root / "tools" / "boards" / "data" / slug / "data.tsv"
    schema_path = root / "tools" / "boards" / "boards.sql"
    db_dir = root / "data" / "boards" / slug
    db_path = db_dir / "boards.sqlite"

    print(f"--- 掲示場データの再インポート ({slug}) ---")
    print(f"TSVファイル: {tsv_file}")
    print(f"出力先DB   : {db_path}")

    if not tsv_file.exists():
        print(f"エラー: データファイルが見つかりません: {tsv_file}")
        return 1
    
    if not schema_path.exists():
        print(f"エラー: スキーマファイルが見つかりません: {schema_path}")
        return 1

    # ディレクトリ作成（念のため）
    db_dir.mkdir(parents=True, exist_ok=True)

    # 既存DBの削除
    if db_path.exists():
        print("既存の boards.sqlite を削除して再作成します...")
        db_path.unlink()

    # DB作成とインポート
    schema_sql = schema_path.read_text(encoding="utf-8")

    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema_sql)
        
        with tsv_file.open("r", encoding="utf-8") as f:
            # コメント行や空行をスキップ
            lines = (line for line in f if line.strip() and not line.startswith('#'))
            reader = csv.DictReader(lines, delimiter="\t")
            
            rows_imported = 0
            for row in reader:
                code = row.get("code", "").strip()
                address = row.get("address", "").strip()
                place = row.get("place", "").strip()
                lat_str = row.get("lat", "").strip()
                lon_str = row.get("lon", "").strip()

                try:
                    lat = float(lat_str) if lat_str else None
                except ValueError:
                    lat = None
                try:
                    lon = float(lon_str) if lon_str else None
                except ValueError:
                    lon = None

                if not code or not address:
                    continue

                conn.execute(
                    "INSERT INTO boards (code, address, place, lat, lon) VALUES (?, ?, ?, ?, ?)",
                    (code, address, place, lat, lon)
                )
                rows_imported += 1
        
        conn.commit()
        print(f"完了: {rows_imported} 件のデータをインポートしました。")
        
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
