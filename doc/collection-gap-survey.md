# 収集取りこぼしの調査と対応計画

調査日: 2026-08-30
きっかけ: 福岡県議会の議員連盟補助金を調べようとしたが、根拠となる委員会記録も
補助金交付規則も検索に出てこなかった。原因を追ったところ、福岡県固有ではなく
**全国規模の収集漏れ**であることが分かった。

このファイルは、担当を引き継ぐ人（AI を含む）が調査を再現し、続きを進めるための
記録である。**未完了の作業が残っている。** 「進め方」の章から読むとよい。

---

## 1. 何が起きているか（確定した事実）

### 1-1. 例規集: legal-square は検索件数に取得元ごとの上限がある（**原因判明・対応済み**）

`data/municipalities/reiki_system_urls.tsv` の `system_type=legal-square` は 103 自治体。
保存済み例規の件数（`/mnt/big/miyabe-tools/reiki/<slug>/html` のファイル数）を数えると、
**丸い数字に固まっている**。

| 保存件数 | 自治体数 |
| --- | ---: |
| ちょうど 100 件 | **55** |
| ちょうど 1000 件 | 7 |
| ちょうど 500 件 | 6 |
| ちょうど 250 件 | 3 |
| 101〜999 件 | 29 |
| 1000 件超 | 11 |

福島県（`07000-fukushima-ken`）が 100 件ちょうど。都道府県の例規が 100 件ということは
ありえないので、実データではなく打ち切りである。

他系統の中央値は 700〜800 件なのに対し、**legal-square の中央値は 100 件**。

`--per-target-limit` は `TAIKEI_LIKE_SYSTEMS = {"taikei", "g-reiki"}` にしか渡らないので
（`tools/reiki/scrape_all_reiki.py:134`）、**こちら側の件数上限ではない**。

#### 原因: ページ送りではなく、取得元ごとの検索件数上限

福島県の詳細検索を実際に開いて確かめたところ、**ページ送りは正常に動いていた**
（1 ページ 10 件・10 ページ）。問題は総件数のほうで、条件を空にした検索の結果表示が

> 1〜10件目/100件

と、**100 件で頭打ちになっていた**。種別を「条例」だけに絞っても、「規則」だけに絞っても
やはり 100 件ちょうど。一方「訓令」は 54 件、「委員会等規程」は 33 件と上限未満の値が出る。
つまり 100 は実データではなく、**この取得元に設定された 1 回の検索の上限**である。

保存件数が 100 / 250 / 500 / 1000 という丸い数字に固まっていたのは、
**取得元ごとに上限の設定値が違う**ためだった。福岡県は上限 1000、福島県は上限 100。

#### 落とし穴その 2: 「次へ」が最終ページでも止まらない

一覧の「次へ」は**最終ページでも無効にならず、押せてしまう**。押すと同じページが
返るだけなので、`a:has-text('次へ')` の有無と `disable` クラスだけを見ていると
**永久に回り続ける**。実際に福岡県の「規則」で 219 ページ目まで回っていた
（総数 317 件・7 ページで終わりのはず）。
件数表示「1～50件目/317件」の末尾番号が総数に達したら終わり、と直した。

このループが、担当交代前に「例規スクレイパが固まる」と見えていた現象の正体である
（出力が無いまま延々と回るので、外からは停止と区別が付かない）。

#### 落とし穴その 3: 不正な日付でも検索が「成功」して見える

上限を割るために制定年月日で範囲を絞ろうとしたところ、**明治1年1月1日**を始点にすると

> 年月日(FROM) に正しい日付をご記入ください。

と弾かれ、**検索が実行されないまま前の結果が残る**。件数表示も前のままなので、
黙って古い件数を読んでしまう。同じ理由で、検索直後に件数を読むと
前の検索結果を読み違えることがある（AJAX の差し替え待ちが要る）。

### 1-1-1. 他の例規システムに同じ上限は無い（確認済み）

legal-square と同種の打ち切りが他系統にもあるかを、保存件数で確かめた。

| 系統 | 対象 | 中央値 | 丸い数字 | 内訳 |
| --- | ---: | ---: | ---: | --- |
| taikei | 551 | 745 | 2 | 400×1, 750×1 |
| g-reiki | 516 | 827 | 3 | 1000×2, 750×1 |
| d1-law | 429 | 785 | 3 | 1000×2, 750×1 |
| **legal-square** | 103 | **101** | **62** | **100×49**, 1000×6, 500×5, 250×2 |
| h-chosonkai | 65 | 617 | 2 | 400×1, 600×1 |
| その他 | 59 | 707〜968 | 0〜1 | |

