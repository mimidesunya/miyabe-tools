# 事務事業評価の画面（事業の一覧と取り込みの状態）

事務事業評価は、自治体の PDF から取り出した文字だけでは表の読み順が崩れて読みにくい。
[機械抽出](hyoka-extraction.md)で事業ごとの欄に分けた自治体の文書と、[AI が書き写した](hyoka-ai-extraction.md)
自治体の文書は、詳細画面に事業の一覧を出す。機械抽出の記録がある自治体はそちらを使う。
取り込みの状態は、会議録・例規と違って実行状態の DB にまだつないでいないので、控えから件数を出す。

## 画面

- **文書の詳細**（`/search/detail/?id=hyoka:…&doc_type=hyoka`）: 事業の記録がある文書は、本文の前に
  「この文書の事業」を出す。見出しは事業名と、担当部署・年度・評価区分・今後の方向性・原典の頁の札。
  開くと標準の欄（[共通形式](hyoka-common-format.md)の 19 項目のうち値のあるもの）と、そのほかの欄を出す。
  欄名は原典のまま（標準の名前と違えば「原典の欄名」を添える）、各事業から原典の頁（`#page=N`）へ飛べる。
  頁で分けた詳細（`…:pN`）では、その頁の事業だけを出す。事業が 60 件を超える文書は見出しだけを先に出し、
  開いたときに `/api/hyoka-record.php?id=…&record=…` から中身（HTML の断片）を読む。
  記録の無い文書は、取り出した文字をそのまま出すことを断る。断り書きは、機械抽出の記録なら「機械で書き写した」、
  AI の記録（`layout: ai_transcription`）なら「AI が書き写し、原典の文字と機械で照合した」。
- **検索結果の一覧**（`/search/?doc_type=hyoka`）: 事業の記録がある文書は、結果ごとにその頁（頁区切りが無ければ
  文書全体）の事業を表で出す（事業名・担当・評価・今後の方向性）。検索語を含む事業を先に最大 5 件、残りの数も添え、
  各行から詳細画面のその事業（`#record-…`）へ飛べる。中身は `/api/search` の `records`・`records_total` で、
  MCP の `search_hyoka` にも同じものが入る。事業ごとの `method`（`machine`・`ai`）で書き写し方を示し、
  一覧の断り書きも分ける。記録の無い文書は抜粋だけ（表の升目ごとの改行は詰める）。
- **収集・公開状況**（`/status/#hyoka`）: 自治体・文書・事業ごとの欄の 3 段の数と、都道府県別の自治体の表
  （取得の結果、文書、検索できる本数、OCR で読んだ・待ち・読めず、事業の記録、年度、掲載ページ）。
- **自治体一覧**（`/municipalities/`）: 事務事業評価の欄に「検索 N本」「事業 N件」の札。「検索 N本」と
  収集・公開状況の自治体名は、その自治体の文書を検索語なしで新しい順に並べた検索結果へ飛ぶ。

## 控え（`data/hyoka`）

画面の容器からは取得の保存先（`/mnt/big/miyabe-tools/work/hyoka`）も抽出の出力先
（`…/work/hyoka-extracted`）も見えないので、`tools/hyoka/build_display_data.py` が要る分だけを縮めて置く。
入力は読むだけ。`data/hyoka` は配備の同期の対象外なので、配備で消えない。

- `status.json`: 自治体ごとの取得の結果（manifest の `last_run`）、文書・本文・OCR・本文の無い文書の数、
  年度の範囲、事業の記録の数、検索に載った本数（索引づくりのログの `[HYOKA]` 監査の `yielded`）。
  OCR は、試した回数が上限（`pdf_ocr.MAX_ATTEMPTS`）に届いて打ち切ったものを「読めず」、まだ試せるものを「待ち」と数える。
- `sheets/<slug>/<doc_id>.json.gz`: シートの全部。頁の表の形（[形 2.0](hyoka-format-2.md) の全欄の層）を、
  `work/hyoka-ai/<slug>/layouts/` から写したもの。AI の置き場が見えないときは今ある控えを消さない。
  升目どうしが重なることがある（川崎市の「事業の概要」の帯は表全体を囲む枠の大きさで読み取られる）ので、
  画面は小さい升目から番地に置き、大きい升目は空いている所まで縮め、空いた番地は空の升目で埋める
  （`miyabe_hyoka_display_sheet_grid`）。
- `records/<slug>/<doc_id>.json`: 事業の記録を詳細画面に出す欄だけに縮めたもの。値は専用欄の原文だけで、
  文中の記載（embedded）や組み立てた値は出さない。欄の中の折り返し（セルの幅で切れた改行）だけは詰める
  （文の終わり「。」と箇条書きの頭「○・①」などの改行は残す）。原典の URL も持たせる。
  ZIP の中の文書の記録は、索引に詳細画面が無いので控えを作らない（状態の数には入れる）。
  抽出し直して無くなった文書の控えは消す。自治体ごとに、機械抽出の記録（`--records-root`）があればそれを、
  無いか空なら AI の記録（`--ai-records-root`、既定 `work/hyoka-ai`。置き場が無ければ使わない）を使い、
  `status.json` の `records_source`（`machine`・`ai`）と合計の `records_ai` に残す。

