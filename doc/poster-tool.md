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
`tasks.sqlite` はリモートサーバー上でのみ生成され、デプロイ時に転送・削除されません（rsync exclude）。  
`boards.sqlite` もデプロイ時は転送されません。初回のみ手動で配置してください。
本番でも `data/boards` はサービスディレクトリ配下に置いたまま運用します。
旧 `data/users.sqlite` は移行期間中だけ読み取り互換を持ち、デプロイ時に新配置へ非破壊コピーします。

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

`init_db.py` と `init_users_db.py` は対象SQLiteを再作成します。既存データを保持する運用では実行せず、
ユーザーDBの配置変更には下記の非破壊移行コマンドを使います。

TSV だけ更新したい場合:

```bash
python domains/election_poster_boards/tools/import_tsv.py 14130-kawasaki-shi
```

旧 `dev/boards/*.py` は同じ処理を呼ぶ互換コマンドとして残しています。

既存ユーザーDBのローカル移行は、最初に確認モードで実行します。

```bash
python domains/election_poster_boards/tools/migrate_legacy_users_db.py
python domains/election_poster_boards/tools/migrate_legacy_users_db.py --apply
```

移行はコピーとSQLiteスキーマ検証だけを行い、旧DBを削除しません。

## メモ

- 公開 URL は `自治体コード-ローマ字名称` に統一します。
- ログイン後の戻り先も `slug` を保持します。
- 位置調整権限は管理者のみです。
