# 例規集ツール

例規集の本文閲覧、AI 評価結果の一覧表示、フィードバック収集を行うツールです。  
画面は自治体切り替えに対応し、どの自治体のデータを使うかは `data/config.json` の定義で決まります。

## 画面

- 例規集ビューア: `/reiki/?slug={slug}`

未対応の自治体を開いた場合は、川崎市データを誤って流用せず「準備中」と表示します。

## 設定

`data/config.json` の `MUNICIPALITIES.{slug}.reiki` を使います。

主な項目:

- `enabled`
- `title`
- `data_dir`
- `clean_html_dir`
- `classification_dir`
- `image_dir`
- `markdown_dir`
- `db_path`
- `legacy_db_path`
- `sortable_prefixes`

川崎市の既存データ配置にも対応するため、`legacy_db_path` を設定すると旧レイアウトの SQLite をそのまま参照できます。

## データ配置

代表的な構成:

- 元HTML: `data/reiki/{slug}` または設定した `data_dir`
- 整形HTML: `data/reiki/{slug}_html`
- AI評価JSON: `data/reiki/{slug}_json`
- 画像: `data/reiki/{slug}_images`
- Markdown: `data/reiki/{slug}_md`
- 一覧SQLite: `data/reiki/{slug}/ordinances.sqlite`

## 関連スクリプト

現状のスクレイパは川崎市向けのままです。

- `tools/reiki/download_kawasaki.py`
- `tools/reiki/parse_kawasaki.py`
- `tools/reiki/classify_reiki.py`

自治体別 SQLite の生成は共通化しています。

```bash
python tools/reiki/init_ordinance_db.py --slug kawasaki
```

## 画面側の挙動

- `slug` ごとに本文ディレクトリと SQLite を切り替えます。
- フィードバックと閲覧数は `slug:filename` 単位で集計します。
- 本文内画像の URL も自治体ごとの `image_dir` から解決します。
