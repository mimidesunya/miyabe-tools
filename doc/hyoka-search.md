# 事務事業評価の全文検索（第1段）

取得済みの評価表を `doc_type=hyoka` として会議録・例規集と同じ検索に載せる。
AI、OCR、項目抽出は実行しない。保存済み本文を読み、検索用の通常の分かち書きを付ける。
公開検索の正本は OpenSearch alias。接続できないときは503で、SQLiteへ切り替えない。

## 収録するものと頁

`work/hyoka/<slug>/manifest.json` の現在版のうち `text_status=ok` の文書だけを読む。
`text/<doc_id>.txt.gz` 冒頭の取得器が付けた題名・年度・種別・出典・掲載ページの5行は
メタ情報として持ち、残りの本文を収録する。`_archive/` と `previous_versions` は読まない。
取得元から見つからなくなった `status=gone` も収録し、検索結果と詳細に印を出す。

原則は物理頁ごとに1件。ただし**現在のPDF取得器は非空頁を二重改行で結合しており、
既存の多頁PDFの本文から物理頁を確定できない**。段落から頁番号を推測せず、
この場合は文書全体を1件とする。原典の頁番号が不明なら `page=null`。

| `page_split_reason` | 単位 |
| --- | --- |
| `single_page_pdf` | manifestで全1頁のPDF。1頁として収録 |
| `form_feed` | 改頁文字 `\f` とmanifestの頁数が一致。空頁の番号を詰めず、非空頁を収録 |
| `saved_text_no_page_boundaries` | 多頁PDFに頁区切りがない。文書全体 |
| `page_boundary_count_mismatch` | 区切りの数と頁数が不一致。文書全体 |
| `page_count_unknown` | PDFの頁数が不明。文書全体 |
| `non_pdf_document` | Excel・Word等。文書全体 |

IDは `hyoka:<slug>:<doc_id>:p<物理頁>`、文書全体なら `hyoka:<slug>:<doc_id>`。
改訂で頁構成が変わるとIDが変わり得る。更新は自治体単位で旧IDを入れ替える。
`source_url` は取得時の原典URL、`source_page_url` は発見した掲載ページ、
`source_page_link` は頁が分かるPDFでは原典URLの `#page=N`、不明なら原典URL。
リンク先が差し替わっている可能性はあるため、保存版の `sha256` と `fetched_at` も返す。

自治体コード・slug・名称・都道府県、`fiscal_year`・`year_label`、`kind`、題名、
`link_text`・`heading`、原典情報、頁、状態を保持する。
年度はmanifestの掲載文書の年度であり、評価実施年度・実績対象年度とは断定しない。

## API・画面・MCP

- 画面: `/search/?doc_type=hyoka`。年度、種別、自治体、都道府県で絞れる。
- 検索: `/api/search?q=子育て&doc_type=hyoka&fiscal_year=2025&kind=project_evaluation`
- 本文: `/api/document?id=<検索結果のid>&doc_type=hyoka`。頁の結果ならその頁の本文。
- MCP: `search_hyoka` → `get_municipal_document`（`doc_type=hyoka`）。MCPに検索処理を複製しない。

`kind` は `project_evaluation` / `policy_evaluation` / `external_review` / `overview`。
`fiscal_year` は4桁の西暦年度で完全一致。不正な年度・種別は422。
自治体は既存の `slug` / `municipality_code`、都道府県は `pref_code` を使う。
`start_date` / `end_date` / `start_year` / `end_year` はhyokaには適用しない。
RESTの既定 `sort=date` はhyokaでは年度の新しい順（不明は末尾）、既存の近接優先を適用する。
MCP `search_hyoka` の既定は `relevance`。既存2種の既定値・応答項目は変えない。
hyoka固有項目はhyokaの結果だけに追加する。仕様は `app/openapi.json` / `.yaml` とAPI Guideを参照。

## 索引の設定と監査

```bash
python3 tools/search/build_opensearch_index.py --doc-type hyoka \
  --hyoka-root /path/to/work/hyoka --mode rebuild
```

読み取り元は `--hyoka-root`、または `MIYABE_HYOKA_ROOT`。既定はリポジトリの `work/hyoka`。
aliasは `--hyoka-alias` / `MIYABE_HYOKA_ALIAS`（既定 `miyabe-hyoka-current`）。
世代名は `miyabe-hyoka-v<build-id>`。統合alias `miyabe-documents-current` にも含める。
PHPと索引のalias設定を一致させる。

**`--doc-type all` は従来どおり minutes / reiki だけ**。既存workerにhyokaが載っていなくても
従来の更新を続けられるよう、hyokaの構築は明示指定とする。他種別の更新でhyoka aliasを外さない。

rebuildは新世代を作り、自治体ごとの投入完了時に部分公開、全体成功後に世代を切り替える。
`--no-switch-alias` なら部分公開も最終切替もしない。
updateは指定slugを検査してから現行世代で差し替える。空・破損の入力で旧文書を消さない。
resumeは部分公開aliasの完了slugを再利用する。候補索引の未完了slugを一度消して再投入し、
改訂前の頁IDを残さない。中断世代のalias filterも維持する。
全量rebuild/resumeでは、現行aliasの公開済みslugと保存先を照合し、自治体ディレクトリが
丸ごと欠けていたら書き込み前に止める（非公開作成・明示した部分公開は除く）。

