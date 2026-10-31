# 事務事業評価の画面（事業の一覧と取り込みの状態）

事務事業評価は、自治体の PDF から取り出した文字だけでは表の読み順が崩れて読みにくい。
[機械抽出](hyoka-extraction.md)で事業ごとの欄に分けた自治体の文書は、詳細画面に事業の一覧を出す。
取り込みの状態は、会議録・例規と違って実行状態の DB にまだつないでいないので、控えから件数を出す。

## 画面

- **文書の詳細**（`/search/detail/?id=hyoka:…&doc_type=hyoka`）: 事業の記録がある文書は、本文の前に
  「この文書の事業」を出す。見出しは事業名と、担当部署・年度・評価区分・今後の方向性・原典の頁の札。
  開くと標準の欄（[共通形式](hyoka-common-format.md)の 19 項目のうち値のあるもの）と、そのほかの欄を出す。
  欄名は原典のまま（標準の名前と違えば「原典の欄名」を添える）、各事業から原典の頁（`#page=N`）へ飛べる。
  頁で分けた詳細（`…:pN`）では、その頁の事業だけを出す。事業が 60 件を超える文書は見出しだけを先に出し、
  開いたときに `/api/hyoka-record.php?id=…&record=…` から中身（HTML の断片）を読む。
  記録の無い文書は、取り出した文字をそのまま出すことを断る。
- **収集・公開状況**（`/status/#hyoka`）: 自治体・文書・事業ごとの欄の 3 段の数と、都道府県別の自治体の表
  （取得の結果、文書、検索できる本数、OCR で読んだ・待ち・読めず、事業の記録、年度、掲載ページ）。
- **自治体一覧**（`/municipalities/`）: 事務事業評価の欄に「検索 N本」「事業 N件」の札。

## 控え（`data/hyoka`）

画面の容器からは取得の保存先（`/mnt/big/miyabe-tools/work/hyoka`）も抽出の出力先
（`…/work/hyoka-extracted`）も見えないので、`tools/hyoka/build_display_data.py` が要る分だけを縮めて置く。
入力は読むだけ。`data/hyoka` は配備の同期の対象外なので、配備で消えない。

- `status.json`: 自治体ごとの取得の結果（manifest の `last_run`）、文書・本文・OCR・本文の無い文書の数、
  年度の範囲、事業の記録の数、検索に載った本数（索引づくりのログの `[HYOKA]` 監査の `yielded`）。
  OCR は、試した回数が上限（`pdf_ocr.MAX_ATTEMPTS`）に届いて打ち切ったものを「読めず」、まだ試せるものを「待ち」と数える。
- `records/<slug>/<doc_id>.json`: 事業の記録を詳細画面に出す欄だけに縮めたもの。値は専用欄の原文だけで、
  文中の記載（embedded）や組み立てた値は出さない。欄の中の折り返し（セルの幅で切れた改行）だけは詰める
  （文の終わり「。」と箇条書きの頭「○・①」などの改行は残す）。原典の URL も持たせる。
  ZIP の中の文書の記録は、索引に詳細画面が無いので控えを作らない（状態の数には入れる）。
  抽出し直して無くなった文書の控えは消す。

取り込み（取得・OCR・抽出・索引づくり）のあとに流す。全量の rebuild のログのあとに update のログを古い順に渡せば、
後のもので自治体ごとに上書きする。保存先を付け忘れて空の入力で走らせると、控えを空で上書きせずに止まる。

```bash
cd ~/services/miyabe-tools
docker compose -p miyabe-tools-scraping -f docker-compose.scraping.yml run --rm --no-deps -T \
  -v /mnt/big/miyabe-tools/work/hyoka:/workspace/work/hyoka:ro \
  -v /mnt/big/miyabe-tools/work/hyoka-extracted:/workspace/work/hyoka-extracted:ro \
  scraper-reiki-index python3 tools/hyoka/build_display_data.py \
  --work-root work/hyoka --records-root work/hyoka-extracted --out data/hyoka \
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
