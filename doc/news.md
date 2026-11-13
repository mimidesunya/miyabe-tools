# 自治体の新着情報

自治体のホームページの新着情報（お知らせ・報道発表など）を、フィード（RSS 1.0・RSS 2.0・Atom）から集めてためる。調べた結果と全体の案は [news-feed-survey.md](news-feed-survey.md)。

いまできているのは **1 段目の取得と保存**まで。リンク先の本文、検索の索引、MCP の道具、画面はまだ無い。

## 台帳

`data/municipalities/news_feed_urls.tsv`。1 行 1 フィード。1 団体に新着・分野ごとなど複数のフィードがあれば全部載せる。

| 列 | 中身 |
|---|---|
| `jis_code` | 自治体コード |
| `url` | フィードの URL |
| `found_by` | 見つけた場所。`head`（トップページの head）・`anchor`（本文のリンク）・`rss_page`（RSS の説明ページ）・`probe`（よくある場所を試した） |
| `feed_format` | `rss1` / `rss2` / `atom` |
| `crawl_status` | `enabled` だけを読む。`excluded` は読まない |
| `exclusion_reason` | 除外の理由。`stale` は 1 年以上更新が無いもの |
| `exclusion_detail` | 理由の詳しいこと |

2026-11-13 の全国調査から作った。2,604 本のうち `enabled` が 2,556 本（1,392 団体）、`stale` が 48 本。

作り直すときは、探し直してから台帳にする。人が理由を書いて除外した行（理由が `stale` 以外）はその判断を残し、今回見つからなかった前の行も残す。

```bash
python tools/news/discover_news_feeds.py --discover          # work/news/discovery.jsonl に追記。止めても続きから
python tools/news/discover_news_feeds.py --write-registry    # 台帳を書く
```

探すのは 1 万 4 千リクエストほどで、16 並列で 30 分ほど。同じ団体へは 1 秒空ける。

## 取得

`tools/news/fetch_news_feeds.py`。台帳の `enabled` のフィードを全部読み、新しい項目を足す。

- 同じホストへは同時に 1 本、1 秒以上空ける。ホストをまたいで 16 並列。
- ETag・Last-Modified で条件付きにする。2 回目からは 57% が 304（変わっていない）で終わる（2026-11-13 に手元で測った）。
- 続けて失敗するフィードは間隔を延ばす。8 回（3 時間おきで 1 日）続いたら 1 日おき、40 回（5 日）続いたら 7 日おき。成功すれば戻る。
- フィードでない応答（サイトの作り直しで HTML の画面が返るなど）も失敗に数える。

```bash
python tools/news/fetch_news_feeds.py --code 01100                 # 1 団体だけ
python tools/news/fetch_news_feeds.py --limit 20 --data-root /tmp/news --work-root /tmp/news-work
python tools/news/fetch_news_feeds.py --dry-run                    # 読むだけで保存しない
```

`--force` は失敗の間隔と条件付き取得を無視して全部読む。

手元で全国を読んだ結果（2026-11-13）:

| | 1 回目 | 2 回目（すぐ後） |
|---|---|---|
| かかった時間 | 113 秒 | 106 秒 |
| 中身を取った | 2,554 | 1,102 |
| 304（変わっていない） | 0 | 1,451 |
| 失敗 | 2 | 3 |
| 新しい項目 | 106,449 | 4 |
| 更新（版を上げた） | 553 | 0 |

失敗は直方市の 2 本（中間証明書を送ってこないので、証明書の検証に落ちる）と、接続を切られた 1 本。1 回目の 10 万 6 千件は、各フィードに出ていたこれまでの分。

## 保存

```
data/news/<slug>/
    items/<年>.jsonl   項目。初めて見た年ごと、1 行 1 項目。追記だけで消さない
    seen.json          見た項目の印。id → [最後の版, 題名と日付の印 ...]
work/news/
    feed_state.json    フィードごとの ETag・Last-Modified・最後に読めた日時・続けた失敗の回数・最後の失敗
    last_run.json      直近の 1 回の結果（失敗の例を 30 件まで）
    runs.jsonl         1 回ごとの結果
```

