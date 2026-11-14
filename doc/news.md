# 自治体の新着情報

自治体のホームページの新着情報（お知らせ・報道発表など）を、フィード（RSS 1.0・RSS 2.0・Atom）から集めてためる。調べた結果と全体の案は [news-feed-survey.md](news-feed-survey.md)。

いまできているのは **フィードの取得と保存**、**リンク先の本文**、**添付の PDF の本文**、**トップページの「各地のお知らせ」**、**一覧と 1 件の API・MCP の道具**（下の「API と MCP」）まで。本文の全文検索はまだ無い。

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
    latest/            トップページの「各地のお知らせ」の一覧（下の「トップページ」）
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

## 本文

`tools/news/fetch_news_bodies.py`。ためた項目を後ろから追いかけ、まだ本文の無い項目のリンク先を読む。本文の抜き出しは `tools/news/news_body.py`。

```
data/news/<slug>/bodies/
    <年>.jsonl    本文（読んだ年ごと、1 行 1 件）。追記だけで消さない
    state.json    項目のファイルをどこまで見たか・まだ読んでいない項目・読んだ項目の版と本文の印
```

**お知らせのページは消える。** 2026-11-14 に手元で 100 自治体 × 5 件を読んだら、出てから数日のものでも 7 件（1.4%）が 404 だった。見つけた回のうちに読み、消えていたら `kind: gone` の行を残す。

- 新しく見つけた順（時の単位。同じなら日付の新しい順）に読む。版が上がった項目は読み直し、本文が前と同じなら行を足さない。
- 1 回に読むのは 1 自治体あたり `4,000 ÷ 待っている自治体の数`（5〜200 件）。1 回目のフィードの 10 万 6 千件のような溜まった分は何回かに分け、どの自治体も少しずつ進む。待っている自治体が減るほど 1 自治体の件数が増える。
- 同じホストへは 1 秒以上空け、同時に 1 本。robots.txt は見ない（会議録・例規と同じ方針。`tools/gijiroku/crawl_policy.py`）。
- つながらない・5xx は間を空けて読み直す（3 時間・6 時間・12 時間・1 日・2 日・3 日・7 日）。2 週間ほど続けて駄目なら `kind: failed` の行を残して諦める。サイトの半日の休みや一時的な拒否で、その間のお知らせを失わないため。404・410 は `gone`。
- 読んだ分は、自治体が 1 つ終わるたびに保存する。回の途中で worker を作り直しても、終わった自治体の分は残る。変わっていない自治体の `state.json` は書き直さない。
- http でつながらないときは https で読み直し、そのホストはその回のあいだ https で読む（鶴岡市はフィードのリンクが http のままで、http を閉じている）。
- 大きさは 20MB まで、本文は 30,000 字まで。

抜き出し方（全国 300 件で試した）:

1. 見出し・メニュー・パンくず・フッター・`aside` などを外す。
2. よくある本文の枠（`article`・`main`・`#main`・`#tmp_contents`・`#contents`・`#content`・`.contents` など）を順に探し、100 字以上あればそれを使う。
3. 無ければ `body` から、リンクでない文字の 7 割以上を持つ子へ降りていく（`.mol_contents`・`#contentBody`・`#ContentPane` などに行き着く）。

100 自治体 × 5 件では、本文の中央値は 801 字、100 字未満は 8 件（1.6%。中身を JavaScript で出すサイト）。段落ごとに改行を入れる。

- PDF のリンクは PDF の本文を抜く（`kind: pdf`・`pages`）。添付と同じ子のプロセスで読み、180 秒で打ち切る。Excel・Word などは形式だけ（`kind: xlsx` など）、画像などは `kind: other` と `media_type`。
- 添付は URL とリンクの文字を控える（`attachments`、50 本まで）。お知らせの 55% に添付があり、報道発表のように中身が PDF だけのものも多い。PDF の中身は下の「添付」で読む。
- 20MB を超えるページは読まずに `kind: too_large`（読み直さない）。

行の欄: `id`・`jis_code`・`revision`（読んだ項目の版）・`url`・`final_url`（転送された先）・`fetched_at`・`status`・`kind`（`html` / `pdf` / `gone` / `failed` / `too_large` / `unreadable` / 形式）・`page_title`・`text`・`chars`・`method`（どこから抜いたか）・`attachments`・`text_hash`・`error`。

Celery の `run_news_fetch` が、フィードを読んだすぐ後に呼ぶ。結果は `work/news/bodies_last_run.json` と `bodies_runs.jsonl`。

