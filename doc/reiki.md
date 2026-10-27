# 例規集ツール

例規集はスクレイパが保存した HTML / Markdown から OpenSearch index を作ります。公開検索で SQLite FTS は使いません。

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
- 画像: `data/reiki/{slug}/images`
- 元 HTML: `work/reiki/{slug}/source`
- Markdown: `work/reiki/{slug}/markdown`
- クロールマニフェスト: `work/reiki/{slug}/source_manifest.json.gz`
- レジューム状態: `work/reiki/{slug}/scrape_state.json`

## AI 評価はしない

例規の AI 評価（分類・スコア・判定理由を付ける `dev/reiki/classify.py`、`data/reiki/{slug}/json`、
評価への投票 `/reiki/api.php`）は 2026-10-27 にやめて消した。画面・索引・検索 API・MCP に評価は出さない。
サイトは原文と取得元を正確に出し、評価は利用者がそれぞれの AI で原文に当たって確かめる。

## スクレイピング

```bash
python tools/reiki/scrape_all_reiki.py --parallel 8 --per-host-parallel 1 --per-host-start-interval 2 --check-updates
```

単独実行例:

```bash
php tools/reiki/scrapers/taikei.php --slug 26000-kyoto-fu --check-updates
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

### legal-square のセッション切れ

取得元は「Reiki-Base Error 処理エラーが発生しました」を返すとき、セッションを
捨てて新しい JSESSIONID を渡す。メイン画面に残った検索画面は古いセッションの
もので、検索や「次へ」の要求は `http://…/SJSrbLogin.jsf` へ転送され、https の
画面からは mixed content として止められる。結果が差し替わらないまま待つので、
白浜町・平戸市・大阪市（2026-10）は本文 1 件の処理エラーのあと取り切れなかった。

スクレイパは、本文の処理エラー画面と、非同期要求が入口へ転送されたことを見て、
ブラウザのコンテキストごと作り直して入口から入り直す（1 回の走査で最大 30 回）。
本文の途中なら同じ検索を出し直し、取り込み済みの行を読み飛ばして続きから歩く。
入り直した直後に開いても処理エラーになる本文は、取得元の側の欠けとして
`source_coverage.json` の `missing_at_source` に数え、失敗には数えない
（多すぎるときは `missing_share_exceeded` で完了にしない）。入り直した回数は
`session_renewals` に残る。

## OpenSearch 反映

```bash
python tools/search/build_opensearch_index.py --mode update --doc-type reiki --slug 14130-kawasaki-shi
```

通常のスクレイピング後は自治体 slug だけを current alias 上で差し替え、全量再構築（`--mode rebuild`）のときだけ versioned index を作って alias を切り替えます。仕組みはリポジトリ直下の [README.md](../README.md)「検索基盤」にまとめています。OpenSearch がない環境では検索 API は 503 を返し、SQLite へフォールバックしません。

## メモ

- gzip 済みの既存成果物とレジューム状態はそのまま利用できます。
