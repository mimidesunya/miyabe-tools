# 例規集ツール

自治体の例規集データを収集・解析するためのツール群です。
現在は川崎市の例規集に対応しています。

## 含まれるスクリプト

`tools/reiki/` ディレクトリに含まれています。

- `download_kawasaki.py`: 川崎市の例規集サイトから HTML データをダウンロードします。
- `parse_kawasaki.py`: ダウンロードした HTML データを解析し、利用しやすい形式（JSON など）に変換します。
- `classify_reiki_gemini.py`: Gemini を使って、1ファイル1法令（条例・規則・要綱・要項）を**政策アクション指向**で分類・評価します。  
  - 一次分類（A-F）  
  - 二次タグ（複数）  
  - 必要度スコア・必要度レベル  
  - AI総評・推奨アクション  
  - 名称の読み（かな）推定  
  - 所管部署（推定）

## 条例ビューア

- 画面: `/app/reiki/index.php`
- 対象: `data/reiki/kawasaki` 配下の `*_j.html`
- 機能:
  - ファイル一覧（ページング）
  - ファイル名/タイトル検索
  - HTML本文表示
  - `kawasaki_json/**/*.json` を参照して分類表示
  - 一覧はHTML由来のタイトルを表示

## Gemini 分類スクリプト

このスクリプトは、**本文・附則・改正履歴を含めた全文**で判定します。
処理対象は `download_kawasaki.py` の出力先である `data/reiki/kawasaki` 配下の `*_j.html` に固定です。

AIに渡す入力は、`data/reiki/kawasaki_md` に対応するMarkdownが存在する場合は **Markdownを優先** し、
見つからない場合のみHTML本文を使用します（`--markdown-dir` で変更可能）。

### 分類体系

- 一次分類（単一選択）
  - `A_法定必須_維持前提`
  - `B_自治体裁量だが基幹_効率化対象`
  - `C_裁量的サービス_縮小統合候補`
  - `D_理念宣言中心_実施見直し候補`
  - `E_規制許認可中心_規制緩和候補`
  - `F_手数料使用料連動_負担軽減候補`
- 二次タグ（複数選択）
  - `罰則あり` / `理念優位` / `上位法参照あり` / `手数料規定あり` / `KPI不明` / `重複疑い`

### 評価項目

- `necessityScore`（0-100）: 高いほど維持必要性が高い
- `necessityLevel`（高/中/低）
- `necessityLevelCode`（1-3）: 低=1 / 中=2 / 高=3
- `fiscalImpactScore`（0-5）: 見直し時の財政効果見込み
- `regulatoryBurdenScore`（0-5）: 規制・手続負担
- `policyEffectivenessScore`（0-5）: 実効性
- `recommendedAction`（維持/効率化/縮小統合/廃止検討/規制緩和/料金見直し/要精査）
- `recommendedActionCode`（1-7）: 推奨アクションの数値コード
- `primaryClassCode`（1-6）: 一次分類 A-F の数値コード
- タグの数値フラグ（0/1）
  - `tagPenalty` / `tagIdeology` / `tagUpperLawRef` / `tagFeeRule` / `tagKpiUnknown` / `tagDuplicate`
- `aiEvaluation`: AIの総合評価コメント
- `readingKana`: 法令名の読み（かな）
- `readingConfidence`（0-1）: 読み推定の確度
- `responsibleDepartment`: 所管部署（推定）
- `departmentConfidence`（0-1）: 所管部署推定の確度

本文から機械的に集計する定量指標（例規ごとのJSONに出力）:

- `charCount` / `lineCount`
- `articleCount`（条文見出しのユニーク件数）
- `penaltyTermCount`（罰則関連語出現数）
- `feeTermCount`（手数料・使用料関連語出現数）
- `upperLawRefCount`（上位法参照語出現数）
- `amendmentCount`（「改正」出現数）
- `supplementCount`（「附則」出現数）

- 既定動作はプレビューモード（APIを呼び出さない）
- 実際にAPIを呼び出すのは `--execute` 指定時のみ
- `--execute` 時は Gemini の **Batch API**（非同期バッチ）でまとめて分類します

### 事前準備

- `config.json` の `gemini.apiKey` と `gemini.textModel` を設定
- 必要に応じて `gemini.baseUrl` / `gemini.timeoutMs` / `gemini.maxRetries` を設定
- バッチ関連の任意設定
  - `gemini.batchChunkSize`（既定: 80）
  - `gemini.batchPollSec`（既定: 15秒）
  - `gemini.batchMaxWaitSec`（既定: 86400秒）
- 入力関連の任意指定
  - `--markdown-dir`（既定: `data/reiki/kawasaki_md`）

### 例: 対象確認のみ（未実行）

```bash
python tools/reiki/classify_reiki_gemini.py
```

### 例: 実際に分類実行

```bash
python tools/reiki/classify_reiki_gemini.py --execute
```

### 主な出力

- 例規ごとのJSON: `data/reiki/kawasaki_json/**/*.json`
  - 入力の `*_j.html` と同じ相対パス・同名で、拡張子のみ `.json`

`confidence` が低いもの（既定 0.70 未満）は `flags` に `needs_human_review` が付きます。

## 使い方

（詳細な使い方は各スクリプトのヘルプを参照してください）
