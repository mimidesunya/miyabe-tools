# 事務事業評価の事業ごとの索引（探す・集計する）

文書の全文検索（[事務事業評価の検索](hyoka-search.md)）とは別に、事業ごとの欄に分けてある記録を 1 事業 1 件で
OpenSearch に入れ、費用・方向性・評価・指標で絞り込み、金額を集計できるようにする。MCP から使う道具は
`search_hyoka_projects`・`aggregate_hyoka_amounts`・`get_hyoka_project`（[MCP連携](mcp.md)）。

全文検索で事業名を探すと、事業の一覧の文書が事業のシートより先に返ることがあり、本文も PDF の文字が崩れた順で
文書まるごとしか取れなかった。AI が「目標割れなのに費用が増えた事業」のような調べ物を MCP だけでできるように作った。

## 索引の作り方

- alias は `miyabe-hyoka-projects-current`（`MIYABE_HYOKA_PROJECTS_ALIAS`）、世代は `miyabe-hyoka-projects-v<build-id>`。
  文書検索の統合 alias `miyabe-documents-current` には入れない。
- 入力は画面用の控え `data/hyoka/records/<slug>/<doc_id>.json`（[画面](hyoka-display.md)、`tools/hyoka/build_display_data.py` が書く）。
  文書の年度・種別・題名と、検索結果の ID（頁で分けた文書は `:p<頁>`）は文書の索引 `miyabe-hyoka-current` から読む。
  自治体名と都道府県は `data/municipalities/municipality_master.tsv`。
- 作り方は `tools/search/hyoka_projects.py`、作り直しは `tools/search/build_hyoka_projects_index.py`。毎回全部を新しい世代に入れ、
  件数を確かめてから alias を切り替える。公開中より件数が半分未満に減るときは切り替えない（`--allow-shrink` で切り替える）。
  古い世代は今の世代のほかに `--keep-previous` 個（既定 1）を残して消す。
- 控えを作り直したら（自治体を足した、読み取りを直した）、この索引も作り直す。

```bash
# 手元で中身を確かめる（索引には入れない。文書の情報は取得の manifest から）
python tools/search/build_hyoka_projects_index.py --display-dir data/hyoka \
  --manifest-root work/hyoka --out /tmp/projects.ndjson
```

## 1 件に入れるもの

- 事業: `project_id`（`hyoka-project:<slug>:<doc_id>:<record_id>`）、事業名、事業番号、担当、目的、評価した年度
  （シートの評価年度、無ければ対象年度、無ければ掲載文書の年度）、評価区分（原典の記載のまま）、方向性、観点（「成果:c」の形）、
  記録の作り方（`program`・`ai`・`machine`）、原典の頁へのリンク、文書の ID。
- 方向性の区分 `direction`: 読み取りが凡例から決めた区分（控えの `direction_code`）、無ければ記載の括弧の名前（「Ⅱ（改善・見直し）」）、
  無ければ 16 字以下の短い記載の言葉。長い文は区分を決めず `other`（「縮小は考えていない」を縮小と読まない）。
- 金額の明細 `amounts`（nested）: 控えの金額・財源の表の値を 1 値 1 件にし、円にそろえる（千円・万円・百万円・億円。
  値の中に単位が書いてあればそちら）。単位の分からない値は円にしない。評価した年度より後の決算・見込は様式の空欄
  （川崎市は 0 と刷る）なので落とす。費目は `total`（総費用）・`direct`（事業費）・`personnel`（人件費）・`sub_project`・`breakdown`・
  `other` と、財源の `funding_*`。
- 評価した年度の費用 `cost_yen`: 総費用、無ければ事業費。段階は決算 → 見込 → 予算 → 計画。原典の値は `cost_raw`・`cost_unit`。
- 前年度からの増減 `cost_prev_yen`・`cost_change_ratio`（今年 ÷ 前年。どちらも集計に使う値で比べる）。
- 人工 `staff_fte`、指標ごとの評価した年度の目標・実績・比 `indicators`（実績 ÷ 目標）、成果指標の比の最小 `outcome_ratio_min`。
  少ない方が良い指標（未収金など）もあるので、比が 1 未満でも未達とは限らない。
- 指標 1 単位あたりの費用 `indicators[].cost_per_unit_yen`（評価した年度の費用 ÷ 実績。展覧会 1 回、参加者 1 人あたりなど）。
  実績が 0 以下、単位が %・点・倍などの割合のときは出さない。費用は事業全体なので、指標が事業の一部しか表さない場合は大きく出る。
- 同じ事業の履歴: 名前の並び `project_names`（年度の古い順、10 個まで）、評価に載った最初と最後の年度 `project_first_year`・`project_last_year`。
- 評価の文の使い回し `text_repeat_ratio`: 評価の文（実施結果・評価の理由・今後の方向性・課題の欄と、欄名に評価・理由・課題・方向・成果などを含む
  そのほかの欄）を文に分け、前の年度の同じ事業の文と同じ文の割合。12 字以上の文が 2 つ以上あるときだけ出す。目的・対象・根拠法令は比べない。
  川崎市の様式ごとの読み取りの控えには評価の理由の文が無いので、今は川崎市では出ない。
- 分析の印 `flags`（原典の値から機械で付けた印で、問題があると決めたものではない。絞り込みの `flag` は「すべてを持つ」）:

