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

- ダウンロード済み会議録: `data/gijiroku/{municipality_dir}/downloads`
- 収集結果一覧: `data/gijiroku/{municipality_dir}/meetings_index.json`
- 検索DB: `data/gijiroku/{municipality_dir}/minutes.sqlite`

`municipality_dir` は `slug` と同じでも、`kawasaki_council` のような別名でも構いません。実際の参照先は設定で決まります。

## 関連スクリプト

Kawasaki 向けのスクレイパ:

```bash
python tools/gijiroku/scrape_kawasaki_minutes.py --ack-robots
```

全文検索 DB の生成:

```bash
python tools/gijiroku/build_minutes_index.py --slug kawasaki
```

`--slug` を付けると、`data/config.json` から対象自治体の出力先を解決します。

## メモ

- スクレイパ自体は自治体ごとのサイト構造差分が大きいため、現時点では川崎市向けスクリプトを維持しています。
- 一方で、Web 画面と SQLite インデクサは自治体単位の切り替えを前提に整理しています。