```bash
python tools/news/fetch_news_bodies.py --code 01100
python tools/news/fetch_news_bodies.py --per-run 500 --data-root /tmp/news --work-root /tmp/news-work
```

手元で 100 自治体 × 5 件（494 リクエスト）を読んで 159 秒。本番の 1 回目（10 万 6 千件の待ち、1 回に 7 千件ほど）は 30〜40 分かかる見込み。

## 添付

`tools/news/fetch_news_attachments.py`。本文の行の `attachments` を後ろから追いかけ、まだ読んでいない **PDF** を読む。Excel・Word などの添付はまだ読まない（PDF が添付の 87%）。

```
data/news/<slug>/attachments/
    <年>.jsonl    PDF の本文（読んだ年ごと、1 行 1 本）。追記だけで消さない
    state.json    本文のファイルをどこまで見たか・まだ読んでいない PDF・読んだ PDF の印（URL の SHA-1 の頭 16 桁 → 本文の印か結果）
```

2026-11-14 に本番の本文 1 万 3 千件を見ると、52% に PDF の添付があり、1 件あたり中央値 2 本・多いものは 50 本、合わせて 4 万 3 千本だった。全国から 1 自治体 1 本ずつ 400 本を読むと、大きさは平均 1.4MB・最大 27MB、頁は中央値 2・最大 139、文字は平均 5,200 字、文字の無いもの（画像だけ）13%、抜き出しは平均 0.46 秒・最大 49 秒（図面）。

- **PDF そのものは残さない。** 平均 1.4MB で 1 日 5 千本ほど出るので、残すと年に数 TB になる。本文と `sha1`（中身の印）・`bytes` を残す。
- 頁は改頁文字（`\f`）で区切る（空の頁も 1 頁と数える）。先頭の 300 頁まで、本文は 200,000 字まで（超えたら `truncated`）。
- 1 件のお知らせから、ページに並んだ順に 20 本まで（様式や過去の号を何十本も並べるページがある）。同じ自治体で同じ URL は 1 回だけ読む。
- 本文を新しく読んだ順に読む。1 回に読むのは 1 自治体あたり `6,000 ÷ 待っている自治体の数`（3〜200 本）。
- **1 回に使うのは 60 分まで。** 過ぎたら新しく読み始めず、次の回に回す（`out_of_time`）。3 時間おきの回が次の回に重ならないため。待ちの古い自治体から読むので、時間切れでも同じ自治体ばかり残らない。
- 同じホストへは 1 秒以上空け、同時に 1 本。http でつながらなければ https で読み直す（本文と同じ）。
- 30MB を超えるもの（大きさを告げていればその場で）は読まずに `kind: too_large`。404・410 は `gone`。つながらない・5xx は、本文と同じ間隔で読み直してから `failed`。
- 読んだ分は、自治体が 1 つ終わるたびに保存する。PDF そのものは残さないので、回の途中で止められて読み直しになると、その間に消えた PDF は戻らない。
- 転送でトップページなどの HTML に行き着いたら `kind: other` と `media_type`。
- **抜き出しは子のプロセスで行い、1 本 180 秒で打ち切る**（`tools/news/pdf_text_worker.py`、`error` に理由、結果の `too_slow`）。pypdf は図面などで何十秒もかかることがあり、終わらない PDF があるとフィードの取得まで止まるため。子は 1 つで、PDF は 1 本ずつ読む（worker の CPU は 0.5）。

行の欄: `url`・`item`（初めて見つけたお知らせの `id`）・`jis_code`・`link_text`（リンクの文字。大きさが書いてあることが多い）・`fetched_at`・`status`・`final_url`・`kind`（`pdf` / `gone` / `failed` / `too_large` / 形式 / `other`）・`bytes`・`sha1`・`pages`・`pages_read`（300 頁で止めたとき）・`text`・`chars`・`text_pages`（文字のある頁の数）・`truncated`・`text_hash`・`size`・`media_type`・`error`。

お知らせとつなぐときは、本文の行の `attachments[].url` と添付の行の `url` を合わせる。

Celery の `run_news_fetch` が、本文を読んだすぐ後に呼ぶ。結果は `work/news/attachments_last_run.json` と `attachments_runs.jsonl`。

```bash
python tools/news/fetch_news_attachments.py --code 01100
python tools/news/fetch_news_attachments.py --per-run 300 --budget-minutes 10 --data-root /tmp/news --work-root /tmp/news-work
```

手元で、前に試した 100 自治体の本文から 1 自治体 3 本ずつ読むと、266 本・320MB を 62 秒。文字なし 32 本。1 行は平均 8.4KB。

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

