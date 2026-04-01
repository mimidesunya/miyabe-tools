# 会議録ツール

議会会議録のスクレイピング結果を SQLite FTS5 に登録し、Web 上で検索するためのツールです。  
日本語の分かち書きと語形正規化には `SudachiPy` を使います。
ビューアは自治体切り替えに対応し、利用可能な自治体だけ検索画面を有効化します。

## 画面

- 会議録検索: `/gijiroku/?slug={slug}`
- 会議録横断検索: `/gijiroku/cross.php`

未対応の自治体では「準備中」を表示します。

## 設定

`data/config.json` の `MUNICIPALITIES.{slug}.gijiroku` を使います。

主な項目:

- `enabled`
- `title`
- `assembly_name`
- `data_dir`
- `downloads_dir`
- `index_json_path`
- `db_path`

## データ配置

代表的な構成:

本番データ (`data/`):

- 検索DB: `data/gijiroku/{slug}/minutes.sqlite`

ローカル作業データ (`work/`):

- ダウンロード済み会議録: `work/gijiroku/{slug}/downloads`
  - 例: `downloads/{year_label}/{meeting_name}/{title}.txt.gz`
- 収集結果一覧: `work/gijiroku/{slug}/meetings_index.json`
- デバッグ用ページ: `work/gijiroku/{slug}/pages`
- 収集結果CSV: `work/gijiroku/{slug}/run_result_*.csv`
- レジューム状態: `work/gijiroku/{slug}/scrape_state.json`

実際の参照先は設定で決まります。

## 関連スクリプト

現状のスクレイパ:

- `tools/gijiroku/scrape_gijiroku_com.py`
- `tools/gijiroku/scrape_kaigiroku_net.py`
- `tools/gijiroku/scrape_dbsr.py`
- `tools/gijiroku/scrape_kensakusystem.py`

`work/municipalities/assembly_minutes_system_urls.tsv` の `system_type` に合わせて命名しています。現時点で実装済みなのは `gijiroku.com` / `voices` 系、`kaigiroku.net` 系、`dbsr` / `db-search` / `kaigiroku-indexphp` 系、`kensakusystem` 系です。

```bash
python tools/gijiroku/scrape_gijiroku_com.py --slug kawasaki-shi --ack-robots
```

```bash
python tools/gijiroku/scrape_kaigiroku_net.py --slug 01202-hakodate --ack-robots
```

```bash
python tools/gijiroku/scrape_dbsr.py --slug hino-shi --ack-robots
```

```bash
python tools/gijiroku/scrape_kensakusystem.py --slug 02202-hirosaki --ack-robots
```

`gijiroku.com` / `voices` を使う自治体を既定設定（6 並列・自治体起動間隔 2 秒）で全国一括実行したい場合:

```bash
python tools/gijiroku/scrape_all_gijiroku_com.py --ack-robots --parallel 6 --delay-between-targets 2
```

まず 5 自治体だけに絞って、親プロセス側に進捗を表示する場合:

```bash
python tools/gijiroku/scrape_all_gijiroku_com.py --ack-robots --max-targets 5 --parallel 5 --delay-between-targets 2
```

実装済みの `gijiroku.com` / `voices` / `kaigiroku.net` / `dbsr` / `db-search` / `kaigiroku-indexphp` / `kensakusystem` をまとめて回す場合:

```bash
python tools/gijiroku/scrape_all_minutes.py --ack-robots --parallel 6 --per-host-parallel 1 --per-host-start-interval 2
```

この一括実行では、自治体ごとのスクレイプ完了直後に `minutes.sqlite` も更新されるため、全国バッチの途中でも完了済み自治体から順に Web 検索可能になります。  
自動更新を止めたい場合は `--no-build-index` を付けます。

既存データの整理:

```bash
php tools/gijiroku/organize_minutes_data.php --slug kawasaki-shi
```

全文検索 DB の手動生成:

```bash
python tools/gijiroku/build_minutes_index.py --slug kawasaki-shi
```

`--slug` を付けると、`data/config.json` から対象自治体の出力先を解決します。

補足:

- FTS5 には本文そのものではなく、SudachiPy で作った terms カラムを登録します。
- 検索時も PHP から同じ SudachiPy ヘルパを呼ぶため、Web 側へ反映するには PHP image の再 build が必要です。

補足:

- 本文・調査用HTML・デバッグJSONは gzip を優先して保存し、同じ論理ファイルの平文重複を避けます。
- スクレイパは既存ダウンロードと `scrape_state.json` を見て中断箇所から再開します。完全に最初からやり直したい場合は `--no-resume` を使います。
- 新規追加の `slug` は `自治体コード-ローマ字名称` を推奨します。

## メモ

- スクレイパは system_type ごとに分けています。自治体ごとの構造差分が大きい場合は、今後も system_type 単位で追加します。
- `gijiroku.com` 系は `voices` も同じスクレイパで扱います。
- `dbsr` 系は `db-search` と `kaigiroku-indexphp` も同じスクレイパで扱います。
- `dbsr` 系は年別一覧から `Template=list` をたどり、検索結果一覧のページ送りを巡回して日付単位の本文テキストを保存します。
- `kensakusystem` 系は `See.exe` のツリーと `PRINT_ALL` の全文表示を使って、1文書ずつ本文を保存します。
- 一方で、Web 画面と SQLite インデクサは自治体単位の切り替えを前提に整理しています。
- `run_result_*.csv` や空の `pages/` は調査用の一時成果物なので、確認後に整理して構いません。
