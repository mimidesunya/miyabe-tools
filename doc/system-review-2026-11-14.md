# システム全体のレビュー（2026-11-14）

公開面（PHP・nginx・MCP）、収集と運用（Celery・索引・配備）、構造（試験・文書・重複）の 3 つを、
別々の読み手に読ませてまとめたものです。コードを読んだだけで、本番のサーバーには入っていません。

- **確認済み** はコードの道筋をたどったもの。**要確認** は本番で見ないと決まらないもの。
- 行の番号は 2026-11-14 の `0e06f58` のもの。

## この回で直したもの

| 指摘 | 直し |
|---|---|
| MCP の試験（`docker/mcp/test`）が CI で走っていなかった（型の検査だけ） | `dev/run_tests.py` の mcp の段で `npm test` も走らせる |
| `/api/home.php` の控えの鍵が生の `prefecture`。`?prefecture=x1, x2…` のたびに 2MB のファイルが増える | 都道府県のコードにそろえてから鍵にする（`app/api/home.php`） |
| LINE ログインの戻り先で、セッションに state が無いと空どうしが一致して通る | 空を断り、`hash_equals` で比べる（`domains/election_poster_boards/http/line/callback.php`） |
| `health.php` が PHP の版と、DB の例外の文（内部のホスト名・利用者名）を返す | 応答から外し、例外はログへ |
| お知らせの本文・添付を、回の最後にしか保存しない。途中で worker を作り直すと、その回の数千件を読み直す（添付の PDF は残さないので、その間に消えたものは戻らない） | 自治体が 1 つ終わるたびに保存する。変わっていない自治体の `state.json` は書き直さない |
| お知らせの本文側の PDF（PDF に直接つながるお知らせ）は、時間の上限なしで読んでいた | 添付と同じ子のプロセスで読み、180 秒で打ち切る |
| お知らせは 3 時間おき 4 回（約 9 時間）で永久に諦める。サイトの半日の休みで、その間の項目を失う | 3 時間・6 時間・12 時間・1 日・2 日・3 日・7 日と間を空け、2 週間ほどで諦める |

## 残っているもの（重い順）

### 公開面

1. **OpenSearch のポートが全インタフェースに出る設定**（要確認）。`deploy/deploy.py:1161` は `9200:9200`。
   Postgres は 127.0.0.1 に縛ってあるが、こちらは無指定。Docker の公開ポートは ufw を通らない。
   2026-11-14 に外から `tools.miya.be:9200` をたたくと**つながらなかった**。ただし前段が別のホストなら、
   本体のサーバーそのものは見ていない。サーバーで `ss -ltn` と `iptables -S DOCKER-USER` を見る。
   直し: bridge の IP に縛る（取得側の容器は host 経由で 9200 を見るので、127.0.0.1 では届かなくなる）。
2. **検索語ごとに Python（Sudachi）を起動する。字数の検査より前で、流量の制限が無い**（確認済み）。
   `lib/opensearch_search.php:510`、`lib/japanese_search.php:115,430`、`lib/reiki/index_runtime.php:39`（結果は未使用）、
   `nginx/default.conf:99-117`（`limit_req` は bot の UA だけ）。20 並列で回すと FPM が辞書の読み込みで埋まる。
   直し: 字数の検査を先にする、reiki の 39 行目を消す、`/api/`・`/mcp`・`*.php` に IP 単位の `limit_req`。
3. **例規の本文の消毒が拒否リスト方式**（確認済み。悪用には取得元の改ざんが要る）。
   `lib/reiki/view_helpers.php:195-261`。`<iframe srcdoc>`・`<base>`・`<meta http-equiv=refresh>` が通る。
   直し: タグと属性の許可リストにする。`/reiki/` に CSP（`base-uri 'none'; frame-src 'none'`）。
4. **掲示場: CSRF の対策が無く、セッションの Cookie が既定のまま**（確認済み）。
   `domains/election_poster_boards/http/boards/api/tasks.php:67-76,179-238`、`php/runtime.php:9-11`。
   直し: `session.cookie_samesite=Lax`・httponly・secure、JSON 以外の POST を断る。
