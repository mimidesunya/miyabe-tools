# 例規集ツール

例規集はスクレイパが保存した HTML / Markdown / JSON から OpenSearch index を作ります。公開検索で SQLite FTS は使いません。

## 画面と API

- 例規集ビューア: `/reiki/?slug={slug}`
- 統合検索 UI: `/search/?doc_type=reiki`
- 統合検索 API: `GET /api/search?q={query}&doc_type=reiki`

自治体別に絞る場合は `slug` を渡します。

```bash
curl "http://localhost/api/search?q=個人情報&doc_type=reiki&slug=14130-kawasaki-shi"
```

## データ配置

- 整形 HTML: `data/reiki/{slug}/html`
- AI 評価 JSON: `data/reiki/{slug}/json`
- 画像: `data/reiki/{slug}/images`
- 元 HTML: `work/reiki/{slug}/source`
- Markdown: `work/reiki/{slug}/markdown`
- クロールマニフェスト: `work/reiki/{slug}/source_manifest.json.gz`
- レジューム状態: `work/reiki/{slug}/scrape_state.json`

## スクレイピング

```bash
python tools/reiki/scrape_all_reiki.py --parallel 8 --per-host-parallel 1 --per-host-start-interval 2 --check-updates
```

単独実行例:

```bash
php tools/reiki/download_taikei.php --slug kyoto-fu --check-updates
python tools/reiki/download_d1_law.py --slug 14130-kawasaki-shi --check-updates
```

## OpenSearch 反映

```bash
python tools/search/build_opensearch_index.py --mode update --doc-type reiki --slug 14130-kawasaki-shi
```

通常のスクレイピング後は、その自治体 slug だけを current alias 上で delete+bulk して差し替えます。alias がまだない初回は、その slug 分の index を作って公開し、以後の自治体が徐々に追加されます。

全量再構築が必要な場合だけ、versioned index を作成して投入完了後に alias を切り替えます。

```bash
python tools/search/build_opensearch_index.py --mode rebuild --doc-type reiki
```

OpenSearch がない環境では検索 API は 503 を返し、SQLite へフォールバックしません。

## メモ

- `ordinances.sqlite` は不要です。削除されていても、保存済み HTML / Markdown / JSON から再インデックスできます。
- 旧横断検索ページと SQLite 検索 API は廃止しました。
- gzip 済みの既存成果物とレジューム状態はそのまま利用できます。
