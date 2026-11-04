# 事務事業評価の AI による書き写し

[機械抽出](hyoka-extraction.md)（自治体・様式ごとの定義）で構造化できているのは 432 自治体のうち 38 だけで、
残りの文書は様式がまちまちで定義を手で書いていては広がらない。そこで、構造化していない自治体の
事業評価の本体（文字の入った PDF）を AI に読ませ、事業ごとの欄を**書き写させる**。
入口は `tools/hyoka/ai/run_ai.py`、容器は `docker/hyoka-ai/`。

## 方針

サイトは事業の良し悪しを AI で評価しない（[共通形式](hyoka-common-format.md)の方針）。AI には原典の欄の文字を
そのまま書き写させるだけで、要約・言い換え・評価はさせない。そのうえで次を守る。

- **原典との機械の照合**: 書き写した値は、その回に渡した升目の文字（空白・印・升目の番号を除いたもの）と照らし、
  空白で区切った各部分が原典に無ければ画面に出さない（記録の `unverified` に残す）。言い換えや作り話を載せないため。
- **専用欄の決まり**: 方向性を写した欄の名前が評価の欄（「総合評価」「判定」など）なら、共通形式の決まり
  （評価区分が方針の意味を兼ねるだけなら方向性は文中の記載）に従い、方向性の専用欄としては出さない。
- **出どころを明示**: 記録は `layout: ai_transcription`、`extraction` に型・指示文の版・升目の版・凡例の有無を持ち、
  `human_reviewed: false`。詳細画面と検索結果の一覧には「AI が書き写し、原典の文字と機械で照合した記録」と断る。
- 機械抽出の記録がある自治体は、そちらを使う（[画面の控え](hyoka-display.md)で機械の記録を優先）。

## なぜ AI か、どの型か（2026-10-31〜11-01 の検証）

抜き取り 56 文書で、AI を使わない汎用の取り出し（表＋欄名の辞書）で誤りなく取れたのは記録のある 42 文書中 3（7%）。
表の升目を文字のまま AI に渡せば、必須 3 項目（事業名・担当部署・今後の方向性）が文字として読めるのは 37（88%）。

文字の入った 49 文書（316 頁）で 4 つの型を比べた（同じ升目・同じ指示文、費用は通常の料金）。

| 型 | 費用 | 結果 |
|---|---|---|
| gpt-6-luna | $0.084 | 方向性の当て推量が最も少ない。事業の取りこぼしは 1 文書 |
| Claude Haiku 5.5 | $0.115 | 別の欄の語を方向性に入れる誤りが 2 文書 |
| Gemini 3.1 Flash-Lite | $0.207 | 印が読めないときに選択肢から当て推量。文字化けをそのまま写した |
| Claude Sonnet 5.5 | $1.807 | 2 文書を記録でないと見誤り 48 事業を落とした |

書き写した値が原典にある割合は 97〜100%。gpt-6-luna を使う。

## 流れ

どの段も途中で止めて同じ命令で続きから流せる。出力は `--out`（既定 `work/hyoka-ai`）の下だけに書く。

| 段 | すること | 出力 |
|---|---|---|
| `plan` | 構造化していない自治体の、`kind: project_evaluation`・文字の入った PDF（OCR でないもの）を選ぶ | `plan.json` |
| `grids` | PDF の各頁を升目の一覧と表の構造にする（並行）。表の構造が無い文書は作り直す | `<slug>/inputs/<doc_id>.json.gz`、`<slug>/layouts/<doc_id>.json.gz` |
| `legends` | 自治体ごとに凡例を作り、凡例あり・なしを比べて使うか決める | `<slug>/legend.json` |
| `requests` | 一括処理（Batch API）の要求ファイルを作る | `requests/part-NNNN.jsonl` |
| `submit` | 要求ファイルを送って一括処理を作る | `batches.json` |
| `collect` | 一括処理の状態を見て、終わった結果を受け取る（期限切れでも終わった分は受け取る） | `<slug>/results/<doc_id>.json` |
| `sync` | 一括処理を使わずに 1 回ずつ送る。小分けの試しと、失敗した区切りのやり直し | 同上 |
| `assemble` | 結果を事業の記録にする | `<slug>/records.jsonl`・`documents.json`・`summary.json` |
| `status` | 進み具合 | — |

