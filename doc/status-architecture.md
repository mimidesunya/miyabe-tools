# 実行状態管理の設計

トップページとステータス表示の正本は PostgreSQL です。会議録・例規集の取得ファイルそのものは引き続き `data/gijiroku` / `data/reiki` と `work/` に置きますが、「いま何件終わったか」「どの自治体が処理中か」「最後にいつ動いたか」は PostgreSQL の管理テーブルから読む方針に統一します。

## 正本

- `management_task_statuses`
  - バッチ単位の実行状態です。
  - `task_key` ごとに `running`、`heartbeat_at`、`updated_at_text`、旧 JSON から取り込んだ場合の `source_mtime`、元の状態 JSON を保持します。
- `processing_task_items`
  - 自治体単位の実行状態です。
  - 主キーは `(task_key, slug)` です。
  - `feature_key` は `gijiroku` / `reiki`、`task_area` は `scrape` / `index` に正規化します。
  - 画面に出す進捗、エラー、警告の元になる行です。
- `homepage_municipality_cards`
  - トップページの自治体カードの派生ビューです。
  - 重いファイル走査を毎回行わないための materialized view 相当であり、正本ではありません。
- `homepage_payload_meta`
  - トップページ全体の派生メタ情報です。
  - 実行中タスクの概要はリクエスト時に live 状態で上書きします。

## 旧 JSON の扱い

`data/background_tasks/*.json` は移行期間の取り込み元と監査用の控えです。表示側は PostgreSQL を優先し、旧 JSON の `filemtime` が DB の `source_mtime` より新しい場合だけ DB へ取り込み直します。

この互換経路は、すべてのスクレイパが PostgreSQL 書き込みを含む新イメージで数サイクル正常に動いたあとに削除します。

## 表示ルール

- `/api/home.php` は PostgreSQL の自治体カードを読み、会議録・例規集の live 実行状態を重ねて返します。
- `/api/task-status.php` も同じ live 状態を使います。
- 実行中タスクの時刻ラベルは `開始` です。
- 待機中タスクの時刻ラベルは `完了` です。
- 実行中は `開始` だけ、停止中は `完了` だけを表示します。
- heartbeat は stale 判定専用です。利用者向け表示には `応答 ...` を出しません。
- カード上段の `DL済` は保存済み実体から再集計した件数です。task item の途中進捗や snapshot 値では水増ししません。
- プログレスバー下は、現在の作業内容を 1 行で表示する場所です。`[500/6312]` や `downloaded=... checked=...` のような内部カウンタは出しません。

## 移行手順

1. スクレイパを停止します。
2. Web 側 compose を更新し、PostgreSQL を起動します。
3. `php /var/www/lib/migrate_runtime_state_to_postgres.php` を PHP コンテナ内で実行します。
4. スクレイパイメージを PostgreSQL 対応版へ更新します。
5. Web とスクレイパを起動します。
6. `/api/home.php` と `/api/task-status.php` が同じ進捗・時刻を返すことを確認します。
7. 数サイクル正常に動いたあと、旧 JSON 互換経路と不要ファイルを削除します。

## 削除してよいもの

検証後に削除候補になるのは、旧 runtime JSON を表示正本として扱うコードと、古い `home_api_payload.json` 依存です。取得済みの会議録・例規集ファイル、OpenSearch index、ユーザーデータ、掲示板データは削除対象ではありません。

削除は「DB へ移ったこと」だけで行わず、少なくとも会議録・例規集・検索反映の通常サイクルが成功してから行います。
