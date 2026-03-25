# ポスター支援ツール

選挙ポスター掲示場の位置確認、作業進捗の共有、LINE ログイン連携を行う Web ツールです。  
掲示場機能は自治体スラッグ単位で分離されており、複数自治体を同じUIで切り替えられます。

## 画面

- マップ: `/boards/{slug}/`
- 一覧: `/boards/list.php?slug={slug}`
- ユーザー一覧: `/boards/users.php?slug={slug}`

例:

- `/boards/kawasaki-shi/`
- `/boards/higashikurume-shi/`

## データ構成

- 掲示場マスタ: `data/boards/{slug}/boards.sqlite`
- タスク状態: `data/boards/{slug}/tasks.sqlite` (リモートでのみ作成)
- 共通ユーザーDB: `data/users.sqlite`
- 初期TSV: `tools/boards/data/{slug}/data.tsv`

`users.sqlite` は全自治体で共有、`boards.sqlite` / `tasks.sqlite` は自治体ごとに分離されます。  
`tasks.sqlite` はリモートサーバー上でのみ生成され、デプロイ時に転送・削除されません（rsync exclude）。  
`boards.sqlite` もデプロイ時は転送されません。初回のみ手動で配置してください。

## 設定

`data/config.json` の `MUNICIPALITIES.{slug}.boards` を使います。

主な項目:

- `enabled`: 画面を有効化するか
- `title`: 画面表示名
- `allow_offset`: 位置調整モードを許可するか

## 初期化

```bash
python tools/boards/init_db.py kawasaki-shi
python tools/boards/init_users_db.py
```

TSV だけ更新したい場合:

```bash
python tools/boards/import_tsv.py kawasaki-shi
```

## メモ

- URL は `slug` ごとに固定です。
- ログイン後の戻り先も `slug` を保持します。
- 位置調整権限は自治体ごとに切り替えられます。
