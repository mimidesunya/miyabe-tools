# 選挙掲示板データベースツール

このディレクトリには、選挙ポスター掲示板データベースの初期化、データインポート、および管理のためのツールが含まれています。

## ディレクトリ構成

```
tools/boards/
├── boards.sql              # boards.sqlite のスキーマ（掲示板位置情報）
├── tasks.sql               # tasks.sqlite のスキーマ（タスク状態追跡）
├── users.sql               # users.sqlite のスキーマ（ユーザー管理）
├── init_db.py              # データベース初期化スクリプト (boards.sqlite, tasks.sqlite)
├── init_users_db.py        # ユーザーデータベース初期化スクリプト
├── import_tsv.py           # TSVデータの再インポート（タスク履歴を保持）
├── geocode_boards.py       # Google Maps APIを使用したジオコーディング
├── migrate_users.py        # ユーザーデータの移行ツール
└── data/                   # 自治体ごとのデータディレクトリ
    └── {slug}/
        └── data.tsv        # 掲示板データTSV
```

## 使い方

### 1. データベースの初期化 (init_db.py)

指定した自治体（slug）のデータベースを初期化します。
`tools/boards/data/{slug}/data.tsv` が存在する必要があります。

**注意:** 既存の `boards.sqlite` と `tasks.sqlite` は削除され、再作成されます。データはすべてリセットされます。

```bash
python tools/boards/init_db.py <slug>
```

使用例:
```bash
# 川崎市 (tools/boards/data/kawasaki/data.tsv を使用)
python tools/boards/init_db.py kawasaki
```

作成されるファイル:
- `data/boards/{slug}/boards.sqlite`
- `data/boards/{slug}/tasks.sqlite`

### 2. データの再インポート (import_tsv.py)

タスクの進捗状況（`tasks.sqlite`）を保持したまま、掲示板データ（`boards.sqlite`）のみを更新します。
座標の修正や住所の変更などを反映させる場合に便利です。

```bash
python tools/boards/import_tsv.py <slug>
```

使用例:
```bash
python tools/boards/import_tsv.py kawasaki
```

### 3. ジオコーディング (geocode_boards.py)

TSVファイルの住所情報から緯度経度を取得し、TSVファイルを更新します。
`data/config.json` に `GOOGLE_MAPS_API_KEY` が設定されている必要があります。

```bash
python tools/boards/geocode_boards.py <slug>
```

### 4. ユーザーデータベースの初期化

ユーザー管理用のデータベースを作成します。

```bash
python tools/boards/init_users_db.py
```

## データ形式

### TSV ファイル形式

`tools/boards/data/{slug}/data.tsv` は以下のカラムを持つタブ区切りテキストです：

1. `id`: 掲示板番号 (必須)
2. `address`: 住所 (必須)
3. `latitude`: 緯度 (オプション)
4. `longitude`: 経度 (オプション)
5. `memo`: メモ (オプション)

ヘッダー行は不要です。

