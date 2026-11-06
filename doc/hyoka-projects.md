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
- 人件費 `personnel_yen` と、費用に占める割合 `personnel_ratio`（評価した年度の費用と同じ年度・段階の人件費 ÷ 総費用。
  事業費しか無ければ 事業費 + 人件費 で割る）。人件費は自治体の見積もり（人工 × 単価）のことが多く、決算にも予算と同じ値が入る（川崎市）。
- 指標の推移（評価した年度まで）: 指標ごとに、目標と実績の両方がある年度の数 `indicators[].ratio_years`、年度の比のうち最良
  `indicators[].best_ratio`（比の出る年度が 2 つ以上のときだけ）、実績が正の最も古い年度 `first_year` とその実績 `first_actual`、
  行の年度の実績 ÷ 最初の年度の実績 `change_ratio`。事業の側には、成果指標の `best_ratio` の最小 `outcome_best_ratio_min`
  （1 未満なら、どの年度も目標に届かない成果指標がある）と、活動・成果指標の `change_ratio` の最小 `actual_change_min` を持つ。
- 指標 1 単位あたりの費用 `indicators[].cost_per_unit_yen`（評価した年度の費用 ÷ 実績。展覧会 1 回、参加者 1 人あたりなど）。
  実績が 0 以下、単位が %・点・倍などの割合のとき、単位が円・千円などの金額のとき、単位が空で名前に率・割合・満足度などを含むときは出さない。
  費用は事業全体なので、指標が事業の一部しか表さない場合は大きく出る。
- 参加者・利用者など 1 人あたりの費用 `cost_per_person_yen`: 名前に参加・来場・入館・受講・利用者などを含み、単位が人・名・者
  （単位が空なら名前に人・者・名を含む）の指標（対象の指標は除く）の実績のうち、いちばん多いもので費用を割る（いちばん控えめな値）。
  待機児童数・配置数・定員のような、事業が働きかけた人でない人の数は入れない。
- 同じ事業の履歴: 名前の並び `project_names`（年度の古い順、10 個まで）、評価に載った最初と最後の年度 `project_first_year`・`project_last_year`。
- 評価の文の使い回し `text_repeat_ratio`: 評価の文（実施結果・評価の理由・今後の方向性・課題の欄と、欄名に評価・理由・課題・方向・成果などを含む
  そのほかの欄）を文に分け、前の年度の同じ事業の文と同じ文の割合。12 字以上の文が 2 つ以上あるときだけ出す。目的・対象・根拠法令は比べない。
  川崎市の様式ごとの読み取りの控えには評価の理由の文が無いので、今は川崎市では出ない。
- [形 2.0](hyoka-format-2.md) の項目（控えにあるときだけ。今は川崎市）: 体系の道筋 `policy_path`（上の段から、段の呼び名・番号・名前・目標）と、
  絞り込み用の `policy_names`（「番号 名前」の並び）、いちばん上の段 `policy_top`・いちばん下の段 `policy_leaf`。事業の階層 `level`
  （原典の呼び名）、区や対象で分けた事業のもとの名前 `variant_base` と分けの名前 `variant_label`、体系図で主な事業の印 `is_main`。
  そろえた評価 `achievement`（`above`・`on_target`・`below`）と `overall`（`high`・`middle`・`low`）は、最後の評価の段階のもの。
  文章の項目・評価の理由・そのほかの欄の文は `search_text` に入れ、語で探せるようにする。金額の明細にも `policy_top`・`policy_leaf`・
  `variant_base` を写す（値が無ければ欄ごと置かない。集計で空の組を作らないため）。
- 分析の印 `flags`（原典の値から機械で付けた印で、問題があると決めたものではない。絞り込みの `flag` は「すべてを持つ」）:

