# 選挙ポスター掲示場データ（互換入口）

実装の正本は `domains/election_poster_boards/` へ移動しました。
このディレクトリには、既存コマンドを壊さないためのPythonラッパーと、
自治体ごとの入力TSVだけを残しています。

## ディレクトリ構成

```
dev/boards/
├── *.py                    # domains/election_poster_boards/tools への互換入口
└── data/                   # 自治体ごとの入力データ
    └── {slug}/
        └── data.tsv        # 選挙ポスター掲示場データTSV
```

新しい手順やスキーマは `doc/poster-tool.md` と
`domains/election_poster_boards/README.md` を参照してください。

## 使い方

### 1. データベースの初期化 (init_db.py)

指定した自治体（slug）のデータベースを初期化します。
`dev/boards/data/{slug}/data.tsv` が存在する必要があります。

**注意:** 既存の `boards.sqlite` と `tasks.sqlite` は削除され、再作成されます。データはすべてリセットされます。

```bash
python dev/boards/init_db.py <slug>
```

使用例:
```bash
# 川崎市 (dev/boards/data/14130-kawasaki-shi/data.tsv を使用)
python dev/boards/init_db.py 14130-kawasaki-shi
```

作成されるファイル:
- `data/boards/{slug}/boards.sqlite`
- `data/boards/{slug}/tasks.sqlite`

### 2. データの再インポート (import_tsv.py)

タスクの進捗状況（`tasks.sqlite`）を保持したまま、掲示場データ（`boards.sqlite`）のみを更新します。
座標の修正や住所の変更などを反映させる場合に便利です。

```bash
python dev/boards/import_tsv.py <slug>
```

使用例:
```bash
python dev/boards/import_tsv.py 14130-kawasaki-shi
```

### 3. ジオコーディング (geocode_boards.py)

TSVファイルの住所情報から緯度経度を取得し、TSVファイルを更新します。
`data/config.json` に `GOOGLE_MAPS_API_KEY` が設定されている必要があります。

```bash
python dev/boards/geocode_boards.py <slug>
```

### 4. ユーザーデータベースの初期化

ユーザー管理用のデータベースを作成します。

```bash
python dev/boards/init_users_db.py
```

## データ形式

### TSV ファイル形式

`dev/boards/data/{slug}/data.tsv` は以下のカラムを持つタブ区切りテキストです：

1. `id`: 掲示場番号 (必須)
2. `address`: 住所 (必須)
3. `latitude`: 緯度 (オプション)
4. `longitude`: 経度 (オプション)
5. `memo`: メモ (オプション)

ヘッダー行は不要です。