| 印 | 付ける条件 |
| --- | --- |
| `outcome_below_half` | 成果指標の比の最小が 0.5 以下 |
| `expand_below_target` | 成果指標が目標割れ（比 < 1）なのに方向性が拡充 |
| `cost_up_below_target` | 成果指標が目標割れなのに、費用が前年度の 1.1 倍以上 |
| `no_outcome_target` | 費用があり、指標を活動と成果に分ける様式なのに、目標の数のある成果指標が無い |
| `repeated_text` | `text_repeat_ratio` が 0.9 以上 |
| `renamed` | 同じ事業の名前が年度で違う |
| `not_in_latest` | その自治体の最新の評価の年度に載っていない（廃止・統合・評価の対象から外れたなど） |

川崎市（31,980 記録）では、令和7年度の評価で `no_outcome_target` 271、`renamed` 133、`cost_up_below_target` 25、`outcome_below_half` 20。
印と履歴は索引を作るときに計算しておくので、検索の速さは変わらない。

## 二重に数えない仕組み

同じ事業は年度ごとのシートに何度も出る（令和6年度のシートに令和6年度の見込、令和7年度のシートに令和6年度の決算）。
自治体ごとに次の順で 1 つを選び、`amounts.best` の印を付ける。集計（`stage=best`）はこの印の値だけを足す。

1. 同じ事業: 事業番号が同じ記録、無ければ名前が同じ記録をまとめる。番号が同じなら名前が変わっても同じ事業
   （川崎市の「特定疾患対策事業」→「難病対策事業」）。番号が振り直されても、違う年度の文書で名前が同じなら同じ事業。
   同じ年度の文書で名前が同じで番号の違う記録（別の部署の同名の事業）はまとめない。
2. 事業・年度・費目ごとに、総費用の行がある記録（事業ごとのシート）を先に、次に決算 → 見込 → 予算 → 計画、同じ段階なら
   新しい年度の文書。総括や中間評価の施策の表の行には、同じ事業名でもシートと意味の違う金額が入ることがある
   （川崎市の総括で、バス利用等促進事業のシートの 928 万円に対して 2.1 億円）。
3. 段階を指定した集計（`stage=settlement` など）のために、段階ごとにも 1 つ選んで `amounts.stage_best` を付ける。

川崎市（31,980 記録）で試した値: 令和6年度の総費用は 572 事業で 9,327.5 億円、事業費は 8,578.3 億円。
令和4〜7年度はどの年度も 572 事業で、同じ事業の重なりは無かった。

## API

- 探す: `/api/hyoka-projects.php`。`q` のほか `slug`・`municipality_code`・`pref_code`・`evaluation_year`・`department`・
  `direction`（カンマ区切り）・`rating`・`aspect`・`method`・`flag`（カンマ区切り、すべてを持つ）・`has_amounts`・`min_cost`・`max_cost`（円）・`below_target`・
  `max_outcome_ratio`・`min_cost_change_ratio`。並びは `relevance`・`cost_desc`・`cost_asc`・`cost_change_desc`・
  `outcome_ratio`・`year_desc`（既定は `q` があれば関連度、無ければ費用の大きい順）。金額の明細は返さない。
- 集計: `/api/hyoka-aggregate.php`。同じ条件に `year`（金額の年度、`group_by=year` 以外は必須。年度をまたいで足すと
  毎年の金額を足した意味の無い数になるため）、`from_year`・`to_year`、`scope`（既定 `total`）、`stage`（既定 `best`）、
  `group_by`（`none`・`municipality`・`prefecture`・`department`・`direction`・`year`）、`limit`、`top`。
  条件（方向性など）は金額を載せた記録の値で絞る。
- 1 件: `/api/hyoka-project.php?id=…`。索引の値（金額の明細つき）、控えの記録（原典の欄と表）、その事業の頁の表を
  文字にしたもの（`include_sheet`、`max_sheet_chars`。頁は控えの `pages`、無ければ `page`。最大 5 頁）。
- 仕様は `app/openapi.json`・`.yaml`。索引が無い・繋がらないときは 503。
- MCP の `get_hyoka_project` は `include_related: true` のときだけ、同じ自治体の会議録と例規集を事業名の語句で引いて 3 件ずつ添える
  （`/api/search` を 2 回足す。既定では引かないので遅くならない）。片方が失敗しても事業の応答は返す。

## 本番で回す手順（この変更では未実行）

1. 承認後にコードを配備し、MCP のイメージを作り直す（`docker compose up -d --build mcp`）。
2. 控えに方向性の区分 `direction_code` と事業の頁 `pages` を入れるため、`build_display_data.py` で控えを作り直す
   （前回と同じ引数。[画面](hyoka-display.md)）。
3. scraper の容器で索引を作る。控えは読むだけで重ねる。

```bash
cd ~/services/miyabe-tools
docker compose -p miyabe-tools-scraping -f docker-compose.scraping.yml run --rm --no-deps -T \
  -v "$PWD/data/hyoka:/workspace/data/hyoka:ro" \
  scraper-reiki-index python3 tools/search/build_hyoka_projects_index.py --display-dir /workspace/data/hyoka
```

4. `[HYOKA-PROJECTS]` の行で件数（`documents`・`with_cost`・`without_document_id`）を確かめ、MCP で
   `search_hyoka_projects`（川崎市・費用の大きい順）、`aggregate_hyoka_amounts`（川崎市・2024 年度・部署ごと）、
   `get_hyoka_project` を 1 回ずつ呼ぶ。

戻すときは alias を前の世代へ向け直す（前の世代は 1 つ残してある）。初回は前の世代が無いので、alias を消せば
3 つの API が 503 になるだけで、ほかの検索には影響しない。
