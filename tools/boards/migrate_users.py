#!/usr/bin/env python3
"""
古い tasks.sqlite (スラッグごと) から共有の users.sqlite へユーザーを移行します。
その後、各スラッグの tasks.sqlite を再初期化します。
"""
from __future__ import annotations

import sqlite3
import shutil
import json
from pathlib import Path
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    config_path = root / "lib" / "config.json"
    
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        municipalities = config.get("MUNICIPALITIES", {})
        slugs = list(municipalities.keys())
        if not slugs:
            slugs = ["kawasaki", "higashikurume"]
    else:
        slugs = ["kawasaki", "higashikurume"]

    users_db = root / "data" / "users.sqlite"
    
    if not users_db.exists():
        print("エラー: users.sqlite が見つかりません。先に init_users_db.py を実行してください。", file=sys.stderr)
        return 1
    
    # 各スラッグの tasks.sqlite からユーザーを移行
    with sqlite3.connect(users_db, timeout=30.0) as users_conn:
        for slug in slugs:
            old_tasks_db = root / "data" / "boards" / slug / "tasks.sqlite"
            if not old_tasks_db.exists():
                print(f"スキップ {slug}: tasks.sqlite が見つかりません")
                continue
            
            print(f"\n{slug} からユーザーを移行中...")
            
            # ロックを避けるために一時的なコピーを作成
            temp_db = old_tasks_db.with_suffix(".sqlite.temp")
            shutil.copy2(old_tasks_db, temp_db)
            
            try:
                # 一時データベースをアタッチ
                users_conn.execute("ATTACH DATABASE ? AS old", (str(temp_db),))
                
                # ユーザーをコピー（line_user_id による重複は無視）
                users_conn.execute("""
                    INSERT OR IGNORE INTO users (line_user_id, name, avatar, created_at, updated_at)
                    SELECT line_user_id, name, avatar, created_at, updated_at
                    FROM old.users
                """)
                
                migrated = users_conn.execute("SELECT changes()").fetchone()[0]
                print(f"  {migrated} 名の新規ユーザーを移行しました")
                
                users_conn.execute("DETACH DATABASE old")
                users_conn.commit()  # デタッチがコミットされることを確認
            except Exception as e:
                print(f"  {slug} からの移行中にエラーが発生しました: {e}")
            finally:
                # 一時ファイルをクリーンアップ（Windows 用にリトライ付き）
                import time
                for _ in range(3):
                    try:
                        if temp_db.exists():
                            temp_db.unlink()
                        break
                    except PermissionError:
                        time.sleep(0.1)
        
        users_conn.commit()
        
        # 最終的なユーザー数を表示
        count = users_conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        print(f"\n共有データベース内の総ユーザー数: {count}")
    
    # 各スラッグの tasks.sqlite を再初期化
    tasks_schema_path = root / "tools" / "boards" / "tasks.sql"
    if not tasks_schema_path.exists():
        print("エラー: tasks.sql が見つかりません", file=sys.stderr)
        return 1
    
    tasks_schema = tasks_schema_path.read_text(encoding="utf-8")
    
    for slug in slugs:
        print(f"\n{slug} の tasks.sqlite を再初期化中...")
        tasks_db = root / "data" / "boards" / slug / "tasks.sqlite"
        old_backup = tasks_db.with_suffix(".sqlite.old")
        
        # 古いデータベースをバックアップ
        if tasks_db.exists():
            if old_backup.exists():
                old_backup.unlink()
            tasks_db.rename(old_backup)
            print(f"  {old_backup.name} にバックアップしました")
        
        # 新しい tasks.sqlite を作成
        with sqlite3.connect(tasks_db, timeout=30.0) as conn:
            conn.executescript(tasks_schema)
            
            # ユーザーデータベースと古いバックアップをアタッチ
            conn.execute("ATTACH DATABASE ? AS users", (str(users_db),))
            if old_backup.exists():
                # バックアップの一時コピーを作成
                temp_backup = old_backup.with_suffix(".sqlite.temp_backup")
                shutil.copy2(old_backup, temp_backup)
                
                try:
                    conn.execute("ATTACH DATABASE ? AS old", (str(temp_backup),))
                    
                    # task_status を移行（ユーザー参照を更新）
                    # line_user_id を介して旧 user.id から新 user.id へのマッピングを作成
                    print("  タスクステータスを移行中...")
                    
                    # まず、ユーザー情報付きで task_status を取得
                    rows = conn.execute("""
                        SELECT 
                            ts.board_code,
                            ts.status,
                            ts.updated_at,
                            ts.last_comment,
                            ou.line_user_id
                        FROM old.task_status ts
                        LEFT JOIN old.users ou ON ou.id = ts.updated_by
                    """).fetchall()
                    
                    for board_code, status, updated_at, last_comment, line_user_id in rows:
                        if line_user_id:
                            # 新しい user_id を取得
                            new_user = conn.execute(
                                "SELECT id FROM users.users WHERE line_user_id = ?",
                                (line_user_id,)
                            ).fetchone()
                            
                            if new_user:
                                conn.execute("""
                                    INSERT OR REPLACE INTO task_status 
                                    (board_code, status, updated_by, updated_at, last_comment)
                                    VALUES (?, ?, ?, ?, ?)
                                """, (board_code, status, new_user[0], updated_at, last_comment))
                    
                    # ステータス履歴を移行
                    print("  ステータス履歴を移行中...")
                    hist_rows = conn.execute("""
                        SELECT 
                            sh.board_code,
                            ou.line_user_id,
                            sh.old_status,
                            sh.new_status,
                            sh.note,
                            sh.created_at
                        FROM old.status_history sh
                        LEFT JOIN old.users ou ON ou.id = sh.user_id
                    """).fetchall()
                    
                    for board_code, line_user_id, old_status, new_status, note, created_at in hist_rows:
                        new_user_id = None
                        if line_user_id:
                            new_user = conn.execute(
                                "SELECT id FROM users.users WHERE line_user_id = ?",
                                (line_user_id,)
                            ).fetchone()
                            if new_user:
                                new_user_id = new_user[0]
                        
                        conn.execute("""
                            INSERT INTO status_history 
                            (board_code, user_id, old_status, new_status, note, created_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (board_code, new_user_id, old_status, new_status, note, created_at))
                    
                    conn.commit()
                    
                    task_count = conn.execute("SELECT COUNT(*) FROM task_status").fetchone()[0]
                    hist_count = conn.execute("SELECT COUNT(*) FROM status_history").fetchone()[0]
                    print(f"  {task_count} 件のタスクステータスと {hist_count} 件の履歴レコードを移行しました")
                    
                    conn.execute("DETACH DATABASE old")
                finally:
                    # 一時バックアップをクリーンアップ
                    if temp_backup.exists():
                        temp_backup.unlink()
            
            conn.execute("DETACH DATABASE users")
    
    print("\n移行完了！")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
