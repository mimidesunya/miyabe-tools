# 会議録スクレイパー

`work/municipalities/assembly_minutes_system_urls.tsv` の `system_type` ごとに、議会会議録サイトを巡回してローカル保存するためのツールです。  
JavaScript 前提サイトのため、`Playwright` を利用します。

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

## 実行例

最初は件数制限付きで動作確認するのを推奨します。

```bash
python tools/gijiroku/scrape_gijiroku_com.py \
  --slug kawasaki-shi \
  --ack-robots \
  --max-meetings 20 \
  --save-html
```

```bash
python tools/gijiroku/scrape_kaigiroku_net.py \
  --slug 01202-hakodate \
  --ack-robots \
  --max-years 1 \
  --max-meetings 10
```

```bash
python tools/gijiroku/scrape_dbsr.py \
  --slug hino-shi \
  --ack-robots \
  --max-meetings 10
```

`gijiroku.com` を全件取得（時間がかかります）:

```bash
python tools/gijiroku/scrape_gijiroku_com.py --slug kawasaki-shi --ack-robots
```

全国の `gijiroku.com` 対象を順番に取得する場合:

```bash
python tools/gijiroku/scrape_all_gijiroku_com.py --ack-robots
```

まず 5 自治体を並列で回し、進捗を親プロセス側で見る場合:

```bash
python tools/gijiroku/scrape_all_gijiroku_com.py --ack-robots --max-targets 5 --parallel 5
```

実装済みの `gijiroku.com` / `kaigiroku.net` / `dbsr` をまとめて回す場合:

```bash
python tools/gijiroku/scrape_all_minutes.py --ack-robots --parallel 4 --per-host-parallel 1
```

## SQLite全文検索インデックス作成

スクレイプ後に、以下で全文検索用DBを作成します。

```bash
python tools/gijiroku/build_minutes_index.py --slug kawasaki-shi
```

生成先:

- `data/config.json` の `MUNICIPALITIES.{slug}.gijiroku.db_path`
- `tools/gijiroku/schema.sql` にDBスキーマ定義

Web画面:

- `/gijiroku/?slug={slug}`（例: `http://localhost/gijiroku/?slug=kawasaki-shi`）

## 出力

デフォルトでは `data/config.json` の `MUNICIPALITIES.{slug}.gijiroku` に定義された出力先へ保存されます。  
以下は `kawasaki-shi` 設定の例です。

- `work/gijiroku/14130-kawasaki-shi/meetings_index.json`  
  発見した会議候補一覧（タイトル・URL・年ラベル）
- `data/gijiroku/14130-kawasaki-shi/minutes.sqlite`  
  Web全文検索用SQLite（FTS5）
- `work/gijiroku/14130-kawasaki-shi/run_result_YYYYMMDD_HHMMSS.csv`  
  実行結果ログ（各会議のステータス）
- `work/gijiroku/14130-kawasaki-shi/downloads/`  
  年別・会議別サブディレクトリ配下にダウンロード成功ファイル
  例: `downloads/令和7年/健康福祉委員会/*.txt.gz`
- `work/gijiroku/14130-kawasaki-shi/pages/`（`--save-html` 指定時）  
  年別・会議別サブディレクトリ配下に取得失敗時の調査用 HTML
- `work/gijiroku/14130-kawasaki-shi/scrape_state.json`
  レジューム用の状態ファイル
- `work/gijiroku/01202-hakodate/pages/`（`--save-debug-json` 指定時）  
  `kaigiroku.net` API エラー調査用 JSON

既存データの整理:

```bash
php tools/gijiroku/organize_minutes_data.php --slug kawasaki-shi
```

## オプション

- `--slug` 自治体slug。`config.json` の出力先を使う
- `--output-dir` 保存先ディレクトリ
- `--headful` ブラウザ表示モードで実行
- `--delay-seconds` 会議ごとの待機秒数（既定: `1.5`）
- `--max-meetings` 処理件数上限（`0` は無制限）
- `--timeout-ms` 操作タイムアウト（ミリ秒）
- `--ack-robots` 規約/robots 確認済みフラグ（必須）
- `--save-html` ダウンロード失敗時に会議詳細HTMLを保存
- `--max-years` `kaigiroku.net` 系で取得対象年数を制限
- `--save-debug-json` `kaigiroku.net` 系で調査用 JSON を保存
- `--no-resume` 既存ダウンロードや状態ファイルを無視して先頭から取り直す

## 補足

`gijiroku.com` 系はサイト側のUI変更により、クリック対象ラベルやダウンロード導線が変わる場合があります。  
その場合は `--headful --save-html` で挙動確認し、`scrape_gijiroku_com.py` 内のセレクタを調整してください。

`kaigiroku.net` 系は一覧・本文とも `/dnp/search/` API を使っています。UI 変更よりも API 仕様変更の影響を受けやすいので、異常時は `--save-debug-json` 付きでレスポンスを確認してください。

`dbsr` 系は年別一覧から `Template=list` をたどり、検索結果一覧のページ送りを巡回して日付ごとに `本文` を抽出します。  
`--max-meetings` は候補列挙の途中でも効くので、最初の動作確認を短く回したいときに便利です。

新規追加の `slug` は `自治体コード-ローマ字名称` を推奨します。既存 `slug` は互換性のためそのまま利用できます。
