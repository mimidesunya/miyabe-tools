# 川崎市議会 会議録スクレイパー

`www13.gijiroku.com/kawasaki_council` の年別一覧を自動巡回し、ダウンロード可能な会議録（テキスト/Word）を取得するためのツールです。  
JavaScript 前提サイトのため、`Playwright` でブラウザ操作を行います。

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
python tools/gijiroku/scrape_kawasaki_minutes.py \
  --ack-robots \
  --max-meetings 20 \
  --save-html
```

全件取得（時間がかかります）:

```bash
python tools/gijiroku/scrape_kawasaki_minutes.py --ack-robots
```

## SQLite全文検索インデックス作成

スクレイプ後に、以下で全文検索用DBを作成します。

```bash
python tools/gijiroku/build_minutes_index.py --slug kawasaki
```

生成先:

- `data/config.json` の `MUNICIPALITIES.kawasaki.gijiroku.db_path`
- `tools/gijiroku/schema.sql` にDBスキーマ定義

Web画面:

- `/gijiroku/?slug=kawasaki`（例: `http://localhost/gijiroku/?slug=kawasaki`）

## 出力

デフォルトでは `data/config.json` の `MUNICIPALITIES.kawasaki.gijiroku` に定義された出力先へ保存されます。  
初期設定の例は以下です。

- `data/gijiroku/kawasaki_council/meetings_index.json`  
  発見した会議候補一覧（タイトル・URL・年ラベル）
- `data/gijiroku/kawasaki_council/minutes.sqlite`  
  Web全文検索用SQLite（FTS5）
- `data/gijiroku/kawasaki_council/run_result_YYYYMMDD_HHMMSS.csv`  
  実行結果ログ（各会議のステータス）
- `data/gijiroku/kawasaki_council/downloads/`  
  年別サブディレクトリ配下にダウンロード成功ファイル（例: `downloads/令和7年/*.txt`）
- `data/gijiroku/kawasaki_council/pages/`（`--save-html` 指定時）  
  年別サブディレクトリ配下に取得失敗時の調査用 HTML

## オプション

- `--slug` 自治体slug。`config.json` の出力先を使う
- `--output-dir` 保存先ディレクトリ
- `--headful` ブラウザ表示モードで実行
- `--delay-seconds` 会議ごとの待機秒数（既定: `1.5`）
- `--max-meetings` 処理件数上限（`0` は無制限）
- `--timeout-ms` 操作タイムアウト（ミリ秒）
- `--ack-robots` 規約/robots 確認済みフラグ（必須）
- `--save-html` ダウンロード失敗時に会議詳細HTMLを保存

## 補足

サイト側のUI変更により、クリック対象ラベルやダウンロード導線が変わる場合があります。  
その場合は `--headful --save-html` で挙動確認し、`scrape_kawasaki_minutes.py` 内のセレクタを調整してください。