legal-square 以外は 400〜550 自治体で丸い数字が 2〜3 件しかなく、偶然の範囲。
中央値も 617〜968 で正常。**この形の取りこぼしは legal-square だけ**である。

なお `system_type=独自` の 26 自治体は対応スクレイパが無く、例規を 1 件も
取得していない（`SUPPORTED_SYSTEMS` に含まれない）。取りこぼしではなく未対応。

### 1-2. 例規集: 福岡県で交付規則が欠けていたのも同じ原因（**対応済み**）

`legal_square.py` の冒頭には元から「詳細検索は最大 1000 件で打ち切られる」と
書かれていた。福岡県はこの上限が 1000 で、条件を空にした 1 回の検索では
1000 件で切られるため「福岡県補助金等交付規則（昭和33年規則第5号）」が落ちていた。
1-1 と同じ話で、上限値が取得元ごとに違うだけである。

### 1-3. 会議録: 「委員会 0 件が 835 自治体（55%）」は**誤検出だった**

当初は `meetings_index.json` の会議名に「委員」を含むものが 1 件も無い自治体を数え、
1514 中 835（55%）が取りこぼしだと見ていた。**この数え方が間違っていた。**

会議種別の持ち方は取得元によって違う。voices 系は
「総務常任」「予算特別」「厚生経済」のように**「委員会」の語を落とした形**で持つ。
港区（6455 会議）を「委員会 0 件」と数えていたが、実際の `meeting_group` は
総務常任 1090・建設常任 1056・保健福祉常任 787 …と、委員会は**ちゃんと取れていた**。

数え直したのが `tools/gijiroku/audit_meeting_types.py`。
「委員会という語が無い」ではなく **「どの会議も本会議の言い回ししか持たない」**
を疑いの条件にする（会議数 200 件以上に限る。小規模議会は委員会を公開していない
ことが普通なので）。

| 系統 | 対象 | 本会議のみ | 種別不明 | 種別=表題 |
| --- | ---: | ---: | ---: | ---: |
| kaigiroku.net | 527 | 221 | 0 | 0 |
| kensakusystem | 139 | 65 | 1 | 0 |
| dbsr | 172 | 58 | 3 | 9 |
| gijiroku.com（voices 含む） | 83 | 13 | 0 | 19 |
| 独自 | 521 | 12 | 34 | 0 |
| static-kaigiroku-dir | 33 | 2 | 1 | 0 |
| site-gikai-pdf | 24 | 0 | 1 | 0 |
| **合計** | **1512** | **371** | **40** | **28** |

- **本会議のみ** — 委員会の入口を見落としている疑い
- **種別不明** — 会議種別が記録されておらず、この情報だけでは判定できない
- **種別=表題** — 会議種別が会議ごとに全部違う。種別として機能していない

### 1-3-1. 「本会議のみ」の大半は、取得元が本会議しか公開していない

疑わしいと出た自治体を取得元まで当たった結果、**多くはスクレイパの落ち度ではなかった**。

| 自治体 | 系統 | 確認したこと |
| --- | --- | --- |
| 鳴門市・沖縄市 | kaigiroku.net | API の会議区分ツリーに `/0/1/4/`（委員会）が**存在しない**。本会議だけ |
| 米子市 | kensakusystem | 入口に「ご覧になれるのは平成5年第378回臨時会以降の**本会議録**です」と明記 |
| 佐賀市 | gijiroku.com | 検索フォームの会議種別が「定例会」「臨時会」だけ。委員会の区分が無い |

比較のため委員会が取れている自治体も見た。渋谷区（kaigiroku.net）の会議区分ツリーには
`/0/1/4/8/10/`（全会議＞委員会＞常任委員会＞総務委員会）以下が並び、
スクレイパはこれを正しく拾っている。**`council_type_path` が `/0/1/` 始まりに限る絞り込みは
委員会を落としていない**（委員会も `/0/1/4/` 配下にある。`/0/2/` は「資料」なので除外が正しい）。

### 1-3-2. 区別が付くようにした（**対応済み**）

取得元は検索フォームや API で「こういう会議種別がある」と自分から示している。
それを `scrape_state.json` の `source_coverage.offered_meeting_types` へ残すようにした。
**提示された種別に委員会があるのに 1 件も収録できていない**なら取りこぼし確定、
**提示された種別にも委員会が無い**なら取得元が公開していないだけ、と機械で分かる。