### 升目の一覧（`grid.py`）

PDF の字を 1 字ずつ座標つきで集め、PyMuPDF の表の検出（罫線）で見つけた升目に振り分ける。
表は `lines_strict`（Excel 由来の塗りの縁を罫線と見ない）を使い、取りこぼしが 1 割を超えるときだけ `lines`。
升目は `行,列[+行の幅x列の幅]: 文字`、表の外の文字は `[上からの位置,左からの位置] 文字`（0〜99）。

- 図形・画像の印を字にする（塗りの丸 ●、線の丸 ○、塗りの四角 ■、線の四角 □、文字を囲む楕円 ◯）。
  同じ大きさの小さな画像が何種類かあり、1 つだけ少なく使われ黒い点が 1 割以上多ければ ☑、ほかは ☐
  （空欄の画像を使い回し、選んだ欄だけ別の画像の様式）。
- 印だけの升目（印が選択肢と別の升目に入る様式。抜き取りの 11%）は、同じ高さで右の最も近い字の前へ付け直す。
  ただし同じ列の上に見出しがあるとき（宇土市のように印の列が意味を持つ表）と、右隣が「－」のときは動かさない。
- 頁に回転の指定があれば見た目の向きに直してから読む（三島市など。直さないと字が逆に並ぶ）。
- 太く見せるために同じ字を重ねた PDF は 1 字にする（足立区など。直さないと「事事務務」になる）。
- 升目の中の行は、右端まで届いていれば折り返しとみなして空白なしでつなぐ（「作業効率 化」→「作業効率化」）。
- 制御文字や意味のない英字ばかりの頁（字の対応表が壊れた PDF）は、文字を出さずにそのことだけを書く。
- 頁は 6 頁ずつ（文字が多ければ少なく）に区切って 1 回で送る。区切りをまたいだ同じ事業は組み立てでまとめる。

### 指示文と凡例（`prompts.py`）

書き写しの指示文は、試しの食い違い（印の当て推量、別の欄の語、施策評価と見て事業を落とす、文字化けの書き写し）を
受けて直した版。答えは JSON の形で縛り、各値の原典の欄名（`labels`）も答えさせる。

凡例は、自治体ごとに見本の文書（新しい 1 本と古い 1 本。記録らしい頁と方向性の欄のある頁）の升目と頁の画像を
gpt-6.1-sol に見せて作らせる「様式の読み方」の説明と、方向性・評価区分の選択肢。書き写しの定義を凡例づくりにも渡す
（渡さなかった試しでは、凡例が「評価結果は方向性ではない」と書いて悪化した）。

凡例は様式の読み方をそろえる力が強い反面、凡例の解釈が誤っていればその自治体の全文書に誤りが広がる。
そこで見本以外の文書を最大 3 本、凡例あり・なしで読ませ、**事業の数（9 割未満）・方向性の埋まり方（5 ポイント）・
原典との照合（1 ポイント）が下がらず、方向性の書き方の種類が増えない（選択肢どおりの割合が上がるなら可）とき
だけ**使う。6 自治体の試しでは、選択肢から選ぶ様式でそろう効果がはっきりし（富田林市で選択肢どおり 0%→100%）、
評価が多段の様式（久山町）は悪化を見つけて使わなかった。

## 記録の形

共通形式 1.2.0 の記録のうち、画面に要る欄に絞ったもの。値は原典の文字。

```json
{
  "schema_version": "1.2.0",
  "record_id": "ai-<doc_id>-p0003-01",
  "record_status": "ai_transcribed",
  "layout": "ai_transcription",
  "source_document_ids": ["<doc_id>"],
  "record_evidence": [{"doc_id": "<doc_id>", "locator": {"type": "pdf", "page": 3}}],
  "fields": {
    "project_name": {"status": "present", "field_kind": "dedicated", "value": "…", "raw_text": "…", "raw_label": "事務事業名", "verified": true},
    "department": {…}, "future_direction": {…}, "evaluation_rating": {…}, "purpose": {…}
  },
  "raw_fields": [{"label": "年度の記載", "standard_key": "year_text", "content": {…}}],
  "unverified": {"purpose": "原典に無かった値"},
  "extraction": {"method": "ai_transcription", "model": "gpt-6-luna", "prompt_version": "extract-v2.1",
                 "legend_version": "legend-v2", "grid_version": "g3", "human_reviewed": false}
}
```

