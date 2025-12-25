#!/usr/bin/env python3
"""
自治体ごとのデータベース（boards.sqlite, tasks.sqlite）を一括初期化します。
TSVファイルは tools/boards/data/{slug}/data.tsv に配置されている必要があります。

使用方法:
  python tools/boards/init_db.py <slug>
"""
from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path


def init_boards_db(slug: str, root: Path, tsv_file: Path) -> None:
    """boards.sqlite を初期化"""
    schema_path = root / "tools" / "boards" / "boards.sql"
    db_dir = root / "data" / "boards" / slug
    db_path = db_dir / "boards.sqlite"

    print(f"\n--- boards.sqlite の初期化 ({slug}) ---")
    print(f"スキーマ: {schema_path}")
    print(f"TSV     : {tsv_file}")
    print(f"出力先  : {db_path}")

    if not schema_path.exists():
        print("エラー: boards.sql が見つかりません。", file=sys.stderr)
        return

    if not tsv_file.exists():
        print(f"エラー: TSVファイルが見つかりません: {tsv_file}", file=sys.stderr)
        return

    db_dir.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        print("既存の boards.sqlite を削除します。")
        db_path.unlink()

    schema_sql = schema_path.read_text(encoding="utf-8")

    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema_sql)
        print("スキーマを作成しました。")

        with tsv_file.open("r", encoding="utf-8") as f:
            # TSVのヘッダー行を検出して DictReader を使う
            # コメント行や空行をスキップする簡易ロジック
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
        print(f"{rows_imported} 件の掲示場データをインポートしました。")


def init_tasks_db(slug: str, root: Path) -> None:
    """tasks.sqlite を初期化"""
    schema_path = root / "tools" / "boards" / "tasks.sql"
    db_dir = root / "data" / "boards" / slug
    db_path = db_dir / "tasks.sqlite"

    print(f"\n--- tasks.sqlite の初期化 ({slug}) ---")
    print(f"スキーマ: {schema_path}")
    print(f"出力先  : {db_path}")

    if not schema_path.exists():
        print("エラー: tasks.sql が見つかりません。", file=sys.stderr)
        return

    db_dir.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        print("既存の tasks.sqlite を削除します。")
        db_path.unlink()

    schema_sql = schema_path.read_text(encoding="utf-8")

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(schema_sql)
        print("スキーマを作成しました。")


def main() -> int:
    if len(sys.argv) < 2:
        print("使用法: python tools/boards/init_db.py <slug>")
        return 1

    slug = sys.argv[1]
    root = Path(__file__).resolve().parents[2]
    
    # TSVファイルのパスを固定: tools/boards/data/{slug}/data.tsv
    # もし geocoded 版があればそちらを優先するロジックを入れても良いが、
    # ここではシンプルに data.tsv (または data.geocoded.tsv があればそちら？)
    # ユーザー指示は "TSVはdata/[slug]/data.tsvに固定" なのでそれに従う。
    # ただし、geocode_boards.py は .geocoded.tsv を吐くので、
    # 運用としては geocoded.tsv を data.tsv にリネームするか、
    # ここで geocoded を優先して探すか。
    # 指示通り data.tsv を探すが、もし geocoded があれば警告を出すか、
    # あるいは geocoded があるならそちらを使うように気を利かせるか。
    # 今回は指示通り data.tsv を基本とするが、
    # geocode_boards.py の出力を使いたい場合はリネームしてもらう想定で。
    
    tsv_dir = root / "tools" / "boards" / "data" / slug
    tsv_file = tsv_dir / "data.tsv"
    
    # もし data.geocoded.tsv があって data.tsv がない、あるいは新しい場合はそちらを使う？
    # いや、シンプルに data.tsv 固定という指示を守る。
    
    if not tsv_file.exists():
        # 念のため geocoded もチェックして案内を出す
        geocoded = tsv_dir / "data.geocoded.tsv"
        if geocoded.exists():
            print(f"警告: {tsv_file} が見つかりませんが、{geocoded.name} は存在します。")
            print(f"座標付きデータを使用するには、ファイル名を data.tsv に変更してください。")
        else:
            print(f"エラー: データファイルが見つかりません: {tsv_file}")
        return 1

    init_boards_db(slug, root, tsv_file)
    init_tasks_db(slug, root)

    print("\nすべての初期化が完了しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