| 系統 | 読み取り元 |
| --- | --- |
| dbsr | 検索フォームの `select[name="CabinetName[]"]` |
| kaigiroku.net | `councils/index` API の会議区分ツリー（`council_type_name2` 以下） |
| gijiroku.com / voices | 検索フォームの `KGTP` チェックボックスのラベル |
| kensakusystem | 読み取り不要。ツリーを全部歩いて取るので提示＝収録 |

全件取得をやり直さなくても済むよう、**種別の一覧だけを取りに行く**
`tools/gijiroku/probe_offered_meeting_types.py` を用意した。1 自治体あたり
数リクエストで終わる。

### 1-3-3. 突き合わせた結果

`probe_offered_meeting_types.py` を dbsr・gijiroku.com・kaigiroku.net の
782 自治体へ流し（失敗 0）、収録内容と突き合わせた。

| 系統 | 対象 | 取りこぼし | 取得元も本会議のみ | 未確認 |
| --- | ---: | ---: | ---: | ---: |
| kaigiroku.net | 527 | **1** | 220 | 0 |
| dbsr | 172 | **4** | 53 | 0 |
| gijiroku.com | 83 | 0 | 13 | 0 |
| kensakusystem | 139 | — | — | 65 |
| 独自 | 521 | — | — | 12 |

kensakusystem と独自は取得元が会議種別を示す仕組みが無いので突き合わせられない。
ただし kensakusystem はツリーを全部歩く作りなので取りこぼしは起きにくい
（米子市は入口に「本会議録です」と明記されていた）。

取りこぼしが確定した 5 自治体:

| 自治体 | 会議数 | 取得元が示す委員会 |
| --- | ---: | --- |
| 23000-aichi-ken 愛知県 | 1052 | 総務企画委員会・県民環境委員会・福祉医療委員会・経済労働委員会… |
| 34000-hiroshima-ken 広島県 | 855 | 総務委員会・生活福祉保健委員会・農林水産委員会・建設委員会… |
| 19201-kofu-shi 甲府市 | 792 | 総務委員会・民生文教委員会・経済建設委員会・環境水道委員会… |
| 23213-nishio-shi 西尾市 | 688 | 企画総務委員会・厚生環境委員会・文教交流委員会・経済建設委員会… |
| 15227-tainai-shi 胎内市 | 572 | 委員会・常任委員会（kaigiroku.net） |

dbsr の 4 自治体は 1-4 の修正だけでは取れず、**別の原因**だった（1-3-4）。
胎内市（kaigiroku.net）は未対応。

### 1-3-3-1. 補った一覧の会議種別が空だった（**対応済み**）

`missing_cabinet_list_pages()` が返す一覧の `meeting_group` を空のまま渡していたため、
そこから取れた会議が**種別なしで記録されていた**。愛知県は 1052 → 1368 会議に
増えたのに、増えた 316 件の種別が空で、委員会を取れているかを後から判定できなかった。
会議種別は補う時点で分かっているので、そのまま渡すよう直した。

**「件数が増えた」だけで確認を止めると、この種の穴は見逃す。**
必ず `audit_meeting_types.py` で種別まで見ること。

### 1-3-4. 年度別一覧が全期間そろっていても、本会議だけのことがある（**対応済み**）

`discover_list_pages()` は年度別一覧（`div.LibraryTable`）を読めた時点で
**そのまま完了として返していた**。ところが愛知県の一覧リンクは
`Cabinet=1`（本会議）ぶんだけで、それが 1996 年から 2026 年まで揃っている。
期間の抜けが無いので「全部取れた」ように見え、委員会が丸ごと欠けたまま
`source_coverage.state=complete` になっていた。

**対応済み**: 年度別一覧を読み終えたあとに検索フォームの会議種別と突き合わせ、
**一覧に出てこない種別ぶんを全期間の一覧として補う**
（`missing_cabinet_list_pages()`）。愛知県で実測:

```
修正前: [INFO] 会議一覧ページ 210 件
修正後: [INFO] 一覧に出てこない会議種別 91 件を全期間で補います
        [INFO] 会議一覧ページ 301 件
```

あわせて、会議種別の選択肢を探す範囲も広げた（`collect_cabinet_options()`）。
検索ページに無くても閲覧メニューまで見に行く。愛知県は検索ページに選択肢が
無かったため `offered_meeting_types` が空で記録され、**取りこぼしを
「取得元も本会議のみ」と誤判定していた**。

**当初の「835 自治体（55%）が取りこぼし」は、実際には 6 自治体だった**
（上の 5 件＋福岡県）。残りは取得元が本会議しか公開していない。

### 1-3-5. gijiroku.com の旧形式で会議種別を決め打ちしていた（**対応済み**）