方向性は原語のまま（`mapping_status: unmapped`）。年度は評価年度か対象年度か分けられないので、原文の欄に残す。

## 本番での流し方

PyMuPDF（AGPL-3.0）が要るので、取得の容器とは別の容器で動かす。取得の保存先は読むだけ。
キーは `data/config.json` の `openai.apiKey`。

```bash
cd ~/services/miyabe-tools
docker build -t miyabe-tools-hyoka-ai docker/hyoka-ai
mkdir -p /mnt/big/miyabe-tools/work/hyoka-ai
AI="docker run --rm -u $(id -u):$(id -g) -v $PWD:/workspace \
  -v /mnt/big/miyabe-tools/work/hyoka:/workspace/work/hyoka:ro \
  -v /mnt/big/miyabe-tools/work/hyoka-extracted:/workspace/work/hyoka-extracted:ro \
  -v /mnt/big/miyabe-tools/work/hyoka-ai:/workspace/work/hyoka-ai \
  -w /workspace miyabe-tools-hyoka-ai python3 tools/hyoka/ai/run_ai.py"
$AI plan
$AI grids --workers 12
$AI legends --workers 8
$AI requests && $AI submit
$AI collect          # 終わるまで繰り返す（一括処理は 24 時間以内）
$AI sync --workers 16   # 失敗した区切りだけやり直す
$AI assemble
```

そのあと [画面の控え](hyoka-display.md) を作り直す（`work/hyoka-ai` も見せる）。

### OpenAI のクレジットが切れたとき

残高が尽きると、一括処理は要求ごとに失敗して `collect` がエラーの区切りとして受け取る。失敗した一括処理でも、終わった分は受け取る。
エラーの区切りだけを Claude Haiku 5.5（比べた型で次に良かったもの）へ送り直す。単価は gpt-6-luna の通常料金と同じで、
一括処理の半額はきかない。キーは `data/config.json` の `claude.apiKey`。

```bash
docker build -t miyabe-tools-hyoka-ai docker/hyoka-ai   # anthropic を入れる
$AI sync --workers 8 --model claude-haiku-5-5
$AI assemble
```

記録の `extraction.model` には区切りごとに実際に使った型が残り、詳細画面の断り書きもそれに合わせて出る。
凡例づくり（`legends`）と一括処理（`requests`・`submit`・`collect`）は OpenAI だけに対応している。

2026-11-01 の対象: 317 自治体、文書約 6 万本・約 67 万頁。升目づくりは 1 頁約 0.4 秒。
費用の見込みは一括処理（半額: 100 万トークンあたり入力 $0.05・出力 $0.25）で約 $90、凡例と比べで約 $15。
gpt-6-luna の一括処理は 1 日 150 億トークンまで積める（組織の段階による）。

## 試験

```bash
python -m unittest tools/hyoka/test_ai_extract.py
```

升目づくりの試験は PyMuPDF があるときだけ動く（試験の中で PDF を作って、印の付け直し・折り返し・表の外の位置を確かめる）。

## まだしていないこと

- OCR で読んだ文書（文字層の無い PDF。2.4 万頁）。升目が無いので、OCR の文字を渡す形を別に試す。
- 施策評価・外部評価・概要の文書。事業の記録ではないので対象外。
- 評価が多段（一次・二次・外部）の様式で、段ごとに記録を分けること。いまは 1 事業 1 記録（最終の段階）。
- 方向性の共通の区分への読み替え、番号で記入する様式の番号と選択肢の対応（凡例の選択肢を使って後の段で行う）。
- 凡例を様式ごと（同じ自治体の年度違いの様式）に分けること。いまは 1 自治体 1 凡例。
- 人による確認。
