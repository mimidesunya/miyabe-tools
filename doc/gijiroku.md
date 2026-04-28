# 会議録ツール

議会会議録のスクレイピング結果を SQLite FTS5 に登録し、Web 上で検索するためのツールです。  
日本語の分かち書きと語形正規化には `SudachiPy` を使います。
ビューアは自治体切り替えに対応し、利用可能な自治体だけ検索画面を有効化します。

## 画面

- 会議録検索: `/gijiroku/?slug={slug}`
- 会議録横断検索: `/gijiroku/cross.php`
- 会議録検索 API 入口: `/api/gijiroku/`
- 会議録検索 OpenAPI JSON: `/api/gijiroku/openapi.json`

未対応の自治体では「準備中」を表示します。

## API

既存の会議録検索 UI と同じ自治体カタログ・検索ロジックを使う読み取り専用 API を用意しています。

- 自治体一覧: `GET /api/gijiroku/municipalities.php`
- 自治体別検索: `GET /api/gijiroku/search.php?slug={slug}&q={query}`
- 開催日ごとの会議一覧: `GET /api/gijiroku/documents.php?slug={slug}&held_on={YYYY-MM-DD}`
- 会議録全文: `GET /api/gijiroku/document.php?slug={slug}&id={id}`
- OpenAPI 仕様: `GET /api/gijiroku/openapi.json`

最初に自治体一覧を取り、その `slug` を検索 API に渡してください。
検索語では `AND` / `OR` / `NOT` / `NEAR/5` と、`"同和地区"` のような引用符つきフレーズ一致が使えます。

```bash
curl "http://localhost/api/gijiroku/municipalities.php"
```

```bash
curl "http://localhost/api/gijiroku/search.php?slug=14130-kawasaki-shi&q=補正予算&per_page=5"
```

```bash
curl "http://localhost/api/gijiroku/documents.php?slug=14130-kawasaki-shi&held_on=2025-06-18"
```

```bash
curl "http://localhost/api/gijiroku/document.php?slug=14130-kawasaki-shi&id=123&q=補正予算"
```

検索結果の `excerpt` は `[[[` と `]]]` でハイライト範囲を示した抜粋です。平文が欲しい場合は `excerpt_plain` を使えます。
特定日の会議を API で取りたい場合は、まず `documents.php` でその日の一覧を取り、返ってきた `id` を `document.php` に渡してください。

## 設定

通常は `data/config.json` に自治体ごとの設定は要りません。  
全国マスタに載っている自治体は、保存先パスやタイトルを既定値から導出します。

主な項目:

- `assembly_name`
- `skip_detection`
- `data_dir`
- `downloads_dir`
- `index_json_path`
- `db_path`
  - 既定値と違う保存先にしたい場合だけ指定

## データ配置

既定の構成:

本番データ (`data/`):

- 検索DB: `data/gijiroku/{slug}/minutes.sqlite`

ローカル作業データ (`work/`):

- ダウンロード済み会議録: `work/gijiroku/{slug}/downloads`
  - 例: `downloads/{year_label}/{meeting_name}/{title}.txt.gz`
- 収集結果一覧: `work/gijiroku/{slug}/meetings_index.json`
- デバッグ用ページ: `work/gijiroku/{slug}/pages`
- 収集結果CSV: `work/gijiroku/{slug}/run_result_*.csv`
- レジューム状態: `work/gijiroku/{slug}/scrape_state.json`

実際の参照先は `slug` からこの既定配置を組み立てます。`config.json` で個別パスを書きたいのは、既定配置を外したい場合だけです。

## 関連スクリプト

現状のスクレイパ:

- `tools/gijiroku/scrape_gijiroku_com.py`
- `tools/gijiroku/scrape_kaigiroku_net.py`
- `tools/gijiroku/scrape_dbsr.py`
- `tools/gijiroku/scrape_kensakusystem.py`

`data/municipalities/assembly_minutes_system_urls.tsv` の `system_type` に合わせて命名しています。現時点で実装済みなのは `gijiroku.com` / `voices` 系、`kaigiroku.net` 系、`dbsr` / `db-search` / `kaigiroku-indexphp` 系、`kensakusystem` 系です。