`gijiroku_com.py` の旧形式向け探索（`discover_legacy_voices_meeting_items`）は
一覧 URL に `KGTP=1,2` を埋め込んでいた。`KGTP` は会議種別の指定で、割り当ては
取得元によって違う。大田区で実測すると:

| KGTP | 会議数 | うち委員会 |
| --- | ---: | ---: |
| `1,2`（修正前） | 48 | **0** |
| `1,2,3,4`（修正後） | 152 | 76 |

存在しない番号は無視されるので、全種別を並べて指定する形に直した。
なお大田区・港区・広島市は年度ページ経由で取れており旧形式には落ちていないため、
これは**表に出ていなかった不具合**である。

### 1-4. 会議録: dbsr の会議種別が本会議だけ（**対応済み**）

`tools/gijiroku/scrapers/dbsr.py` の `widened_period_list_pages()` は、
**入口メニューに現れた会議種別しか期間拡張しない**作りだった。

福岡県（`www.pref.fukuoka.dbsr.jp`）の入口ページにある期間つきリンクは 13 本あるが、
会議種別は `t`（本会議定例会）・`r`（本会議臨時会）・`t,r` の 3 つだけ。
一方、検索フォームの `select[name="CabinetName[]"]` には **50 種別**があり、
総務企画地域振興委員会（`sc`）以下 48 種別はリンクが無いので探索対象外だった。

**対応済み**: `read_cabinet_options(page)` で検索フォームの会議種別を全部読み、
メニュー由来の URL と同じ形で種別ぶんの一覧 URL を生成する。

```
修正前: [INFO] 会議一覧ページ 3 件
修正後: [INFO] 直近分の一覧しか無いので、期間を全期間へ広げます（会議種別 50 件）
        [INFO] 会議一覧ページ 50 件
```

### 1-5. 「検索反映待ち」の表示が誤解を招く（**未対応**）

福岡県の状態表示:

> 取得元の全一覧から 1899 件を取得済みです。現在は 1859/1899 件を検索でき、
> 残りを検索へ反映中または反映待ちです。

`--mode update` で再索引しても 1859 件のままで `deleted=0`。差の 40 件は
**中身のない「休会」記録**（保存 2020 件のうち 451 件が休会。本文は日付と URL だけの 985 バイト）。
索引側が落とすのが正しい挙動なので、**永久に埋まらない差を「待ち」と表示している**。

---

## 2. 調査の再現手順

### 2-1. サーバーへコマンドを送る

`deploy/deploy.py` の `ssh_exec` を借りるのが早い。

```python
import sys, os
ROOT = r"F:\dev\mimidesunya-public\miyabe-tools"
sys.path.insert(0, os.path.join(ROOT, "deploy")); os.chdir(ROOT)
import deploy as D
cfg = D.load_config("deploy.json"); D.prepare_ssh_key_from_config(cfg)
D.ssh_exec(cfg, "任意のコマンド", stream=True)
```

- 共有データ: `/mnt/big/miyabe-tools`（`gijiroku` / `reiki` / `work`）
- スクレイパ: `docker compose -f docker-compose.scraping.yml exec -T scraper-gijiroku ...`
  （例規は `scraper-reiki`、索引は `scraper-gijiroku-index` / `scraper-reiki-index`）
- Git Bash から `/tmp/...` を渡すときは `MSYS_NO_PATHCONV=1` を付ける。付けないと
  Windows のパスへ変換されて失敗する。

### 2-2. 例規の件数を数える

```sh
cd /mnt/big/miyabe-tools/reiki
for d in */; do n=$(find "$d/html" -type f 2>/dev/null | wc -l); echo "$n ${d%/}"; done | sort -rn
```

`reiki_system_urls.tsv` の `system_type` と突き合わせ、丸い数字に固まっていないかを見る。

### 2-3. 会議録の会議種別の偏りを数える

```sh
docker compose -f docker-compose.scraping.yml exec -T scraper-gijiroku   python tools/gijiroku/audit_meeting_types.py --only-issues
```

系統ごとの集計は stderr に出る。`--system dbsr` で系統を絞れる。

判定は次の 4 つ。

- **取りこぼし** — 取得元は委員会があると言っているのに 1 件も無い。直すべき対象
- **本会議のみ(取得元も)** — 取得元にも委員会が無い。これ以上取れるものはない
- **本会議のみ(未確認)** — 取得元の言い分をまだ記録していない。下の probe を先に走らせる
- **種別不明 / 種別=表題** — 会議種別が使える形で残っていない

取得元の言い分を集めるには、全件取得をやり直さなくても次で足りる。

```sh
docker compose -f docker-compose.scraping.yml exec -T scraper-gijiroku   python tools/gijiroku/probe_offered_meeting_types.py --system dbsr
```

