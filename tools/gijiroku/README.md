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
pip install -r tools/gijiroku/requirements.txt
playwright install chromium
```

公開検索は OpenSearch に集約しています。スクレイパは本文とメタ情報を保存し、検索 index は `tools/search/build_opensearch_index.py` がスクレイピング済みファイルから作ります。

## 実行例

最初は件数制限付きで動作確認するのを推奨します。

```bash
python tools/gijiroku/scrape_gijiroku_com.py \
  --slug 14130-kawasaki-shi \
  --ack-robots \
  --max-meetings 20 \
  --save-html
```

```bash
python tools/gijiroku/scrape_kaigiroku_net.py \
  --slug 01202-hakodate-shi \
  --ack-robots \
  --max-years 1 \
  --max-meetings 10
```

```bash
python tools/gijiroku/scrape_dbsr.py \
  --slug 13212-hino-shi \
  --ack-robots \
  --max-meetings 10
```

```bash
python tools/gijiroku/scrape_kensakusystem.py \
  --slug 02202-hirosaki-shi \
  --ack-robots \
  --max-meetings 10
```

香美市公式サイトの PDF 一覧型:

```bash
python tools/gijiroku/scrape_kami_city_pdf.py \
  --slug 39212-kami-shi \
  --ack-robots \
  --max-meetings 3
```

自治体公式サイトの `site/gikai` 系 PDF 一覧型:

```bash
python tools/gijiroku/scrape_site_gikai_pdf.py \
  --slug 23425-kanie-cho \
  --ack-robots \
  --max-meetings 3
```

静的な `kaigiroku/gijiroku` ディレクトリ型:

```bash
python tools/gijiroku/scrape_static_kaigiroku_dir.py \
  --slug 01226-sunagawa-shi \
  --ack-robots \
  --max-meetings 3
```

`gijiroku.com` を全件取得（時間がかかります）:

```bash
python tools/gijiroku/scrape_gijiroku_com.py --slug 14130-kawasaki-shi --ack-robots
```

全国の `gijiroku.com` / `voices` 対象を既定設定（6 並列・同一ホスト起動間隔 2 秒）で取得する場合:

```bash
python tools/gijiroku/scrape_all_minutes.py --ack-robots --systems gijiroku.com --parallel 6 --per-host-start-interval 2
```

まず 5 自治体だけを対象にして、進捗を親プロセス側で見る場合:

```bash
python tools/gijiroku/scrape_all_minutes.py --ack-robots --systems gijiroku.com --max-targets 5 --parallel 5 --per-host-start-interval 2
```

実装済みの `gijiroku.com` / `voices` / `kaigiroku.net` / `dbsr` / `db-search` / `kaigiroku-indexphp` / `kensakusystem` / `kami-city-pdf` / `site-gikai-pdf` / `static-kaigiroku-dir` をまとめて回す場合:

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
php tools/gijiroku/organize_minutes_data.php --slug 14130-kawasaki-shi
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

## 補足

`gijiroku.com` / `voices` 系はサイト側のUI変更により、クリック対象ラベルやダウンロード導線が変わる場合があります。  
その場合は `--headful --save-html` で挙動確認し、`scrape_gijiroku_com.py` 内のセレクタを調整してください。

`kaigiroku.net` 系は一覧・本文とも `/dnp/search/` API を使っています。UI 変更よりも API 仕様変更の影響を受けやすいので、異常時は `--save-debug-json` 付きでレスポンスを確認してください。

`dbsr` / `db-search` / `kaigiroku-indexphp` 系は年別一覧から `Template=list` をたどり、検索結果一覧のページ送りを巡回して日付ごとに `本文` を抽出します。  
`--max-meetings` は候補列挙の途中でも効くので、最初の動作確認を短く回したいときに便利です。

`kensakusystem` 系は `See.exe` の年別ツリーを再帰的にたどり、`PRINT_ALL` の全文表示を使って本文を保存します。  
`--headful` は他スクリプトとの互換のため受理しますが、取得処理自体はブラウザ描画を使いません。

公開 URL の `slug` は `自治体コード-ローマ字名称` に統一します。既存 slug や自治体コードだけの指定も alias として受け付けます。