結果を見るには `work/news/last_run.json`（フィード）・`bodies_last_run.json`（本文）・`attachments_last_run.json`（添付）。

## トップページ

トップページの「各地のお知らせ」。取得の 1 回の終わりに `tools/news/latest_news.py` がためた項目から一覧を書き、web はそれを読むだけ。

```
work/news/latest/all.json        全国。1 自治体 1 件まで、20 件
work/news/latest/<01〜47>.json   都道府県。1 自治体 3 件まで、20 件
```

- 直近 7 日（きょうを含む）の項目から選ぶ。日付の無い項目（全体の 2.4%）と、見つけた日より後の日付の項目（催しの日を日付にするフィード）は出さない。
- 日付の新しい順。同じ日付なら見つけた時刻（時の単位）の新しい順、同じ回に見つけたものは項目の id（URL の SHA-1）の順。取得は自治体コードの順に回るので、見つけた時刻の順だと毎回同じ地方が上に来る。id の順なら地方が混ざり、次の取得までは並びが変わらない。
- 同じ自治体の同じ題名・同じ日付は 1 つにする（新着と分野のフィードで URL が違うことがある）。
- 項目の記録は後ろから読み、7 日より前に書いた行に当たったらやめる。手元の全国の 1 回目（10 万 6 千件がみな 7 日以内）で 2 秒。
- 書けなかったときも取得の結果は残し、`last_run.json` の `latest_error` に書く。書けたときは候補の数（`latest_candidates`）と、お知らせのあった都道府県の数（`latest_prefectures`）。

web 側:

- `lib/news.php` が一覧を読んで並べる。リンクは http(s) だけ、題名はエスケープする。全国の一覧だけ都道府県名を付ける。
- `app/index.php` が全国の一覧を最初から出す。一覧がまだ無ければ（手元など）この欄ごと出さない。
- 都道府県を選ぶと `app/assets/js/home-news.js` が `/api/news-latest.php?pref=13` から並べた HTML をもらって差し替える。選んだ都道府県はそのブラウザにだけ覚える。ページの中の部品なので、公開の API（OpenAPI・MCP）には載せない。
- スマホの幅では 8 件で止め、残りはボタンで開く。
- 本番の web は共有領域の `work` を `/var/www/work` に重ねているので、置き場所を足さずに読める。

一覧だけ書き直すには:

```bash
python tools/news/latest_news.py --data-root data/news --work-root work/news
```

テストは `tools/news/test_latest_news.py` と `php tests/news_latest_test.php`。

## API と MCP

ためたお知らせを、自治体・都道府県・期間で一覧にし、1 件の本文と添付の PDF の文字を返す。

| | REST | MCP |
|---|---|---|
| 一覧 | `/api/news.php` | `list_municipal_news` |
| 1 件 | `/api/news-item.php?id=news:<slug>:<項目の id>` | `get_municipal_news_item` |

仕様は `app/openapi.json`・`.yaml`。使い方の案内は `/api-guide/`。

### 索引

`tools/news/news_index.py` が、ためた記録を SQLite（`work/news/index.sqlite`）に足す。

- `run_news_fetch` が、本文を読んだ後と添付を読んだ後の 2 回呼ぶ（添付は 60 分かかることがあるので、項目と本文はその前に出す）。
- 項目・本文・添付の記録は追記だけなので、ファイルごとに読んだ位置（バイト）を覚え、増えた分だけ読む。自治体 1 つごとに確定し、読んだ位置も同じ確定に入れる。改行で終わっていない最後の行は次に回す。
- 項目は id ごとに最後の版。同じ自治体の同じ題名・同じ日付は先に見つけた 1 件だけ出す（`hidden`）。
- 並べる日付（`sort_date`）は項目の日付。日付が無いものと、見つけた日より後の日付（催しの日）は見つけた日。
- 本文の抜粋は 160 字。ページ番号・更新日・「メニューを飛ばして本文へ」などの行と題名と同じ行を飛ばし、15 字以上の最初の行から取る。
- 読み直して消えていた（`gone`）・読めなかった行で、前に読めた本文を消さない。添付も同じ。
- **本文と添付の全文は索引に入れない。** 索引は行の位置（`bodies/<年>.jsonl` と先頭からのバイト数）だけを持ち、web がそこから 1 行だけ読む。読んだ行の `id`（添付は `url`）が違えば本文を返さない。
- 索引の形を変えたら `SCHEMA_VERSION` を上げる。次の回に別のファイル（`index.sqlite.building`）で一から作り、差し替える。
- web は読むだけで開くので、WAL は使わない（読み手が `-shm` を書けない）。確定の一瞬だけ読み手を待たせる（PHP は 10 秒まで待つ）。
- 手元で 7 自治体・593 件を一から作って 0.3 秒。本番の 1 回目（2026-11-14、1,391 自治体・項目 110,708 件・本文 32,636 件・添付 14,365 本）は 130MB で、1 件 1.2KB ほど。

