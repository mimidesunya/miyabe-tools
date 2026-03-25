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
- `source_dir`
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

本番データ (`data/`):

- 整形HTML: `data/reiki/{slug}/html`
- AI評価JSON: `data/reiki/{slug}/json`
- 画像: `data/reiki/{slug}/images`
- 一覧SQLite: `data/reiki/{slug}/ordinances.sqlite`

ローカル作業データ (`work/`):

- 元HTML: `work/reiki/{slug}/source`
- Markdown: `work/reiki/{slug}/markdown`
- クロールマニフェスト: `work/reiki/{slug}/source_manifest.json`

## 関連スクリプト

現状のスクレイパ:

- `tools/reiki/download_d1_law.py`
- `tools/reiki/parse_d1_law.py`
- `tools/reiki/download_taikei.php`
- `tools/reiki/classify_reiki.py`

スクレイパ名は `work/municipalities/reiki_system_urls.tsv` の `system_type` に合わせています。`d1-law` と `taikei` は `--slug` で対象自治体を切り替えます。

京都府のような `taikei` 系スクレイパは `https://www.pref.kyoto.jp/reiki/` の体系ページを巡回し、元HTML・Markdown・マニフェストを `work/reiki/kyoto-fu` に、整形HTML・SQLite を `data/reiki/kyoto-fu` に出力します。

```bash
php tools/reiki/download_taikei.php --slug kyoto-fu
```

少件数の試運転:

```bash
php tools/reiki/download_taikei.php --slug kyoto-fu --limit=10 --force
```

`d1-law` 系の取得例:

```bash
python tools/reiki/download_d1_law.py --slug kawasaki-shi
```

自治体別 SQLite の生成は共通化しています。

```bash
python tools/reiki/init_ordinance_db.py --slug kawasaki-shi
```

## 画面側の挙動

- `slug` ごとに本文ディレクトリと SQLite を切り替えます。
- フィードバックと閲覧数は `slug:filename` 単位で集計します。
- 本文内画像の URL も自治体ごとの `image_dir` から解決します。
