# 会議録ツール

議会会議録のスクレイピング結果を SQLite FTS5 に登録し、Web 上で検索するためのツールです。  
ビューアは自治体切り替えに対応し、利用可能な自治体だけ検索画面を有効化します。

## 画面

- 会議録検索: `/gijiroku/?slug={slug}`

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

`work/municipalities/assembly_minutes_system_urls.tsv` の `system_type` に合わせて命名しています。現時点で実装済みなのは `gijiroku.com` 系、`kaigiroku.net` 系、`dbsr` 系です。

```bash
python tools/gijiroku/scrape_gijiroku_com.py --slug kawasaki-shi --ack-robots
```

```bash
python tools/gijiroku/scrape_kaigiroku_net.py --slug 01202-hakodate --ack-robots
```

```bash
python tools/gijiroku/scrape_dbsr.py --slug hino-shi --ack-robots
```

`gijiroku.com` を使う自治体を全国一括で回したい場合:

```bash
python tools/gijiroku/scrape_all_gijiroku_com.py --ack-robots
```

まず 5 自治体を並列で回し、親プロセス側に進捗を表示する場合:

```bash
python tools/gijiroku/scrape_all_gijiroku_com.py --ack-robots --max-targets 5 --parallel 5
```

実装済みの `gijiroku.com` / `kaigiroku.net` / `dbsr` をまとめて回す場合:

```bash
python tools/gijiroku/scrape_all_minutes.py --ack-robots --parallel 4 --per-host-parallel 1
```

既存データの整理:

```bash
php tools/gijiroku/organize_minutes_data.php --slug kawasaki-shi
```

全文検索 DB の生成:

```bash
python tools/gijiroku/build_minutes_index.py --slug kawasaki-shi
```

`--slug` を付けると、`data/config.json` から対象自治体の出力先を解決します。

補足:

- 本文・調査用HTML・デバッグJSONは gzip を優先して保存し、同じ論理ファイルの平文重複を避けます。
- スクレイパは既存ダウンロードと `scrape_state.json` を見て中断箇所から再開します。完全に最初からやり直したい場合は `--no-resume` を使います。
- 新規追加の `slug` は `自治体コード-ローマ字名称` を推奨します。

## メモ

- スクレイパは system_type ごとに分けています。自治体ごとの構造差分が大きい場合は、今後も system_type 単位で追加します。
- `dbsr` 系は年別一覧から `Template=list` をたどり、検索結果一覧のページ送りを巡回して日付単位の本文テキストを保存します。
- 一方で、Web 画面と SQLite インデクサは自治体単位の切り替えを前提に整理しています。
- `run_result_*.csv` や空の `pages/` は調査用の一時成果物なので、確認後に整理して構いません。
