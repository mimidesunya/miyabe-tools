#!/usr/bin/env python3
"""
users.sqlite（共有LINEユーザーデータベース）を初期化します。
/var/www/data/users.sqlite を作成します。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    schema_path = root / "tools" / "boards" / "users.sql"
    db_path = root / "data" / "users.sqlite"

    print(f"スキーマファイル : {schema_path}")
    print(f"出力先DB         : {db_path}")

    if not schema_path.exists():
        print("エラー: スキーマファイルが見つかりません。", file=sys.stderr)
        return 1

    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 既存のデータベースを削除
    if db_path.exists():
        print(f"既存のデータベースを削除中: {db_path}")
        db_path.unlink()
    
    schema_sql = schema_path.read_text(encoding="utf-8")

    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema_sql)

        # オブジェクトの確認
        rows = conn.execute(
            """
            SELECT type, name
            FROM sqlite_master
            WHERE type IN ('table','index')
              AND name NOT LIKE 'sqlite_%'
            ORDER BY type, name;
            """
        ).fetchall()

        print("\n作成されたオブジェクト:")
        for obj_type, name in rows:
            print(f" - {obj_type}: {name}")

    print("\n初期化が完了しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