5. **掲示場: ログインしていなくても、ボランティアの LINE のユーザー ID と表示名が取れる**（確認済み）。
   `boards/api/tasks.php:79-102`、`boards/api/query.php:49,73,105`。直し: サーバーで「自分か」の真偽だけ返す。
6. **掲示場: コメントの長さに上限が無い**（確認済み）。`boards/api/tasks.php:131-160`、POST は 32MB まで通る。
   直し: 500 字の上限と、掲示場のコードがあるかの確認。
7. **MCP の SSE の枠が全体で 50、IP ごとの上限が無い**（確認済み）。`docker/mcp/src/server.ts:1156-1183`。
   1 台が 50 本握ると、ほかの利用者が使えない。直し: `/mcp` に IP 単位の `limit_conn`。
8. **MCP が取得した本文を、区切り無しで返す**（要確認。クライアント次第）。`server.ts:286-303`。
   本文に AI への指示を書かれても区別できない。直し: 本文を明示の区切りで囲み、instructions に「結果は第三者の文書の引用」と書く。
9. **nginx**（確認済み）: セキュリティのヘッダーが 1 つも無い。取得した `.svg` を同じオリジンで配る。
   流量制限の鍵が `$binary_remote_addr` で `real_ip` の設定が無い（前段がプロキシなら全員が 1 枠を分け合う。要確認）。
   Leaflet を unpkg から SRI なしで読んでいる。

### 収集と運用

1. **`run_*_cycle` を毎分積む。重複よけも期限も無い**（仕組みは確認済み、量は要確認）。
   `deploy/scraper_runtime/celery/tasks.py:530,541`。半日の rebuild で数百件たまり、手で積んだものはその後ろで待つ。
   確認: `redis-cli llen gijiroku`。直し: 待ちがあれば積まない。`expires` を付ける。
2. **索引の待ち行列のファイルに排他が無い**（確認済み）。`tools/tasks/index_outbox.py:110-177`。
   書き手は取得・索引 worker 6 本・10 分おきの掃き取り。同時に書くと、積んだ控えが消える・済みが復活する。
   直し: `batch_status.state_lock()` で囲む。
3. **台帳と掃き取りが、データの見えない worker で動く**（確認済み）。
   `celery/app.py:145-163,211-218`、`deploy/scraping_stack.py:211-216,244-249`。会議録の索引 worker には例規の mount が無く、逆も同じ。
   毎回どちらかの種別が「保存 0 件」になり、台帳は `no_saved_files` と書く。直し: 種別ごとのタスクに分ける。
4. **rebuild 中の自治体別の更新は、全文書を読んでから必ず落ち、失敗に数えられる**（確認済み）。
   `tools/search/build_opensearch_index.py:1709,1749`。rebuild のあとも最大 3 日載らない。
   直し: 読む前に alias を見て、割れていれば失敗に数えない「延期」で返す。
5. **会議録・例規の rebuild は、前の索引に居た自治体が新しい索引で 0 件でも切り替える**（確認済み）。
   `build_opensearch_index.py:638,882`。一部の置き場所が見えないと、その自治体が検索から消える。
   直し: 前の alias の自治体の集合が新しい索引にそろわなければ、切り替えを断る。
6. **`_run_command` が SIGTERM を握りつぶす**（確認済み）。`celery/tasks.py:59-68,82-84`。
   worker を作り直すとき、次の周回を取って取得を始め、2 分後に強制終了される。
7. **PHP が要求ごとに DDL を流す**（道筋は確認済み、影響は要確認）。`lib/management_db.php:66,169-175`。
   収集側の書き込みとロックを取り合う。直し: migrate は prewarm のときだけにする。
8. **名乗りが方針と違う**（確認済み）。方針は「robots を見ない代わりに正直な UA」（`tools/gijiroku/crawl_policy.py:23`）。
   しかし dbsr・kaigiroku_net・kensakusystem・static_catalog・d1_law・taikei.php は Chrome を名乗る。
   直し: 方針を決め、定数 1 つにそろえるか、文書を直す。