```bash
python tools/news/news_index.py              # 増えた分だけ
python tools/news/news_index.py --rebuild    # 一から
```

### 一覧（`/api/news.php`）

- 自治体は `municipality`（名前。「町田市」も「東京都町田市」も受ける）・`municipality_code`（5 桁か検査数字つきの 6 桁）・`slug` のどれか。同じ名前が複数ある（伊達市・府中市など）ときは 422 で候補（`candidates`）を返す。見つからない自治体も 422（`/api/search` と同じ）。
- 自治体を指定しなければ `pref_code`（1〜47）の都道府県、それも無ければ全国。
- `since`・`until` は `sort_date` で絞る。`q` は題名と概要に含まれる言葉（空白で区切ると全部を含むもの、5 語・50 字まで）。本文の全文検索ではない。題名と概要を 1 件ずつ見るので、全国（都道府県も自治体も指定しない）では期間を 31 日以内に限り、since が無ければ直近 7 日にする（全国の 10 万 8 千件を見ると 1.1 秒かかった）。
- 新しい順（`sort_date`、見つけた日時、id）。`per_page` は 100 まで（MCP は 50）、`page × per_page` は 10,000 まで。
- 集めていない自治体は `municipality.collecting` が false で、`note` を付ける。
- 応答は 5 分の `Cache-Control`。

### 1 件（`/api/news-item.php`）

- 本文（`max_body_chars`、既定 20,000 字）と、ページにあった添付をページに並んだ順に返す。
- 添付の PDF の文字は `max_attachment_chars`（添付の全部で、既定 20,000 字）まで、並んだ順に詰める。使い切った添付は `omitted`。`include_attachments=0` なら一覧だけ。
- PDF の文字は頁ごとに `〔N頁〕` の印を付ける。印は切る前に付ける（記録の改頁文字 `\f` を直す）。
- PDF 以外（Excel・Word など）と、まだ読んでいない PDF は `read: false`。
- MCP の文字の結果では、本文と添付の文字を `<<<…ここから>>>`・`<<<ここまで>>>` で囲む。第三者の文書の中に AI への指示が書かれていても、区別できるようにするため。

### 本番

- php の容器に、共有領域の `news` を `/var/www/data/news` へ読み取り専用で見せる（`deploy/deploy.py` の compose）。索引は `work` の下なので、前から見えている。
- 索引が無いうちは 503。

## 量と負荷

- 1 日に 2,556 本 × 8 回 ≒ 2 万リクエスト。半分以上は 304 で中身を送らない。相手 1 団体あたりは 1 日 8 回 × フィードの本数。
- 保存は 1 項目 600 バイトほど。1 回目の 10 万 6 千件で 62MB。その後は 1 日 2,400 件ほど（1.4MB）で、年に 500MB ほど。
- 本文は 1 件 4.5KB ほど（添付の一覧を含む）。溜まった 10 万 6 千件で 480MB、その後は 1 日 13MB・年に 5GB ほど。リンク先へのリクエストは 1 日 2,900 件ほど（相手 1 団体あたり 1 日数件）。
- 添付の PDF は 1 本 13KB ほど（本文だけ。本番の 1 回目は 4,640 行で 62MB）。1 日 5 千本ほどで、年に 24GB ほど。溜まった分（10 万 6 千件の 52%、20 本までで 26 万本ほど）は 1 回 60 分で 3 千〜4 千本ずつの見込みで、2 週間ほど。ダウンロードは 1 本平均 1.4MB で、1 日 7GB ほど。

## 残り

- 直方市の 2 本（証明書の検証に落ちる）。中間証明書を送ってこない形なら、例規の取得（`tools/reiki/scrapers/static_catalog.py` の `ca_bundle_path`）と同じく足せば通る見込み。まだ確かめていない。
- 次の段: 本文と添付の全文検索（OpenSearch）、文字の無い PDF（13%）の OCR、Excel・Word の添付、地図の自治体の欄への表示、フィードの無い団体の読み取り（[news-feed-survey.md](news-feed-survey.md) の「作るときの案」）。
- 実行状態（`management_task_statuses`）や収集状況のページにはまだ出していない。
