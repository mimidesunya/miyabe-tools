# 会議録スクレイパー

`data/municipalities/assembly_minutes_system_urls.tsv` の `system_type` ごとに、議会会議録サイトを巡回してローカル保存するためのツールです。  
JavaScript 前提サイト向けに `Playwright` を利用します。`kensakusystem` 系は静的HTML主体のため HTTP 取得で対応しています。

## 重要

- `robots.txt` では CGI 配下（会議録本体がある領域）へのアクセス制限が示されています。
- 実運用前に利用規約・許諾を確認してください。
- 本ツールは明示確認フラグ `--ack-robots` がないと実行しません。
- 連続アクセス負荷を下げるため、`--delay-seconds` を小さくしすぎないでください。

## セットアップ

```bash
pip install -r dev/requirements/gijiroku.txt
playwright install chromium
```

公開検索は OpenSearch に集約しています。スクレイパは本文とメタ情報を保存し、検索 index は `tools/search/build_opensearch_index.py` がスクレイピング済みファイルから作ります。

## 実行例

最初は件数制限付きで動作確認するのを推奨します。

```bash
python tools/gijiroku/scrapers/gijiroku_com.py \
  --slug 14130-kawasaki-shi \
  --ack-robots \
  --max-meetings 20 \
  --save-html
```

```bash
python tools/gijiroku/scrapers/kaigiroku_net.py \
  --slug 01202-hakodate-shi \
  --ack-robots \
  --max-years 1 \
  --max-meetings 10
```

```bash
python tools/gijiroku/scrapers/dbsr.py \
  --slug 13212-hino-shi \
  --ack-robots \
  --max-meetings 10
```

```bash
python tools/gijiroku/scrapers/kensakusystem.py \
  --slug 02202-hirosaki-shi \
  --ack-robots \
  --max-meetings 10
```

香美市公式サイトの PDF 一覧型:

```bash
python tools/gijiroku/scrapers/kami_city_pdf.py \
  --slug 39212-kami-shi \
  --ack-robots \
  --max-meetings 3
```

自治体公式サイトの `site/gikai` 系 PDF 一覧型:

```bash
python tools/gijiroku/scrapers/site_gikai_pdf.py \
  --slug 23425-kanie-cho \
  --ack-robots \
  --max-meetings 3
```

静的な `kaigiroku/gijiroku` ディレクトリ型:

```bash
python tools/gijiroku/scrapers/static_kaigiroku_dir.py \
  --slug 01226-sunagawa-shi \
  --ack-robots \
  --max-meetings 3
```

`独自`（自治体サイト上の PDF 会議録・共通システム無し）を汎用クロールで取得:

```bash
python tools/gijiroku/scrapers/gikai_pdf.py \
  --slug 01333-shiriuchi-cho \
  --ack-robots \
  --max-meetings 3
```

`gijiroku.com` を全件取得（時間がかかります）:

```bash
python tools/gijiroku/scrapers/gijiroku_com.py --slug 14130-kawasaki-shi --ack-robots
```

全国の `gijiroku.com` / `voices` 対象を既定設定（6 並列・同一ホスト起動間隔 2 秒）で取得する場合:

```bash
python tools/gijiroku/scrape_all_minutes.py --ack-robots --systems gijiroku.com --parallel 6 --per-host-start-interval 2
```

まず 5 自治体だけを対象にして、進捗を親プロセス側で見る場合:

```bash
python tools/gijiroku/scrape_all_minutes.py --ack-robots --systems gijiroku.com --max-targets 5 --parallel 5 --per-host-start-interval 2
```

実装済みの `gijiroku.com` / `voices` / `kaigiroku.net` / `dbsr` / `db-search` / `kaigiroku-indexphp` / `kensakusystem` / `kami-city-pdf` / `site-gikai-pdf` / `static-kaigiroku-dir` / `独自`(汎用PDF) をまとめて回す場合:

```bash
python tools/gijiroku/scrape_all_minutes.py --ack-robots --parallel 8 --per-host-parallel 1 --per-host-start-interval 2
```

