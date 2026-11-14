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

## JSON の扱い

スクレイパは実行状態を PostgreSQL へ書くのと同時に、`data/background_tasks/*.json` にも書いています（`tools/tasks/status.py`）。DB へ書けなかったときの控えを兼ねます。表示側は PostgreSQL を優先し、JSON の `filemtime` が DB の `source_mtime` より新しい場合だけ DB へ取り込み直します（`lib/background_tasks.php`）。

2026-10-01 時点でもこの経路は現役です。JSON を読む経路を消すなら、先にスクレイパ側の JSON 書き込みを止め、DB へ書けないときの扱いを決める必要があります。

## 表示ルール

- `/api/home.php` は PostgreSQL の自治体カードを読み、会議録・例規集の live 実行状態を重ねて返します。
- `/api/task-status.php` も同じ live 状態を使います。
- 実行中タスクの時刻ラベルは `開始` です。
- 待機中タスクの時刻ラベルは `完了` です。
- 実行中は `開始` だけ、停止中は `完了` だけを表示します。
- heartbeat は stale 判定専用です。利用者向け表示には `応答 ...` を出しません。
- カード上段の `DL済` は保存済み実体から再集計した件数です。task item の途中進捗や snapshot 値では水増ししません。
- プログレスバー下は、現在の作業内容を 1 行で表示する場所です。`[500/6312]` や `downloaded=... checked=...` のような内部カウンタは出しません。
- `/api/status-summary.php` の実行状況は `/api/task-status.php` と同じ live 状態から作ります。収集状況ページは前者で描画してから後者で更新するので、出どころが違うと更新のたびに数字が戻って見えます（2026-09-06 には同じ時刻で会議録の取得完了が 1444 と 1308、索引の検索可が 1500/1501 と 1307/1308 に割れていました）。カタログの控えをそのまま返さないこと。
- 会議録の `最新日付` は、検索できる範囲の終わり（`search_coverage.to`）より古く表示しません。鮮度は `scrape_state.json` の `plan_summary.date_max` から取りますが、あれは**その実行が計画した分**の最大日です。途中でエラーになって古い年しか計画しなかった実行が残ると、実際に持っている文書より古い日付になります（仙台市が 2026-02-17 の会議録を検索できるのに 1991-01-14 と表示されていました）。

## 状況 API の控えの新しさ

`/api/status.php`・`/api/status-summary.php` の自治体ごとの状態は、`data/background_tasks/status_api_payload.json` の控えから返します（`homepage_build_status_api_payload_cached`）。実行状況（`task_state_summaries`・`running_tasks`）だけは、要求のたびに `/api/task-status.php` と同じ live 状態に差し替えます。

- 控えが 60 秒より古いと、古い控えをそのまま返し、応答のあとに作り直します。作り直しのきっかけは見に来た要求だけなので、誰も見ていない間は控えが古いまま残ります。
- 長く空いたあとの 1 回目の作り直しは遅く、40 秒〜2 分ほどかかります（2026-11-14 は 43 秒、11-12 は 2 分ほど）。温まっていれば 1 秒ほどです。その間は何度取っても古い控えが返ります。11-12 には、これを「21:04 の控えのまま止まっている」と見誤りました。
- 応答のヘッダ:
  - `X-Status-Cache`: `hit`（60 秒以内の控え）・`stale`（古い控え）・`miss`（控えが無く、その場で作った）
  - `X-Status-Cache-Refresh`: `stale` のときだけ付きます。`started` はこの要求の応答のあとに作り直す、`running` はほかの要求が作り直している最中です。
- 外から今の状態を確かめるときは、本文の `generated_at`（作り直しが終わった時刻）を見ます。古ければ 1〜3 分おいて取り直し、`generated_at` が新しくなるのを待ちます。数秒おきに続けて取っても新しくはなりません。
- 作り直しが 30 秒を超えると、PHP のログに `[status_api] background cache refresh took …s` が出ます。失敗は `[status_api] background cache refresh failed: …` です（実行状況は `[task_status_api]`、トップ API は `[home_api]`）。

## 経緯

2026-05-20 に旧 JSON から PostgreSQL へ移しました（377d105）。一度きりの移行スクリプト `lib/migrate_runtime_state_to_postgres.php` は 2026-10-01 に削除しました。新しい環境では旧 JSON が無いので要りません。

取得済みの会議録・例規集ファイル、OpenSearch index、ユーザーデータ、選挙ポスター掲示場データは、実行状態の整理で消してよいものではありません。