調査から集め直した自治体（`work/hyoka2`、[調査](hyoka-survey.md)）は、`--overlay-slug` で 1 つずつ公開する。その自治体だけ
状態を `--overlay-root` から作り、記録は[様式ごとの読み取り](hyoka-forms.md)の `records.jsonl`（`--overlay-records-root`）を、
シートの全部は `--overlay-layouts-root` を使う。控えの記録は `schema_version: 2.0-form`、事業ごとに `layout: form_reader` で、
標準の欄（一覧の札）に加えて金額、財源、人員を年度と段階の表（`amounts`、`funding`、`staff`）、指標、評価の段階、
検算の結果（`checks`）を持つ。詳細画面はこれを表で出し、断り書きを「様式に合わせた読み取りプログラムで書き写した」にする。
金額と財源の表は、行ごとに原文の `values` と円にそろえた `yen` を持ち、画面は「10億7,865万円」の形で出す（原典の値は升目の
title。[形 2.0](hyoka-format-2.md) の金額）。何頁にもわたる事業は頁の一覧 `pages`、評価には方向性の区分 `direction_code` も持つ。
形 2.0 の項目が読み取りの記録にあれば、体系の道筋 `policy_path`、事業の階層 `level`・親 `parent`・分け `variant`・主な事業 `is_main`、
文章の項目 `texts`（全文）、そのほかの欄 `extra_fields`（区画の見出し・欄名・値）、評価ごとのそろえた段階 `achievement`・`overall`・
`aspect_levels`（段階の名前と原典の記載・凡例の選択肢）と理由 `reasons` も持つ。詳細画面は「体系の道筋」「事業の説明」の見出しで出し、
そろえた段階は評価の段階の欄に原典の記載を添えて、そのほかの欄は 1.2.0 の `raw_fields` と同じ見出しにまとめる。
控えを作るとき（`write_form_records`）は記録を 2 回読み、体系の道筋を同じ自治体のほかの記録で補う。番号の無い段（体系図の無い
文書でシートの欄から読んだ段）は、同じ年度に同じ呼び名で名前の骨組みが同じ段の番号を取る（`code_from_doc`）。上の段の無い道筋
（体系図が分冊の前の方にだけある文書）には、同じ年度に番号の親（「1-4-4」なら「1-4」）が分かる記録の道筋を前に足し、
足した段に出どころの文書 `from_doc` を付ける（画面は「同じ年度のほかの文書の体系図から補った段」と添える）。段の名前は、
同じ段・同じ番号で骨組み（仮名と記号を除いた字）が同じ名前のうち字の多いものにそろえる（体系図の文字の層の仮名の抜けを直す。原文は `source` に残る）。
川崎市だけ書き直すときは、控え全体を作り直さずに `write_form_records` を overlay の記録から呼べばよい（2026-11-05 の作業）。

取り込み（取得・OCR・抽出・索引づくり）のあとに流す。全量の rebuild のログのあとに update のログを古い順に渡せば、
後のもので自治体ごとに上書きする。保存先を付け忘れて空の入力で走らせると、控えを空で上書きせずに止まる。

```bash
cd ~/services/miyabe-tools
docker compose -p miyabe-tools-scraping -f docker-compose.scraping.yml run --rm --no-deps -T \
  -v /mnt/big/miyabe-tools/work/hyoka:/workspace/work/hyoka:ro \
  -v /mnt/big/miyabe-tools/work/hyoka-extracted:/workspace/work/hyoka-extracted:ro \
  -v /mnt/big/miyabe-tools/work/hyoka-ai:/workspace/work/hyoka-ai:ro \
  scraper-reiki-index python3 tools/hyoka/build_display_data.py \
  --work-root work/hyoka --records-root work/hyoka-extracted --ai-records-root work/hyoka-ai --out data/hyoka \
  --index-log work/hyoka-index-logs/rebuild-YYYYMMDDHHMMSS.log
```

2026-10-31 の全量（索引 `miyabe-hyoka-v20261031071938`）で試した結果: 432 自治体、文書 125,101、
検索できる 117,149（409 自治体）、OCR で読んだ 3,168、事業の記録 52,127（38 自治体、控えは 4,374 文書・77MB）。
いちばん大きい控えは島根県の 1 文書 743 件・4.1MB。

## 試験

```bash
python -m unittest tools/hyoka/test_build_display_data.py
php tests/hyoka_display_test.php
```

## まだしていないこと

- 取り込みの「いま動いている処理」の表示（取得・OCR・索引づくりの実行状態を DB へ書く）。定期の取り込みにつなぐときに。
- 自治体マップ（トップの地図）の自治体ごとの状態。
- 事業の記録での横断検索（部署・方向性で絞る）。いまは文書の詳細の中での絞り込みだけ。
- 取得した PDF の写しをこちらで配ること。原典は自治体のサイトへのリンクだけ。