`--parallel` はスクレイプ並列です。検索 index の更新は、スクレイプ完了自治体ごとに OpenSearch builder の `--mode update --slug ...` を実行します。更新を止めたい場合だけ `--no-build-index` を付けます。

## OpenSearch index 作成

通常は自治体単位で current alias を更新します。

```bash
python tools/search/build_opensearch_index.py --mode update --doc-type minutes --slug 14130-kawasaki-shi
```

全量再構築が必要な場合だけ、スクレイピング済みファイルから OpenSearch の versioned index を作成して alias を切り替えます。

```bash
python tools/search/build_opensearch_index.py --mode rebuild --doc-type minutes
```

Web画面:

- `/search/?doc_type=minutes`

## 出力

デフォルトでは `slug` から組み立てた既定出力先へ保存されます。  
以下は `14130-kawasaki-shi` 設定の例です。

- `work/gijiroku/14130-kawasaki-shi/meetings_index.json`  
  発見した会議候補一覧（タイトル・URL・年ラベル）
- `work/gijiroku/14130-kawasaki-shi/run_result_YYYYMMDD_HHMMSS.csv`  
  実行結果ログ（各会議のステータス）
- `work/gijiroku/14130-kawasaki-shi/downloads/`  
  年別・会議別サブディレクトリ配下にダウンロード成功ファイル
  例: `downloads/令和7年/健康福祉委員会/*.txt.gz`
- `work/gijiroku/14130-kawasaki-shi/pages/`（`--save-html` 指定時）  
  年別・会議別サブディレクトリ配下に取得失敗時の調査用 HTML
- `work/gijiroku/14130-kawasaki-shi/scrape_state.json`
  レジューム用の状態ファイル
- `work/gijiroku/01202-hakodate-shi/pages/`（`--save-debug-json` 指定時）  
  `kaigiroku.net` API エラー調査用 JSON

既存データの整理:

```bash
php dev/gijiroku/organize_minutes_data.php --slug 14130-kawasaki-shi
```

## オプション

- `--slug` 自治体slug。全国マスタから出力先を解決する
- `--output-dir` 保存先ディレクトリ
- `--headful` ブラウザ表示モードで実行
- `--delay-seconds` 会議ごとの待機秒数（既定: `1.5`）
- `--max-meetings` 処理件数上限（`0` は無制限）
- `--timeout-ms` 操作タイムアウト（ミリ秒）
- `--parallel` 自治体スクレイパの同時実行数
- `--ack-robots` 規約/robots 確認済みフラグ（必須）
- `--save-html` ダウンロード失敗時に会議詳細HTMLを保存
- `--max-years` `kaigiroku.net` 系で取得対象年数を制限
- `--save-debug-json` `kaigiroku.net` 系で調査用 JSON を保存
- `--no-resume` 既存ダウンロードや状態ファイルを無視して先頭から取り直す

## 一覧・レジューム・更新確認の設計

会議録スクレイパは、各サイト固有の「一覧取得」で保存対象の候補を作り、その後の共通処理を `gijiroku_planning.py` に集約します。

- 一覧取得: 各スクレイパが `MeetingItem` 相当の候補リストを作る
- 保存計画: `gijiroku_planning.build_base_plans()` が保存ディレクトリ、ファイル名、重複名の識別子、レジュームキーを決める
- 既存判定: `.txt` / `.txt.gz` や同名の保存済みファイルがあるかだけを見て、ダウンロードが必要な候補を絞る
- レジューム: 前回も未取得が残っていた場合は未取得候補だけを処理する。開催日や年度が推定できる場合は古い未取得分から再開し、日付が不明な場合は一覧順を維持する
- 更新確認: 前回未取得が 0 件で、今回の一覧取得で新しい候補が現れた場合だけダウンロード対象にする。日付が推定できる場合は新しい候補を先に処理する。トップページや一覧で最新更新日が取れるサイトでは、今後その値を事前判定に使う

現時点では、会議録の「新しい順」はサイトごとの一覧/API順に依存します。コード側で全サイト共通に新しい順を保証してはいないため、更新判定に使う日付は `held_on`、タイトル内の日付、年度情報から推定できる範囲に限ります。

