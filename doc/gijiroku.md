# 会議録ツール

会議録はスクレイパが `work/gijiroku/{slug}` に落とした成果物から OpenSearch index を作ります。公開検索で SQLite FTS は使いません。

## 画面と API

- 統合検索 UI: `/search/?doc_type=minutes`
- 旧会議録入口: `/gijiroku/?slug={slug}` は統合検索へリダイレクト
- 統合検索 API: `GET /api/search?q={query}&doc_type=minutes`

自治体別に絞る場合は `slug` を渡します。

```bash
curl "http://localhost/api/search?q=補正予算&doc_type=minutes&slug=14130-kawasaki-shi"
```

## データ配置

`data/config.json` に自治体別の検索 DB 設定は不要です。保存先は全国マスタと slug から導出します。

- ダウンロード済み会議録: `work/gijiroku/{slug}/downloads`
- 収集結果一覧: `work/gijiroku/{slug}/meetings_index.json`
- レジューム状態: `work/gijiroku/{slug}/scrape_state.json`
- 調査用ページ/CSV: `work/gijiroku/{slug}/pages`, `work/gijiroku/{slug}/run_result_*.csv`

## スクレイピング

```bash
python tools/gijiroku/scrape_all_minutes.py --ack-robots --parallel 8 --per-host-parallel 1 --per-host-start-interval 2
```

`assembly_minutes_system_urls.tsv` で `crawl_status=enabled` の自治体だけを実行します。`enabled` は運用者による明示許可であり、robots監査を行いません。`--ack-robots` はこの実行判断を確認する既存のCLIゲートとして残しますが、`excluded` や `review_required` を上書きしません。取得対象外とその根拠は次で確認できます。

`crawl_status=enabled` の行は、TSVの `url` または `system_type` が変わってもrobots監査を省略して取得対象になります。それ以外の行は、フィンガープリント不一致をリモートのCelery dispatcherが検出して再監査します。

```bash
python tools/gijiroku/scrape_all_minutes.py --list-excluded
```

スクレイパは既存ダウンロードと `scrape_state.json` を見て再開します。完全に取り直す場合だけ `--no-resume` を使います。

## OpenSearch 反映

```bash
python tools/search/build_opensearch_index.py --mode update --doc-type minutes --slug 14130-kawasaki-shi
```

通常のスクレイピング後は、その自治体 slug だけを current alias 上で delete+bulk して差し替えます。alias がまだない初回は、その slug 分の index を作って公開し、以後の自治体が徐々に追加されます。

全量再構築が必要な場合だけ、versioned index を作成して投入完了後に alias を切り替えます。

```bash
python tools/search/build_opensearch_index.py --mode rebuild --doc-type minutes
```

OpenSearch がない環境では検索 API は 503 を返し、SQLite へフォールバックしません。

## メモ

- `minutes.sqlite` は不要です。削除されていても、保存済み会議録ファイルから再インデックスできます。
- 旧 `/api/gijiroku/*`、横断検索ページ、自治体別 SQLite 検索は廃止しました。
- `enabled` の明示許可、ack-robots、アクセス間隔、ホスト単位の同時実行制御を維持します。