各自治体の `[HYOKA]` JSONログに、原典数 `raw_total`、収録した原典数 `yielded`、
意図した除外 `intentional_excluded`、予定数 `expected_indexable`、
理由のない欠落 `unexplained_drop`、実際の検索件数 `indexed_documents`、
頁を分けた/分けられない理由 `page_split_reasons` を記録する。
進捗の概数は原典文書数なので、頁分割後の検索件数とは異なる。
`text_status!=ok` は意図した除外、okなのに本文・原典情報が欠ける場合は公開を止める。

## 本番で回す手順（この変更では未実行）

対象は **`/mnt/big/miyabe-tools/work/hyoka` を持つ本番取得ホスト**。
ホストの接続先は既存の運用設定を確認すること。この作業では本番接続先や資格情報を読んでいない。
コードの配置先は既存手順の `~/services/miyabe-tools`、容器は既存scraperイメージを使う
一時的な `scraper-reiki-index` サービスの `run`。通常workerを再起動せず、Celeryも起動しない。

会議録・例規集のmountは `deploy/scraping_stack.py` の `shared_data_dir` から組み立てるが、
hyoka取得時の保存先は上記の絶対パスだった。**既存index workerにそのままexecしない**。
必ずhyoka保存先を明示して読み取り専用で重ねる。ホスト側に存在することを先に確かめる。

承認後にコードとMCPのビルドを既存配備手順で反映し、次をホスト上で実行する。

```bash
cd ~/services/miyabe-tools
test -d /mnt/big/miyabe-tools/work/hyoka
find /mnt/big/miyabe-tools/work/hyoka -mindepth 2 -maxdepth 2 -name manifest.json | wc -l
mkdir -p work/hyoka-index-logs

# 既存サービスのOpenSearch接続設定を継承。hyokaの入力だけを追加mountする。
hyoka_index() {
  docker compose -p miyabe-tools-scraping -f docker-compose.scraping.yml \
    run --rm --no-deps -T \
    -v /mnt/big/miyabe-tools/work/hyoka:/workspace/work/hyoka:ro \
    scraper-reiki-index python3 tools/search/build_opensearch_index.py \
    --doc-type hyoka --hyoka-root /workspace/work/hyoka "$@"
}

# 実在するslugを選ぶ。まず1自治体を非公開の世代へ入れてログを確認。
hyoka_index --mode rebuild --slug '<対象slug>' --no-switch-alias \
  --build-id hyoka-check-YYYYMMDDHHMMSS > work/hyoka-index-logs/sample.log 2>&1

# 全量。正常に終わった自治体から公開が進むため、実行前にaliasの控えを残す。
hyoka_index --mode rebuild --build-id YYYYMMDDHHMMSS \
  > work/hyoka-index-logs/rebuild.log 2>&1

# 後日の自治体単位の更新
hyoka_index --mode update --slug '<対象slug>' \
  > work/hyoka-index-logs/update.log 2>&1

# 部分公開後の中断から再開。別の新世代を同時に作らない。
hyoka_index --mode resume --resume-index miyabe-hyoka-vYYYYMMDDHHMMSS \
  > work/hyoka-index-logs/resume.log 2>&1
```

標本・全量とも終了コードと `[HYOKA]` の監査を確認する。全自治体のmanifestが見えているか、
`text_status=ok` の数と監査数が合うか、`unexplained_drop=0` かを確認する。
全量の前は取得器を同時に走らせず、manifestと本文の改訂が途中で混ざらないようにする。
`--allow-partial-alias` / `--allow-empty-slug-delete` は通常使わない。

公開後はhyokaのalias件数、複数自治体・年度・種別の検索、結果IDの本文取得、原典頁リンクを確認する。
会議録・例規の検索も各1件確認する。実OpenSearchへの接続試験と全量の所要時間・容量は本番前確認に残る。

切替前に `miyabe-hyoka-current` と `miyabe-documents-current` のaliasメンバー・filterを控える。
問題があれば1回の `_aliases` でhyokaの参照を元に戻し、統合aliasのhyoka部分も同時に戻す。
他種別のメンバーとfilterは維持する。元の世代は検証が終わるまで削除しない。
updateは現行世代を変えるため、戻す必要がある運用では先にrebuildで旧世代を残す。

## 次の段との境界

- OCR待ちは今回対象外。`tools/hyoka/ocr_hyoka_docs.py`（NDLOCR-Lite、会議録の OCR と同じ部品）で
  本文にした自治体を、そのあと update する。OCR は取得器・全量の索引づくりと同時に走らせない
  （どちらも manifest を書く・読むため）。
  取得の容器で `python3 tools/hyoka/ocr_hyoka_docs.py --all`（数えるだけなら `--dry-run`）。
- 頁区切りを持たない既存PDFを頁単位へ移すには、別作業で物理頁を保つ再抽出が必要。
- 項目の記録は [共通形式](hyoka-common-format.md) と別作業の `tools/hyoka/extract/` が担当。
  この全文検索はそれに依存せず、事業名・費用・評価結果等を解釈して補わない。
- hyoka用Celery定期実行、OCR掃き取り、自治体マップの収集進捗表示はまだ接続していない。
  今回は明示CLI実行と既存の索引ログ・進捗補助を利用する。