**項目は消さない。** 自治体のサイトは古いお知らせを消し、フィードには中央値で 36 日分しか出ない。ためたものが記録になる。

項目の欄:

| 欄 | 中身 |
|---|---|
| `id` | リンク先の URL から作る印（SHA-1 の頭 16 桁）。リンクが無ければフィードの URL・guid・日付から |
| `jis_code`・`slug` | 自治体 |
| `url` | リンク先。相対の URL は直してある |
| `title` | 題名 |
| `published_at`・`date` | 日付（日本時間）。時差の書いていないものは日本時間と見なす。読めなければ欄が無い |
| `date_raw` | フィードに書いてあったままの日付 |
| `summary` | 概要。HTML の印を外して 1,000 字まで |
| `categories` | 分類（`category`・`dc:subject`・`category01〜03`） |
| `event_start`・`event_end` | 催しの開始・終了日（一部の CMS だけ） |
| `feed_url` | 見つけたフィード |
| `first_seen_at` | 初めて見た日時 |
| `revision` | 版。同じ `id` で題名か日付が変わると 1 つ上げてもう 1 行足す |
| `fingerprint` | 題名と日付の印 |

空の欄は書かない。

**読む側は `id` ごとに最後の行を使う。** 更新のたびに新着へ出し直すページ（人口の統計など）は、同じ URL で日付だけ変わって出てくる。そのたびに版を上げた行が足される。

`seen.json` は前に見た題名と日付の印を 8 つまで覚え、そのどれとも違うときだけ版を上げる。最後の印とだけ比べると、次の 2 つが毎回「更新」になり、行が増え続ける（手元で 553 件あった）。

- 同じ URL に別々のお知らせを付けるフィード（苫小牧市）
- 新着と分野ごとのフィードで、同じ項目の日付が違うもの

`seen.json` が壊れていたら脇へどけ、項目の記録から作り直す。

フィードの読み方（題名の整え方・日付の読み方）を変えると、そのときフィードに出ている項目の印が変わり、1 回だけ版を上げた行が足される。

## 本番

スクレイパの Celery に組み込んである（[remote-scraping.md](remote-scraping.md)）。

- beat が 3 時間おきに `run_news_fetch` を `news` キューへ積む（`CELERY_NEWS_FETCH_SECONDS`、既定 10,800）。
- 専用の worker `scraper-news` が受ける（CPU 0.5、並列 1）。会議録・例規の取得 worker は 1 自治体を何日も掴むことがあり、索引 worker の待ち行列は 1,000 自治体単位になる。その後ろに並べない。
- 置き場所は共有領域（`shared_data_dir`）の `news` と `work/news`。それぞれ worker の `/workspace/data/news` と `/workspace/work/news` に重ねる。

すぐに 1 回読ませるには:

```bash
docker compose -p miyabe-tools-scraping -f docker-compose.scraping.yml exec -T scraper-news \
  sh -lc 'cd /workspace && PYTHONPATH=/workspace python3 deploy/scraper_runtime/celery/enqueue.py news-fetch'
```

結果を見るには `work/news/last_run.json`。

## 量と負荷

- 1 日に 2,556 本 × 8 回 ≒ 2 万リクエスト。半分以上は 304 で中身を送らない。相手 1 団体あたりは 1 日 8 回 × フィードの本数。
- 保存は 1 項目 600 バイトほど。1 回目の 10 万 6 千件で 62MB。その後は 1 日 2,400 件ほど（1.4MB）で、年に 500MB ほど。

## 残り

- 直方市の 2 本（証明書の検証に落ちる）。中間証明書を送ってこない形なら、例規の取得（`tools/reiki/scrapers/static_catalog.py` の `ca_bundle_path`）と同じく足せば通る見込み。まだ確かめていない。
- 次の段: リンク先の本文、検索の索引と MCP の道具、自治体の詳細ページへの表示、フィードの無い団体の読み取り（[news-feed-survey.md](news-feed-survey.md) の「作るときの案」）。
- 実行状態（`management_task_statuses`）や収集状況のページにはまだ出していない。