| 印 | 付ける条件 |
| --- | --- |
| `outcome_below_half` | 成果指標の比の最小が 0.5 以下 |
| `expand_below_target` | 成果指標が目標割れ（比 < 1）なのに方向性が拡充 |
| `cost_up_below_target` | 成果指標が目標割れなのに、費用が前年度の 1.1 倍以上 |
| `no_outcome_target` | 費用があり、指標を活動と成果に分ける様式なのに、目標の数のある成果指標が無い。指標が 1 本も無いシート（総費用の行がある記録）も、同じ自治体のほかの記録が指標を分けていれば付ける |
| `repeated_text` | `text_repeat_ratio` が 0.9 以上 |
| `renamed` | 同じ事業の名前が年度で違う |
| `not_in_latest` | その自治体の最新の評価の年度に載っていない（廃止・統合・評価の対象から外れたなど） |
| `below_target_every_year` | `outcome_best_ratio_min` が 1 未満（比の出る年度が 2 つ以上ある成果指標が、どの年度も目標に届いていない） |
| `actual_halved` | `actual_change_min` が 0.5 未満（活動・成果指標の実績が最初の年度の半分未満に減った。工事の箇所数や少ない方が良い指標も入る） |
| `blank_sheet` | 費用のあるシートなのに、目的・内容・実績・評価の文も指標も無い（事業名・番号・担当・年度・実施期間と、組織コード・SDGs のゴールなどのそのほかの欄だけ） |

川崎市（31,980 記録）では、令和7年度の評価で `no_outcome_target` 348（うち指標が 1 本も無いシート 77）、`renamed` 133、
`below_target_every_year` 78、`actual_halved` 31、`cost_up_below_target` 25、`outcome_below_half` 20、`blank_sheet` 7（地域課題対応事業の 7 区）。
令和7年度の評価の 572 事業のうち、`personnel_ratio` は全事業、`cost_per_person_yen` は 74 事業で出る。
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
  `policy`（体系の道筋のどこかの名前・番号の一部）・`direction`（カンマ区切り）・`rating`・`aspect`・`achievement`・`overall`（カンマ区切り）・
  `method`・`flag`（カンマ区切り、すべてを持つ）・`is_main`・`has_amounts`・`min_cost`・`max_cost`（円）・`below_target`・
  `max_outcome_ratio`・`min_cost_change_ratio`・`max_outcome_best_ratio`（`outcome_best_ratio_min` の上限）・
  `max_actual_change_ratio`（`actual_change_min` の上限）・`min_personnel_ratio`（0〜1）・`min_cost_per_person`（円）。
  並びは `relevance`・`cost_desc`・`cost_asc`・`cost_change_desc`・`outcome_ratio`・`year_desc`・`personnel_ratio_desc`・
  `cost_per_person_desc`（既定は `q` があれば関連度、無ければ費用の大きい順）。金額の明細は返さない。
- 集計: `/api/hyoka-aggregate.php`。同じ条件に `year`（金額の年度、`group_by=year` 以外は必須。年度をまたいで足すと
  毎年の金額を足した意味の無い数になるため）、`from_year`・`to_year`、`scope`（既定 `total`）、`stage`（既定 `best`。
  段階だけを足すなら `settlement`・`estimate`・`revised_budget`・`budget`・`initial_budget`・`plan`）、
  `group_by`（`none`・`municipality`・`prefecture`・`department`・`direction`・`year`・`policy_top`・`policy_leaf`・`variant`）、`limit`、`top`。
  体系や分けの分からない事業の金額は内訳に入らず、`other_groups_yen` に入る。
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

推移・人件費の割合・1 人あたりの費用・空欄の印を足したとき（`schema_version` 2）は、控えの作り直しは要らない。
コードを配備して MCP のイメージを作り直し、手順 3・4 で索引を作り直す。新しい欄の無い前の世代の索引でも、API は条件に当たる事業が無いだけで動く。

戻すときは alias を前の世代へ向け直す（前の世代は 1 つ残してある）。初回は前の世代が無いので、alias を消せば
3 つの API が 503 になるだけで、ほかの検索には影響しない。