```bash
python tools/gijiroku/scrape_gijiroku_com.py --slug 14130-kawasaki-shi --ack-robots
```

```bash
python tools/gijiroku/scrape_kaigiroku_net.py --slug hakodate-shi --ack-robots
```

```bash
python tools/gijiroku/scrape_dbsr.py --slug hino-shi --ack-robots
```

```bash
python tools/gijiroku/scrape_kensakusystem.py --slug hirosaki-shi --ack-robots
```

`gijiroku.com` / `voices` を使う自治体だけを既定設定（6 並列・同一ホスト起動間隔 2 秒）で全国一括実行したい場合:

```bash
python tools/gijiroku/scrape_all_minutes.py --ack-robots --systems gijiroku.com --parallel 6 --per-host-start-interval 2
```

まず 5 自治体だけに絞って、親プロセス側に進捗を表示する場合:

```bash
python tools/gijiroku/scrape_all_minutes.py --ack-robots --systems gijiroku.com --max-targets 5 --parallel 5 --per-host-start-interval 2
```

実装済みの `gijiroku.com` / `voices` / `kaigiroku.net` / `dbsr` / `db-search` / `kaigiroku-indexphp` / `kensakusystem` をまとめて回す場合:

```bash
python tools/gijiroku/scrape_all_minutes.py --ack-robots --parallel 8 --per-host-parallel 1 --per-host-start-interval 2
```

この一括実行では、自治体ごとのスクレイプ完了直後に `minutes.sqlite` も更新されるため、全国バッチの途中でも完了済み自治体から順に Web 検索可能になります。  
自動更新を止めたい場合は `--no-build-index` を付けます。

既存データの整理:

```bash
php tools/gijiroku/organize_minutes_data.php --slug 14130-kawasaki-shi
```

全文検索 DB の手動生成:

```bash
python tools/gijiroku/build_minutes_index.py --slug 14130-kawasaki-shi
```

トップページで会議録が `要反映` と表示された場合は、ダウンロード件数は揃っている一方で `minutes.sqlite` など公開用成果物の検出が追いついていません。常駐の会議録サービスは各サイクルの先頭で `build_missing_minutes_indexes.py` を優先実行し、`要反映`、完了間近、未着手、反映済みの順で不足分補完を進めます。進行中の補完はトップページの `Scraping Now` に `会議録 反映` として出ます。すぐに直らない場合は、会議録サービス停止や build 失敗を疑ってください。

`--slug` を付けると、`data/municipalities` の全国マスタから対象自治体の出力先を解決します。

補足:

- FTS5 には本文そのものではなく、SudachiPy で作った terms カラムを登録します。
- 検索時も PHP から同じ SudachiPy ヘルパを呼ぶため、Web 側へ反映するには PHP image の再 build が必要です。

補足:

- 本文・調査用HTML・デバッグJSONは gzip を優先して保存し、同じ論理ファイルの平文重複を避けます。
- スクレイパは既存ダウンロードと `scrape_state.json` を見て中断箇所から再開します。完全に最初からやり直したい場合は `--no-resume` を使います。
- 公開 URL の `slug` は `自治体コード-ローマ字名称` に統一します。既存 slug や自治体コードだけの指定も alias として受け付け、正規 URL へリダイレクトします。

## メモ

- スクレイパは system_type ごとに分けています。自治体ごとの構造差分が大きい場合は、今後も system_type 単位で追加します。
- `gijiroku.com` 系は `voices` も同じスクレイパで扱います。
- `dbsr` 系は `db-search` と `kaigiroku-indexphp` も同じスクレイパで扱います。
- `dbsr` 系は年別一覧から `Template=list` をたどり、検索結果一覧のページ送りを巡回して日付単位の本文テキストを保存します。
- `kensakusystem` 系は `See.exe` のツリーと `PRINT_ALL` の全文表示を使って、1文書ずつ本文を保存します。
- 一方で、Web 画面と SQLite インデクサは自治体単位の切り替えを前提に整理しています。
- `run_result_*.csv` や空の `pages/` は調査用の一時成果物なので、確認後に整理して構いません。
- 画面上の時刻表示は `Asia/Tokyo` に揃えます。
