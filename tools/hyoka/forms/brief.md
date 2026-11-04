# 事務事業評価シートの読み取りプログラムを書く

## 目的

自治体の事務事業評価シートから、税金の使われ方を市民が検証するための値を取り出す。この団体の様式に合わせた読み取りプログラム `reader.py` を書く。プログラムは同じ様式の文書を全部、AI を使わずに読む。値はすべて原典の升目の番地付きで返し、検算で確かめる。

## ファイル

- `layouts/<doc_id>.json.gz`: 文書の頁の升目。`cells.load_document(path)` で読む。表の番号は 1 から、行と列は 0 から。番地は表の中が `p{頁}t{表}r{行}c{列}`、表の外の行が `p{頁}l{番号}`。
- `pdf/<doc_id>.pdf`: 原本。表の見た目を確かめたいときは PyMuPDF で頁を画像にして見る（`fitz.open(path)[頁-1].get_pixmap(dpi=110).save("x.png")`。作った画像は終わったら消す）。
- `cells.py`: 升目の道具。`find`、`find_all`、`right`、`below`、`left`、`above`、`row`、`column`、`table_cells`、`find_line`、`marked`（印の付いた選択肢）、`number`、`status`、`western_year`。升目に複数の行があれば `Cell.lines` に行ごとの文字がある（点線で区切った複数の指標が 1 つの升目に入る様式など）。行ごとの値の組は `Cell.line_values()`。
- `example_reader.py`: 別の自治体（川崎市）の読み取りの手本。書き方の参考にする。この団体の様式は違う。
- `survey.json`: この団体の公開状況の調査の記録。様式と年度（`forms`）、担当が開いて確かめた見本（`samples`）。
- `check.py`: 確かめる台本。`python check.py --layouts layouts --reader reader.py`。`--doc <doc_id>` で 1 文書だけ、`--show 20` で問題の例を多く見る。

## 記録の形

`read(document) -> list[dict]`。1 件が 1 事業の 1 回の評価。

- `pages`: その事業の頁番号の配列
- `evaluation_year`、`target_year`: 評価の年度と、評価の対象になった事業の実施年度（西暦）。書かれていなければ 0
- `project`: `name`、`name_budget`（予算上の事業名）、`code`、`department`（部、課、係）、`account`（会計、款、項、目）、`plan_alignment`（総合計画の位置づけ）、`legal_basis`、`target`（対象）、`purpose`（目的）、`period`（期間）。どれも値の組の配列 `[{"raw": 原文, "ref": 番地}]`。`Cell.value()` で作る。無ければ空の配列
- `amounts`: 金額の表の数字を 1 つずつ全部。`{label, fiscal_year, raw, ref, stage, scope, unit, unit_source}`
  - `stage`: budget（予算）、settlement（決算、実績）、estimate（見込）、plan（計画）、unstated（表に書かれていない。推測しない）
  - `scope`: direct（人件費を含まない事業費）、personnel（人件費）、total（人件費を含む総費用）、sub_project（細事業）、breakdown（費目などの内訳）
  - 「事業費」という語を信用しない。合計の式や欄の並びから、人件費が入るかを決める。人件費の欄が無ければ direct
  - `unit`: その表の見出し、欄名、表の外の「（単位：千円）」から読む。千円と決めつけない。`unit_source` に読んだ原文
  - 単位は金額の表ごとに決め、PDF の頁を画像にして必ず目で確かめる。「（千円）」「単位：千円」の形を優先し、注記や文中の「395円」（一人当たり負担額など）と混同しない。表の外の行には近くの注記の字が混ざることがある
  - 空欄は入れない。「－」は raw をそのまま入れる。平均給与、人件費単価は `staff` に入れる
- `funding`: `{label, fiscal_year, raw, ref, stage, source, unit}`。source は national、prefecture、bond、fund、user_fee、other_specific、general
- `staff`: 人数、人工、平均給与、人件費単価。`{label, fiscal_year, raw, ref, unit}`
- `indicators`: `{name, name_ref, kind_raw, kind, unit, note, values: [{fiscal_year, target_raw, actual_raw, rate_raw}]}`。kind は target（対象）、activity（活動）、outcome（成果）、unknown。表の書き方に従い、kind_raw に原文
- `evaluations`: 評価の段階ごとに 1 件。`{stage_raw, actor, status, rating_raw, direction_raw, direction, reason_refs, aspects}`
  - actor: 担当、課長、部長、行革担当、首長、外部、市民、議会、その他
  - status: done、blank（欄はあるが空）、partial
  - direction: expand（拡充）、continue（継続）、maintain（現状維持）、improve（改善、見直し）、reduce（縮小）、integrate（統合）、suspend（休止）、abolish（廃止）、complete（完了）、other、none
  - 選択肢は印（☑ ■ ● ◎）の付いたものだけを読む。□ ☐ は選ばれていない。印が 1 つも無い並びからは選ばない（none）
  - `reason_refs`: 理由の文章の番地。`aspects`: 必要性、有効性などの観点ごとの評価 `{aspect_raw, rating_raw}`
- `reflection`: 評価の結果を予算や事業に反映した記述。`{fiscal_year, kind, raw, unit, change_raw, ref}`。kind は increase、decrease、new、none、unknown

## 進め方

1. `survey.json` と文書をいくつか見て、年度ごとの様式の違いをつかむ。文書の多い年度の様式から仕上げる。
2. `reader.py` を書く。様式ごとに関数を分けてよい。欄名と表の並びで読む。文書ごとの値を書き込まない（事業名や番号で分岐しない）。
3. `check.py` を流す。番地の問題は 0 にする。検算が合わないものは、読み違い（列のずれ、単位、別の行、升目に 2 つの数字）を疑って直す。原典が合っていないなら直さず、notes.md に書く。
4. 金額の無い記録、記録の無い文書を見て、取りこぼしが無いか確かめる。シートでない文書（表紙、目次、一覧、概要）は記録を作らなくてよい。
5. 最後に `notes.md` を書く: 様式の種類と年度、取れた項目、取れない項目と理由、検算の結果、文書の種類（シートでない文書）。

## 守ること

- `reader.py` は `cells` と標準ライブラリ（re、decimal など）だけを使う。ネットワーク、ファイルの読み書きをしない。
- 推測で値を埋めない。原典に無い値は入れない。
- このフォルダの外を変えない。`check.py` と `cells.py` は変えない。
- 途中で質問しない。迷ったら notes.md に書いて進む。