`--system` は dbsr / kaigiroku.net / gijiroku.com。kensakusystem と独自は対象外
（前者はツリーを全部歩くので提示＝収録、後者は共通の読み取り口が無い）。

### 2-4. MCP で検索して確かめる

```
POST https://tools.miya.be/mcp
Accept: application/json, text/event-stream
{"jsonrpc":"2.0","id":1,"method":"tools/call",
 "params":{"name":"search_minutes","arguments":{"q":"...","municipality_code":"40000"}}}
```

`municipality_code` は全国地方公共団体コード（福岡県は `40000`）。
`sort` は `date` または `relevance` のみ。`per_page` は最大 50。

---

## 3. 対応計画

### 済んだこと

| 項目 | 内容 |
| --- | --- |
| legal-square の件数上限 | 種別＋制定年月日で再帰分割。`tools/reiki/scrapers/legal_square.py` |
| legal-square の日付検証 | 元号の実在する開始日・終了日で範囲を刻む。エラー時は警告して打ち切らない |
| legal-square の件数読み違え | 検索前に件数表示へ印を付け、差し替わるまで待ってから読む |
| dbsr の会議種別 | 検索フォームから全種別を読む。`tools/gijiroku/scrapers/dbsr.py` |
| 福島県での効果確認 | 例規 100 → 1598 件（件数のみ、本文取得なし） |

**既存テスト `tools/gijiroku/test_dbsr.py` 14 件は全通過**（`PYTHONPATH=. python tools/gijiroku/test_dbsr.py`）。

### 残っていること

#### A. legal-square の再取得（**修正済み・全 103 自治体の再取得が残り**）

原因は 1-1 のとおり検索件数の上限で、ページ送りは正常だった。
`legal_square.py` を種別＋制定年月日の再帰分割に作り直した（5 章）。

福島県で件数だけ数えた結果（本文取得なし・所要 174 秒）:

| | 件数 |
| --- | ---: |
| 修正前（条件なしで 1 回検索） | 100 |
| 修正後（12 種別 × 制定年月日で 31 分割） | **1598** |

残っているのは **legal-square 全 103 自治体の再取得**。100 件ちょうどの 55 自治体が
最優先だが、250 / 500 / 1000 件の 16 自治体も同じ理由で欠けている。

参考: 100 件ちょうどの自治体（先頭）
`01213-tomakomai-shi` `03202-miyako-shi` `04209-tagajo-shi` `04213-kuriharacity-shi`
`06210-tendo-shi` `07000-fukushima-ken` `08202-hitachi-shi` `09203-tochigi-shi`
`11215-sayama-shi` `11232-kuki-shi` `11245-fujimino-shi` `12204-funabashi-shi`

#### B. dbsr の取りこぼし 4 自治体の再取得（**実行中**）

1-3-3 で確定した愛知県・広島県・甲府市・西尾市。1-4 の修正で取れるはず。
`/tmp/dbsr_fix.sh` で順に流している（6 章）。

#### C. kaigiroku.net / kensakusystem の突き合わせ（**probe 実行中**）

kaigiroku.net の 221 自治体は `probe_offered_meeting_types.py` の結果待ち。
kensakusystem の 65 自治体は読み取り口が無いので、会議数の多い順に手で当たる。

#### D. 会議種別が種別として機能していない（**対応済み・再取得で反映**）

`meeting_group` が会議ごとに全部違う値になっていた（「令和８年第１回定例会（第７号）」など）。

- dbsr: `meeting_group_from_meeting_name()` で年・回次・日別番号・文書種別を落とす
  （「令和８年第１回定例会（第７号）」→「定例会」）
- gijiroku.com: `trim_group_label()` で日付から先を落とす
  （「１２月定例会－11月28日-01号」→「１２月定例会」）

既存データには反映されないので、再取得したときに直る。

#### E. 例規検索が日付順で本則を埋もれさせる（**対応済み**）

「補助金等交付規則」で本則が 15 位に沈んでいたのは順位付けではなく
**MCP の既定ソートが日付順**だったため（`sort=relevance` なら 1 位）。
例規は制定が古い基本規則ほど重要なことが多いので、`search_reiki` の既定を
関連度順にした（`docker/mcp/src/server.ts`）。会議録は日付順のまま。

#### F. 「反映待ち」表示（**対応済み**）

`lib/homepage/runtime.php` で、取得した会議数ではなく
`document_kinds.json` の**本文がある件数**と突き合わせるようにした。
目次や休会など本文の無い記録の差を「反映待ち」と書かなくなる。

