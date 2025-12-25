-- LINEユーザー管理用の共有SQLiteスキーマ（全自治体で共有）
-- このデータベースは /var/www/data/users.sqlite に一度だけ作成します。

BEGIN IMMEDIATE;

DROP TABLE IF EXISTS users;

-- ユーザーテーブル: LINEユーザー情報を保存します。
CREATE TABLE users (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  line_user_id    TEXT    NOT NULL UNIQUE,  -- LINEの固有ユーザーID
  name            TEXT,                      -- LINEの表示名
  avatar          TEXT,                      -- LINEのプロフィール画像URL
  created_at      TEXT    DEFAULT (datetime('now')),
  updated_at      TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_users_line_user_id ON users(line_user_id);

COMMIT;

-- 使用上の注意 ---------------------------------------------------------------
-- このデータベースはすべての自治体で共有されます。
-- 各自治体の tasks.sqlite は ATTACH DATABASE を使用してユーザーを参照します。
-- 
-- tasks.sqlite のクエリでこのデータベースをアタッチする方法:
--   ATTACH DATABASE '/var/www/data/users.sqlite' AS users;
-- その後、 users.users テーブルとして参照します。
