# 選挙ポスター掲示場支援ツール

選挙ポスター掲示場の位置確認、作業進捗の共有、LINE ログイン連携を行う Web ツールです。
会議録・例規集・OpenSearch・公開文書APIとは別の業務領域として、
`domains/election_poster_boards/` が実装とデータライフサイクルを所有します。
掲示場機能は自治体スラッグ単位で分離されており、複数自治体を同じUIで切り替えられます。

`app/boards/` と `app/line/` は既存URLを維持するための公開アダプターです。

## 画面

- マップ: `/boards/{slug}/`
- 一覧: `/boards/list.php?slug={slug}`
- ユーザー一覧: `/boards/users.php?slug={slug}`

例:

- `/boards/14130-kawasaki-shi/`
- `/boards/13222-higashikurume-shi/`

## データ構成

- 掲示場マスタ: `data/boards/{slug}/boards.sqlite`
- タスク状態: `data/boards/{slug}/tasks.sqlite` (リモートでのみ作成)
- 共通ユーザーDB: `data/boards/users.sqlite`
- 初期TSV: `dev/boards/data/{slug}/data.tsv`

`users.sqlite` は全自治体で共有、`boards.sqlite` / `tasks.sqlite` は自治体ごとに分離されます。  
`tasks.sqlite` はリモートサーバー上でのみ生成され、デプロイ時に WAL / SHM を含めて転送・削除されません（rsync exclude）。

`boards.sqlite` と `users.sqlite` も WAL / SHM を含めてデプロイ対象外です。`boards.sqlite` は初回のみ手動で配置してください。
本番でも `data/boards` はサービスディレクトリ配下に置いたまま運用します。

## 設定

通常は `data/config.json` に自治体ごとの設定は要りません。  
`data/municipalities` のマスタと `slug` から、DB パスや表示名を既定値で導出します。

主な項目:

- `db_path` / `tasks_db_path`
  - 既定値と違う保存先にしたい場合だけ指定

## 初期化

```bash
python domains/election_poster_boards/tools/init_db.py 14130-kawasaki-shi
python domains/election_poster_boards/tools/init_users_db.py
```

`init_db.py` と `init_users_db.py` は対象SQLiteを再作成します。既存データを保持する運用では実行しません。

TSV だけ更新したい場合:

```bash
python domains/election_poster_boards/tools/import_tsv.py 14130-kawasaki-shi
```

住所から緯度経度を付けるには `geocode_boards.py` を使います（`data/config.json` に `GOOGLE_MAPS_API_KEY` が要ります）。

## 入力TSV

`dev/boards/data/{slug}/data.tsv` はヘッダー行の無いタブ区切りで、列は次の順です。

1. `id`: 掲示場番号（必須）
2. `address`: 住所（必須）
3. `latitude`: 緯度
4. `longitude`: 経度
5. `memo`: メモ

ツールは引数の slug をそのままディレクトリ名に使います。いま `dev/boards/data/` にあるディレクトリは `kawasaki-shi` のようにコードの無い名前なので、`14130-kawasaki-shi` で使うにはディレクトリ名を合わせてください（2026-10-01 時点で未整理）。

## メモ

- 公開 URL は `自治体コード-ローマ字名称` に統一します。
- ログイン後の戻り先も `slug` を保持します。
- 位置調整権限は管理者のみです。
