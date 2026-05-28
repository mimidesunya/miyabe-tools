-- 掲示場データ用のSQLiteスキーマ
-- 座標はR-Treeインデックスを使用して高速なバウンディングボックスクエリを可能にします。

BEGIN IMMEDIATE;

-- スキーマを再実行する場合に備えて、既存のオブジェクトを削除します。
DROP TRIGGER IF EXISTS trg_boards_rtree_ai;
DROP TRIGGER IF EXISTS trg_boards_rtree_au;
DROP TRIGGER IF EXISTS trg_boards_rtree_ad;
DROP TABLE   IF EXISTS boards_rtree;
DROP TABLE   IF EXISTS boards;

-- CSVデータを保持するメインテーブル。
-- 一部の行には緯度・経度が欠落している場合があるため、これらのカラムはNULLを許容します。
CREATE TABLE boards (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  code    TEXT    NOT NULL UNIQUE,  -- 例: "701-1"
  address TEXT    NOT NULL,         -- 例: "麻生区 東百合丘2丁目3番"
  place   TEXT    NOT NULL,         -- 例: 設置場所の説明や目印
  lat     REAL,                     -- 緯度 (NULL許容)
  lon     REAL,                     -- 経度 (NULL許容)
  CHECK (lat IS NULL OR (lat BETWEEN -90  AND 90)),
  CHECK (lon IS NULL OR (lon BETWEEN -180 AND 180))
);

-- テキスト検索用のインデックス。
CREATE INDEX IF NOT EXISTS idx_boards_address ON boards(address);
CREATE INDEX IF NOT EXISTS idx_boards_place   ON boards(place);

-- バウンディングボックスを使用した高速な空間範囲フィルタリング用のR-Tree。
-- ポイントデータの場合、min==maxとなります。
-- カラム名: X = 経度(lon), Y = 緯度(lat)。
CREATE VIRTUAL TABLE boards_rtree USING rtree(
  id,
  min_lon, max_lon,
  min_lat, max_lat
);

-- R-Treeをメインテーブルと同期させます。
CREATE TRIGGER trg_boards_rtree_ai
AFTER INSERT ON boards
WHEN NEW.lon IS NOT NULL AND NEW.lat IS NOT NULL
BEGIN
  INSERT INTO boards_rtree (id, min_lon, max_lon, min_lat, max_lat)
  VALUES (NEW.id, NEW.lon, NEW.lon, NEW.lat, NEW.lat);
END;

CREATE TRIGGER trg_boards_rtree_au
AFTER UPDATE ON boards
BEGIN
  -- 既存のR-Treeエントリを削除
  DELETE FROM boards_rtree WHERE id = NEW.id;
  -- 更新後に有効な座標がある場合は再挿入
  INSERT INTO boards_rtree (id, min_lon, max_lon, min_lat, max_lat)
  SELECT NEW.id, NEW.lon, NEW.lon, NEW.lat, NEW.lat
  WHERE NEW.lon IS NOT NULL AND NEW.lat IS NOT NULL;
END;

CREATE TRIGGER trg_boards_rtree_ad
AFTER DELETE ON boards
BEGIN
  DELETE FROM boards_rtree WHERE id = OLD.id;
END;

COMMIT;

-- 使用上の注意 ---------------------------------------------------------------
-- 1) boardsテーブルにデータをインポートします（code, address, place, lat, lon）。
--    トリガーにより、緯度・経度が入力されている行については自動的にR-Treeが更新されます。
--
-- 2) バウンディングボックスクエリの例:
--    SELECT b.*
--    FROM boards_rtree r
--    JOIN boards b ON b.id = r.id
--    WHERE r.min_lon <= :max_lon AND r.max_lon >= :min_lon
--      AND r.min_lat <= :max_lat AND r.max_lat >= :min_lat;
--
-- 3) 半径クエリが必要な場合は、まず半径を使用してバウンディングボックスを計算し、
--    その後、必要に応じて結合された行に対して距離で絞り込みを行います。
--
-- 4) SQLiteビルドでR-Treeモジュールが有効である必要があります（Pythonのsqlite3や
--    sqlite3 CLIでは通常デフォルトで有効になっています）。