### 系統別の更新確認方針

| 系統 | 最終更新日 | 一覧取得 | 日付順の扱い | 対策 |
|---|---|---|---|---|
| `kaigiroku.net` | トップに共通の最終更新日は未確認。API 応答内の日程情報が主材料 | `get_view_years` / `councils/index` / `minutes/get_schedule_all` で構造化取得 | API 順は信頼せず、タイトル・年度から推定 | API で日付フィールドが取れる自治体は `held_on` 化する |
| `dbsr` / `db-search` / `kaigiroku-indexphp` | トップの最終更新日は未確認 | 年別一覧から文書行を取得 | `held_on` を取れるため最も信頼しやすい | 日付範囲と一覧順を state に記録し、差分判定へ使う |
| `gijiroku.com` / `voices` | 共通の最終更新日は未確認 | 検索画面・詳細リンク探索 | 年度・タイトル推定が中心 | 一覧順を検証し、推定不能なものは順序保証なしとして扱う |
| `kensakusystem` | 共通の最終更新日は未確認 | `See.exe` ツリーと `ResultFrame.exe` | 年度・ツリー順が中心 | 年度推定で粗く範囲管理し、日付順は保証しない |
| `kami-city-pdf` / `site-gikai-pdf` | 自治体ページ次第 | PDF リンク一覧 | 年度推定が中心 | 静的ページの更新日・年度を取れる場合のみ使う |
| `static-kaigiroku-dir` | 自治体ページ次第 | HTML/PDF リンク巡回 | サイト構造次第 | まず一覧ページの探索範囲を抑え、日付粒度を state に残す |

`scrape_state.json` の `plan_summary` には、候補総数、未取得数、日付判明数、日付粒度、一覧の元順序が昇順/降順/混在かを保存します。これにより、最終更新日が取れない系統でも「一覧の差分で十分か」「日付順を信じて打ち切れるか」を後から判定できます。

`kaigiroku.net` の公開 API を実サンプルで確認したところ、`get_view_years` と `councils/index` は軽量ですが、`minutes/get_schedule_all` の行は主に `schedule_id` / `name` / `page_no` で、独立した最終更新日フィールドは確認できませんでした。そのため `name` の `01月29日－01号` のような日付と年度を使い、`held_on` を保存します。

`dbsr` 系は一覧ページから `held_on` を取れる一方、HTTP `Last-Modified` は動的ページの応答時刻になる場合があり、最終更新日の根拠としては使いません。前回の `plan_summary` で「未取得 0 件・一覧順が降順・日付順を信頼可能」と分かっている場合だけ、既知 URL だけのページに到達した時点で一覧ページ送りを止め、旧 `meetings_index.json` と merge します。初回や順序が不明な場合は従来通り全件一覧を取ります。

## 補足

`gijiroku.com` / `voices` 系はサイト側のUI変更により、クリック対象ラベルやダウンロード導線が変わる場合があります。  
その場合は `--headful --save-html` で挙動確認し、`scrapers/gijiroku_com.py` 内のセレクタを調整してください。

`kaigiroku.net` 系は一覧・本文とも `/dnp/search/` API を使っています。UI 変更よりも API 仕様変更の影響を受けやすいので、異常時は `--save-debug-json` 付きでレスポンスを確認してください。

`dbsr` / `db-search` / `kaigiroku-indexphp` 系は年別一覧から `Template=list` をたどり、検索結果一覧のページ送りを巡回して日付ごとに `本文` を抽出します。  
`--max-meetings` は候補列挙の途中でも効くので、最初の動作確認を短く回したいときに便利です。

`kensakusystem` 系は `See.exe` の年別ツリーを再帰的にたどり、`PRINT_ALL` の全文表示を使って本文を保存します。  
`--headful` は他スクリプトとの互換のため受理しますが、取得処理自体はブラウザ描画を使いません。

公開 URL の `slug` は `自治体コード-ローマ字名称` に統一します。既存 slug や自治体コードだけの指定も alias として受け付けます。