#### G. 定期実行（未対応）

2-2（例規の件数が丸い数字に固まっていないか）と 2-3（会議種別の偏り）を定期実行し、
一覧で出す。2-3 は C の突き合わせが入ったので、そのまま「直すべき対象」の一覧になる。

---

## 4. 進め方（引き継ぐ人へ）

福岡県の例規は完了・検証済み（6 章）。残りは会議録側。

1. **走っているジョブを確認する。** 福岡県の会議録は celery が
   `--discovery-timeout-seconds 0` で取得中（6 章）
2. 完了したら OpenSearch へ再索引する

   ```sh
   docker compose -f docker-compose.scraping.yml exec -T scraper-gijiroku-index \
     python tools/search/build_opensearch_index.py --mode update \
     --doc-type minutes --slug 40000-fukuoka-ken
   ```

   例規は `--doc-type reiki` と `scraper-reiki-index`
3. **検証**: 会議録に「**総務企画地域振興委員会**」が入り、
   議員連盟補助金の審議が見つかるか
4. 3 章 C（取得元が公開していないのか、こちらの見落としかを機械で区別する）へ進む。
   ここが片付かないと、A・B 以外にどれだけ取りこぼしがあるのか分からない

### 元の調査目的

毎日新聞の報道で、福岡県が県議の任意団体「議員連盟」13 団体に年 1200 万円規模の
補助金を 30 年以上出していたことが判明した。明確な審査基準が無く、県議会事務局は
「経緯があやふや」として廃止を検討している。

本会議記録（1995〜2026 年・1859 件）を調べた範囲では、**議連の海外訪問や活動は
繰り返し報告されているのに、その費用構造に触れた発言が 1 件も無い**。
補助金の審議が行われるはずの委員会記録が未収録だったため、
「議事録に無い」とは結論できない状態だった。

例規側では「福岡県補助金等交付規則の適用を受けない交付金及び給付金の指定」
（昭和33年告示第291号・全 65 項目）を確認済み。政務活動費交付金（第40号）や
宿泊税交付金（第55号）は載っているが、**議員連盟への補助金は載っていない**。
本則の「福岡県補助金等交付規則」が取れれば、議連補助金が同規則の手続
（交付申請・交付決定・実績報告・額の確定）の対象かどうかを判定できる。

---

## 5. 触ったファイル

| ファイル | 変更 |
| --- | --- |
| `tools/reiki/scrapers/legal_square.py` | 種別＋制定年月日の再帰分割。`read_result_total` / `detect_cap` / `month_slots` / `apply_filters` / `span_label` / `run_search`（差し替え待ちと日付エラー検出）/ `harvest_pages` / `collect` |
| `tools/gijiroku/scrapers/dbsr.py` | `read_cabinet_options` を追加し、`widened_period_list_pages` に `cabinet_options` 引数を追加 |
| `tools/gijiroku/scrapers/gijiroku_com.py` | 旧形式探索の `KGTP` を全種別指定に。`read_offered_meeting_types` 追加。`trim_group_label` で日付以降を落とす |
| `tools/gijiroku/scrapers/kaigiroku_net.py` | `offered_type_labels` を追加し、会議区分ツリーのラベルを記録 |
| `tools/gijiroku/audit_meeting_types.py` | 新規。提示された会議種別と収録内容を突き合わせる |
| `tools/gijiroku/probe_offered_meeting_types.py` | 新規。取得はせず、取得元が示す会議種別だけを取りに行く |
| `tools/gijiroku/test_dbsr.py` / `test_gijiroku_com.py` | 会議種別の切り出しのテストを追加 |
| `tools/gijiroku/test_gijiroku_targets.py` | robots.txt で除外しない方針に合わせて期待値を修正（既存の失敗テスト） |
| `lib/homepage/runtime.php` | 「反映待ち」を本文のある件数と突き合わせる |
| `docker/mcp/src/server.ts` | `search_reiki` の既定ソートを関連度順に |

`legal_square.py` の分割の考え方:

1. 条件なしで 1 回検索し、総件数と「◯件を超え」の表示から**この取得元の上限**を推定する
2. 上限に達していなければそのまま全件取得して終わり
3. 達していれば種別（条例・規則・告示…）で分ける
4. 種別だけでは上限に張り付くものは、**制定年月日の範囲を二分**して上限を下回るまで細かくする
   （最小単位は 1 か月。元号の実在する開始日・終了日に丸めてあるので日付検証に弾かれない）
5. 種別ごとの「全期間」検索の結果も取り込むので、制定年月日が無い例規も拾える
6. 題名・番号・公布日のハッシュで重複を除くため、範囲が重なっても二重には取らない

