-- タスクステータス追跡用のSQLiteスキーマ
-- このデータベースは自治体ごとに作成され、ATTACHを使用してusers.sqliteのユーザーを参照します。

BEGIN IMMEDIATE;

DROP TRIGGER IF EXISTS trg_task_status_hist;
DROP TABLE   IF EXISTS status_history;
DROP TABLE   IF EXISTS task_status;

-- 各掲示場のタスクステータス追跡
-- アタッチされたusers.sqliteデータベースのユーザーを参照します。
CREATE TABLE task_status (
  board_code    TEXT    PRIMARY KEY,
  status        TEXT    NOT NULL DEFAULT 'pending',  -- pending(未着手), in_progress(着手中), done(完了), issue(異常)
  updated_by    INTEGER NOT NULL,                    -- アタッチされたDBの users.users(id) を参照
  last_comment  TEXT,
  updated_at    TEXT    DEFAULT (datetime('now')),
  CHECK (status IN ('pending', 'in_progress', 'done', 'issue'))
);

CREATE INDEX IF NOT EXISTS idx_task_status_updated_by ON task_status(updated_by);
CREATE INDEX IF NOT EXISTS idx_task_status_status ON task_status(status);

-- すべてのステータス変更とコメントの履歴ログ
CREATE TABLE status_history (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  board_code  TEXT    NOT NULL,
  user_id     INTEGER NOT NULL,         -- アタッチされたDBの users.users(id) を参照
  old_status  TEXT,
  new_status  TEXT,
  note        TEXT,
  created_at  TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_status_history_board_code ON status_history(board_code);
CREATE INDEX IF NOT EXISTS idx_status_history_user_id ON status_history(user_id);
CREATE INDEX IF NOT EXISTS idx_status_history_created_at ON status_history(created_at);

-- ステータス変更をログに記録するトリガー
CREATE TRIGGER trg_task_status_hist
AFTER UPDATE ON task_status
WHEN OLD.status IS NOT NEW.status
BEGIN
  INSERT INTO status_history (board_code, user_id, old_status, new_status, note)
  VALUES (NEW.board_code, NEW.updated_by, OLD.status, NEW.status, NEW.last_comment);
END;

COMMIT;

-- 使用上の注意 ---------------------------------------------------------------
-- このデータベースは自治体（スラッグ）ごとに作成されます。
-- クエリを実行する前に、共有ユーザーデータベースをアタッチしてください:
--   ATTACH DATABASE '/var/www/data/users.sqlite' AS users;
-- 
-- その後、ユーザー情報と結合して取得できます:
--   SELECT ts.*, u.name, u.avatar
--   FROM task_status ts
--   LEFT JOIN users.users u ON u.id = ts.updated_by
