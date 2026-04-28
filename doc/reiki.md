# 例規集ツール

例規集の本文閲覧、AI 評価結果の一覧表示、フィードバック収集を行うツールです。  
横断全文検索と自治体別検索の日本語分かち書きには `SudachiPy` を使います。
画面は自治体切り替えに対応し、どの自治体のデータを使うかは `data/municipalities` の全国マスタから決まります。

## 画面

- 例規集ビューア: `/reiki/?slug={slug}`
- 例規集横断全文検索: `/reiki/cross.php`

未対応の自治体を開いた場合は、川崎市データを誤って流用せず「準備中」と表示します。

## 設定

通常は `data/config.json` に自治体ごとの設定は要りません。  
全国マスタに載っている自治体は、保存先パスやタイトルを既定値から導出します。

主な項目:

- `skip_detection`
- `source_dir` / `clean_html_dir` / `classification_dir` / `image_dir` / `markdown_dir` / `db_path`
  - 既定値と違う保存先にしたい場合だけ指定

例規一覧のかな順ソートは `data/municipalities/municipality_master.tsv` の `name_kana` から自動調整します。

## データ配置

既定の構成:

本番データ (`data/`):

- 整形HTML: `data/reiki/{slug}/html`
- AI評価JSON: `data/reiki/{slug}/json` (`.json.gz`)
- 画像: `data/reiki/{slug}/images`
- 一覧SQLite: `data/reiki/{slug}/ordinances.sqlite`

ローカル作業データ (`work/`):

- 元HTML: `work/reiki/{slug}/source` (`*_j.html.gz`)
- Markdown: `work/reiki/{slug}/markdown` (`*.md.gz`)
- クロールマニフェスト: `work/reiki/{slug}/source_manifest.json.gz`
- 体系ページ一覧: `work/reiki/{slug}/taxonomy_pages.json.gz` (`taikei` 系)

## 関連スクリプト

現状のスクレイパ:

- `tools/reiki/download_d1_law.py`
- `tools/reiki/parse_d1_law.py`
- `tools/reiki/download_taikei.php`
- `tools/reiki/classify_reiki.py`

スクレイパ名は `data/municipalities/reiki_system_urls.tsv` の `system_type` に合わせています。`d1-law`、`taikei`、`g-reiki` は `--slug` で対象自治体を切り替えます。

京都府のような `taikei` 系スクレイパは `https://www.pref.kyoto.jp/reiki/` の体系ページを巡回し、元HTML・Markdown・マニフェストを `work/reiki/kyoto-fu` に、整形HTML・SQLite を `data/reiki/kyoto-fu` に出力します。`config.json` で個別パスを書かなくても、この既定配置を使います。

```bash
php tools/reiki/download_taikei.php --slug kyoto-fu
```

`g-reiki` 系は同じ taikei-like スクレイパを `--system-type=g-reiki` 付きで使います。香美市の取得例:

```bash
php tools/reiki/download_taikei.php --system-type=g-reiki --slug 39212-kami-shi
```

少件数の試運転:

```bash
php tools/reiki/download_taikei.php --slug kyoto-fu --limit=10 --force
```

更新確認付きで再実行する場合:

```bash
php tools/reiki/download_taikei.php --slug kyoto-fu --check-updates
```

`d1-law` 系の取得例:

```bash
python tools/reiki/download_d1_law.py --slug 14130-kawasaki-shi
```

既存の条例も取り直して更新を反映したい場合:

```bash
python tools/reiki/download_d1_law.py --slug 14130-kawasaki-shi --check-updates
```

実装済みの `d1-law` / `taikei` / `g-reiki` をまとめて回す場合:

```bash
python tools/reiki/scrape_all_reiki.py --parallel 8 --per-host-parallel 1 --per-host-start-interval 2 --check-updates
```

`scrape_all_reiki.py` は自治体ごとのスクレイプ完了後に `ordinances.sqlite` を自動で再構築します。  
整形 HTML を基準に DB を作り直すので、既にダウンロード済みなのに DB に入っていないページも次のビルドで拾えます。

全文検索用の FTS5 には、整形 HTML / Markdown から SudachiPy で作った terms カラムを登録します。

手動で自治体別 SQLite を再構築したい場合:

```bash
python tools/reiki/build_ordinance_index.py --slug 14130-kawasaki-shi
```

トップページで例規集が `要反映` と表示された場合は、スクレイプ件数は揃っている一方で `ordinances.sqlite` や公開用 HTML の検出が追いついていません。上の build を自治体別に再実行するか、一括スクレイプを再実行して反映を確認します。

補足:

- スクレイパは gzip された元HTML/Markdown/JSON を優先し、同じ論理ファイルの平文重複は新規実行時に整理します。
- ダウンロード途中で止まっても、既存ソースと生成済み出力を見て不足分だけ再開します。
- `init_ordinance_db.py` は互換ラッパーとして残してあり、内部では `build_ordinance_index.py` を呼びます。
- 公開 URL の `slug` は `自治体コード-ローマ字名称` に統一します。既存 slug や自治体コードだけの指定も alias として受け付け、正規 URL へリダイレクトします。

## 画面側の挙動

- `slug` ごとに本文ディレクトリと SQLite を切り替えます。
- フィードバックと閲覧数は `slug:filename` 単位で集計します。
- 本文内画像の URL も自治体ごとの `image_dir` から解決します。
- コメント日時などの画面表示は `Asia/Tokyo` に揃えます。