いずれも**未コミット**（2026-08-30 時点）。

---

## 6. 作業中の状態（2026-08-30 12:05 JST 更新）

### サーバーで走らせているジョブ

| ジョブ | ログ | 起動のしかた |
| --- | --- | --- |
| 福岡県 例規（種別＋年月日分割） | `/tmp/reiki_fk.log` | `/tmp/reiki_watchdog.sh` |
| 福岡県 会議録（50 種別） | celery のログ | celery が自動で起動（`--discovery-timeout-seconds 0`） |
| legal-square 全 103 自治体の再取得 | `/tmp/ls_backfill_0.log` 〜 `_2.log` | `sh /tmp/ls_backfill.sh 0`〜`2`（3 並列） |
| 取得元が示す会議種別の収集 | `/tmp/probe_offered.log` | `sh /tmp/probe_offered.sh` |
| dbsr の取りこぼし 4 自治体の再取得 | `/tmp/dbsr_fix.log` | `sh /tmp/dbsr_fix.sh` |

`ls_backfill.sh` は `/tmp/legal_square_slugs.txt` を 3 で割って担当を分け、
ログが 900 秒伸びなければ固まったとみなして殺し、次の自治体へ進む。
`dbsr_fix.sh` は celery が同じ自治体を掴んでいる間は待つ（二重に書くと索引が壊れる）。

**legal-square の再取得が終わったら OpenSearch へ反映すること。**
slug 直指定で走らせているので、バッチ経由の自動索引更新が働かない。

```sh
docker compose -f docker-compose.scraping.yml exec -T scraper-reiki-index   python tools/search/build_opensearch_index.py --mode update --doc-type reiki --slug <slug>
```

会議録は手で起動した分が `--discovery-timeout-seconds 3600` に引っかかって
11:59 に `DiscoveryTimeoutError` で落ちた。**全期間の一覧は 1 会議種別あたり数分かかり、
50 種別では 1 時間では終わらない。** その後 celery が同じ自治体を
`--discovery-timeout-seconds 0`（無制限）で拾い直したので、そちらに任せている。

### 固まったら再実行する見張り（`/tmp/reiki_watchdog.sh`）

legal-square のスクレイパは、ポップアップ（本文ビューア）待ちで**無出力のまま
固まることがある**（CPU 0・ファイルも増えない）。Playwright の `evaluate` には
タイムアウトが無いため、待ち続けてしまう。ログが 600 秒更新されなければ
kill して同じコマンドを流し直す見張りをサーバーの `/tmp/reiki_watchdog.sh` に置いた。
既存ファイルは読み飛ばすので続きから進む。

```sh
setsid nohup sh /tmp/reiki_watchdog.sh > /tmp/reiki_watchdog.out 2>&1 < /dev/null &
```

手で流すなら:

```sh
cd ~/services/miyabe-tools
docker compose -f docker-compose.scraping.yml exec -T scraper-reiki   python tools/reiki/scrapers/legal_square.py --slug 40000-fukuoka-ken --system-type legal-square
```

**`--limit` は付けないこと。** 付けると最後にマニフェストがその件数で上書きされ、
収録が縮む（一度やって 1000 → 150 件に縮めた。全件取得で回復させた）。

### サーバーの配置について

`~/services/miyabe-tools` は **git のチェックアウトではない**（`.git` が無い）。
スクレイパを試すときはファイルを直接置き換えている。リポジトリの内容と
食い違うことがあるので、動かす前に中身を確かめること。

### 未コミットの変更

このリポジトリは以下が未コミット。**コミットしていない。**

今回の調査に関わるもの:

- `tools/reiki/scrapers/legal_square.py` — 種別＋制定年月日の再帰分割
- `tools/gijiroku/scrapers/dbsr.py` — 会議種別の全列挙
- `doc/collection-gap-survey.md` — この文書
- `doc/README.md` — 索引に 1 行追加

同じ作業ツリーに、**別件（政治活動ビラ作成ツール）の未コミット変更**も入っている。
混ぜないこと。

- `app/tools/`（`/tools/bira/` の画面）
- `lib/bira/`（テンプレート・素材・フォント・AI 生成・回数制限）
- `lib/cti/`（Copper PDF の CTIP2 ドライバ同梱、Apache-2.0）
- `app/robots.txt`（`Disallow: /tools/`）
- `docker/php/requirements-python.txt`（fonttools・brotli 追加）
- `domains/election_poster_boards/`（ログイン後に `/tools/` 配下へ戻す）

コミットするなら 2 つに分ける。

