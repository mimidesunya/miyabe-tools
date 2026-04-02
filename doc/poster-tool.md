# ポスター支援ツール

選挙ポスター掲示場の位置確認、作業進捗の共有、LINE ログイン連携を行う Web ツールです。  
掲示場機能は自治体スラッグ単位で分離されており、複数自治体を同じUIで切り替えられます。

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
- 共通ユーザーDB: `data/users.sqlite`
- 初期TSV: `tools/boards/data/{slug}/data.tsv`

`users.sqlite` は全自治体で共有、`boards.sqlite` / `tasks.sqlite` は自治体ごとに分離されます。  
`tasks.sqlite` はリモートサーバー上でのみ生成され、デプロイ時に転送・削除されません（rsync exclude）。  
`boards.sqlite` もデプロイ時は転送されません。初回のみ手動で配置してください。
本番でも `data/boards` と `data/users.sqlite` はサービスディレクトリ配下に置いたまま運用します。

## 設定

通常は `data/config.json` に自治体ごとの設定は要りません。  
`data/municipalities` のマスタと `slug` から、DB パスや表示名を既定値で導出します。

主な項目:

- `db_path` / `tasks_db_path`
  - 既定値と違う保存先にしたい場合だけ指定

## 初期化

```bash
python tools/boards/init_db.py 14130-kawasaki-shi
python tools/boards/init_users_db.py
```

TSV だけ更新したい場合:

```bash
python tools/boards/import_tsv.py 14130-kawasaki-shi
```

## メモ

- 公開 URL は `自治体コード-ローマ字名称` に統一します。
- ログイン後の戻り先も `slug` を保持します。
- 位置調整権限は管理者のみです。