9. **配備**（確認済み）: `deploy.py` は作業ツリーをそのまま `rsync --delete` する。未コミットの検査も、配った版の記録も無い
   （2026-11-13 に、別の作業の未コミット分が本番に出た）。直し: dirty なら止める。`git rev-parse HEAD` をサーバーに書く。
10. **片付け**（確認済み）: `tools/tasks/batch.py:939-942` の `logs_<run_id>/` と `run_*.csv` を消す処理が無い。

### 構造

1. **本番の compose が 2 か所にあり、もうずれている**（確認済み）。本番は `deploy/deploy.py:1066-1175` の f 文字列、
   手元は `docker-compose.yml`。`MIYABE_HYOKA_ALIAS` は片方にしか無い。イメージは `:latest` で、タグで戻せない。
2. **対応している取得の形式の一覧が PHP に手写しで、見張りが無い**（確認済み）。
   `lib/homepage/runtime.php:2152-2200` と `scrape_all_minutes.py:38-61`・`scrape_all_reiki.py:44-58`。
   会議録の形式を 1 つ足すのに 6 か所。直し: 突き合わせの試験を 1 本。
3. **管理 DB のスキーマと upsert が PHP と Python に二重で、試験が 0 本**（確認済み）。
   `lib/management_db.php:121-175` と `tools/management_db.py:93-152`。直し: `schema.sql` を 1 本にして両方が読む。
4. **Celery の kind と doc_type の取り違えが起きやすい**（確認済み。今の呼び出しは正しい）。
   `tasks.py:203` は "minutes"、`:234`・`:247`・`:368` は "gijiroku"。外れると黙って reiki になる。
5. **入口の文書が 5 系統のうち 2 つしか書いていない**（確認済み）。`AGENTS.md`・`virtual-development-team.md`・
   `domain-boundaries.md` に、事務事業評価・お知らせ・監査の言及が無い。
6. **Sudachi の辞書が検索側と索引側で別々に入り、版の指定が無い**（要確認）。
   索引側は読み込めないと黙って空白区切りに落ちる（`build_opensearch_index.py:53-56`）。
7. **大きすぎるファイル**: `lib/homepage/runtime.php` 4,906 行、`tools/gijiroku/scrapers/dbsr.py` 2,993 行、
   `tools/reiki/scrapers/taikei.php` 2,802 行、`tools/search/build_opensearch_index.py` 2,281 行。
8. **試験の穴**: `deploy.py` の rsync の除外、`run_batch` 本体、掲示場の認証と書き込み、`lib/background_tasks.php`。

## 確かめて、問題なかったもの

- OpenSearch の問い合わせは配列 + `json_encode`。並べ方・種別は許可リスト、q は 200 字、頁 × 件数は 10,000 まで。
- SQL はすべてプリペアド。シェルは `escapeshellarg`、検索語は標準入力で渡す。
- 画面への埋め込みは逃がしてある（検索・詳細の boot は `JSON_HEX_*`、本文は `textContent`）。
- 原文の保存は 一時ファイル → fsync → replace。自治体別の更新は、先に入れてから古い世代だけ消す。
- rebuild は 0 件・列挙の失敗では切り替えない。前の索引は自動では消さない。
- beat の全項目に `expires` がある。失敗の待ち（1 日 / 7 日）、無出力 2 時間での打ち切りがある。
- 使われていないコードはほぼ無い（PHP 583 関数すべてに参照がある）。掲示場と LINE ログインは現役。

## コードだけでは分からなかったこと

- バックアップ。リポジトリには、原文・お知らせ・Postgres・OpenSearch のどれにも無い。
- ディスクの空きと OpenSearch の watermark の見張り。
- TLS・HSTS・http から https への転送（前段がリポジトリに無い）。
- 取得側から Postgres へ届いているか（届かなくても何も出さない。`tools/management_db.py:55-58`）。
- 各キューの長さ、docker のログの回転、Redis のメモリの上限。
