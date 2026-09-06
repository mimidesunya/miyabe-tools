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
php tools/reiki/scrapers/taikei.php --slug kyoto-fu --check-updates
python tools/reiki/scrapers/d1_law.py --slug 14130-kawasaki-shi --check-updates
```

### legal-square の取り切り方

legal-square は 1 回の検索が上限（多くは 100 件）で打ち切られる。
上限に張り付いた区間は、次の順で割ってから取り込む。

1. 制定年月日の期間を二分する
2. 単月まで来たら、その月の中を日で割る
3. 単日でも上限なら、種別ツリーの第 2 階層で割る
4. それでも上限なら、**件名のキーワード**で「含む」「含まない」に割る

4 は詳細検索の件名欄（`searchWord-A`〜`E`、AND 結合）を使う。2 つ合わせれば
元の区間と過不足なく一致するので、取りこぼしを増やさずに上限を越えられる。
欄は 5 つなので 1 区間あたり最大 32 分割になる。分割語は
`TITLE_SPLIT_WORDS` から選ぶが、**実際に二つに分かれた語**を採る。
分かれない語で欄を使うと、空振りのまま 5 枠を使い切る。

宮古市の平成17年6月6日（合併の日）の条例は、期間でも種別でも割れず
100 件で頭打ちだった。件名で割ると 174 件まで取り切れる。

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
