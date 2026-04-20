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

`build_minutes_index.py` と Web 検索は `SudachiPy` を前提にします。  
スクレイパ用 Docker image だけでなく、Web 用 PHP image にも Python / SudachiPy を入れて同じ分かち書きを使います。

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

実装済みの `gijiroku.com` / `voices` / `kaigiroku.net` / `dbsr` / `db-search` / `kaigiroku-indexphp` / `kensakusystem` をまとめて回す場合:

```bash
python tools/gijiroku/scrape_all_minutes.py --ack-robots --parallel 8 --per-host-parallel 1 --per-host-start-interval 2
```

`--parallel` はスクレイプ並列、`--index-parallel` は `minutes.sqlite` 更新並列です。  
この一括実行では、各自治体のスクレイプ完了後に `minutes.sqlite` も更新されるため、バッチ進行中でも完了済み自治体から順に検索可能になります。  
自動更新を止めたい場合だけ `--no-build-index` を付けてください。

## SQLite全文検索インデックス作成

単発で DB を再生成したい場合は、以下を実行します。

```bash
python tools/gijiroku/build_minutes_index.py --slug 14130-kawasaki-shi
```

生成先:

- 既定値 `data/gijiroku/{slug}/minutes.sqlite`
- `tools/gijiroku/schema.sql` にDBスキーマ定義

この DB の FTS 部分は、生テキストではなく SudachiPy で分かち書きした terms カラムを検索対象にします。

Web画面:

- `/gijiroku/?slug={slug}`（例: `http://localhost/gijiroku/?slug=14130-kawasaki-shi`）
- `/gijiroku/cross.php`（自治体横断での会議録全文検索）

## 出力

デフォルトでは `slug` から組み立てた既定出力先へ保存されます。  
以下は `14130-kawasaki-shi` 設定の例です。

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
- `--index-parallel` `minutes.sqlite` 更新の同時実行数
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