```sh
# 収集取りこぼしの調査と修正
git add tools/reiki/scrapers/legal_square.py tools/gijiroku/scrapers/dbsr.py         doc/collection-gap-survey.md doc/README.md
# ビラ作成ツール（別件）
git add app/tools lib/bira lib/cti app/robots.txt         docker/php/requirements-python.txt domains/election_poster_boards
```

### 放置したときに何が治り、何が治らないか

**取得まわりは放置で治る。** celery beat が生きていて（`miyabe-tools-scraping-scraper-beat-1`）、
会議録・例規とも 1 日おきに周期取得を投入する。各自治体は
**前回の確認から 30 日経つと再取得の対象に戻る**（`tools/freshness_metadata.py`
の `FRESHNESS_SKIP_DAYS = 30`）。周期取得は `--index-dispatch celery` なので
OpenSearch の増分更新も自動で走る。

`data/background_tasks/*_snapshot.json` の `last_checked_at` から、対象に戻る時期は次のとおり。

| 対象 | 前回確認 | 再取得の対象に戻る |
| --- | --- | --- |
| legal-square 103 自治体 | 2026-08-02〜08-03 | **2026-09-01 頃** |
| dbsr の取りこぼし 4 自治体 | 2026-08-06〜08-08 | **2026-09-05 頃** |

修正済みのスクレイパはサーバー上のファイルと一致していることを確認済み
（`tools/reiki/scrapers/legal_square.py` ほか 5 ファイル）。
6 章のジョブを全部止めても、上の日付以降に順次直っていく。

### 一度失敗した自治体は永久に巡回対象から外れていた（**対応済み**）

上の「30 日で対象に戻る」を実測で確かめたところ、**戻らない自治体があった**。

| | 30 日以上放置 | うち status=failed |
| --- | ---: | ---: |
| 例規 | 6 | **6** |
| 会議録 | 45 | **45** |

`tools/tasks/priority.py` は実エラーで失敗した自治体を `previous_failed`
（score=0）として巡回から外す。手動の `--retry-failed` を待つ設計だが、
celery の周期取得はこのフラグを渡さないので**誰も再試行しない**。
八丈町の例規は 2026-05-17 から 105 日間、対象に戻っていなかった。

**対応済み**: 失敗から `FAILED_RETRY_DAYS = 7` 日経ったら自動で 1 度やり直す
（`failure_is_retryable()`）。すぐに再実行しない元の意図は保ちつつ、
放置で永久に止まる状態をなくした。適用後の選定:

```
例規:   failed_retry=39  → 実行対象 35 自治体
会議録: failed_retry=60  incomplete=36
```

**放置では治らないもの（残り）:**

1. 取得元の作りが変わって失敗し続ける自治体は、7 日おきに再試行を繰り返すだけで
   直らない。`audit_meeting_types.py` と `--list-targets` の `failed_retry` を
   ときどき見て、繰り返し失敗しているものは個別に直す
2. 例規側の会議種別にあたる仕組み（`offered_meeting_types`）は kensakusystem と
   独自系には無い。この 2 系統の取りこぼしは機械では判定できない

### 次にやること

1. 6 章の 4 つのジョブの生死を確認する
2. legal-square の再取得が終わったら、対象 slug を OpenSearch へ再索引する
3. `probe_offered_meeting_types.py` が kaigiroku.net まで終わったら
   `audit_meeting_types.py` を流し直し、「取りこぼし」に出た自治体を取り直す
4. 福岡県の会議録に「総務企画地域振興委員会」が入ったかを確認する

### 福岡県 例規の結果（2026-08-30 13:12 完了）

| | 件数 |
| --- | ---: |
| 修正前 | 1000（上限で打ち切り） |
| 修正後 | **1221**（15 種別の合計） |

**「福岡県補助金等交付規則」（昭和33年規則第5号・1958-03-01）を取得・索引済み。**
`--doc-type reiki --slug 40000-fukuoka-ken` で再索引し、`count=1222`。

なお福岡県はどの種別も上限 1000 に届かなかったため、制定年月日の分割は発動していない。
その経路は福島県で確認済み（100 → 1598 件）。

### 付随して見つかった問題: 例規検索の順位付け

`search_reiki` に **題名そのもの**「補助金等交付規則」を投げると、
「福岡県補助金等交付規則」は **15 位**にしか出ない。1 位は「福岡県造林事業交付金交付規程」。
本文中で他の例規を参照しているだけの文書が、題名が完全一致する本則より上に来る。
収集の問題ではなく順位付けの問題だが、**探しても見つからない**という点では同じ被害になる。
題名の完全一致・前方一致に大きく重み付けする改修が要る。
