# 仮想開発チーム

この文書は、Miyabe Tools を AI エージェントと人間で共同開発するときの役割分担です。
参考案の大きなチーム構成を、このリポジトリの実体に合わせて小さく運用できる形に整理します。

## 目的

Miyabe Tools は、独立した選挙ポスター掲示場支援と、会議録・例規集の収集・検索・公開 API・MCP 連携を同居させたツール群です。
特に `tools.miya.be` の中核は、自治体文書を集めることだけではなく、人間と AI が同じデータを検索し、全文確認し、原典へ戻れる状態を保つことです。

チーム全体の優先順位は次の順です。

1. 原典 URL と取得元情報を失わない。
2. 会議録 `minutes` と例規集 `reiki` を混同しない。
3. `/api/search` と `/api/document` の互換性を守る。
4. OpenSearch alias を正として、SQLite FTS へ戻さない。
5. MCP は検索ロジックを持たず、既存 API を薄く呼ぶ。
6. クロールとインデックス更新を再実行可能にする。
7. AI 向け出力では、抜粋だけで断定させず全文と原典確認へ誘導する。

## チーム構成

| 役割 | 主な担当 | 主なコード/文書 |
| --- | --- | --- |
| Codex Orchestrator | タスク受付、影響範囲確認、実装統合、最終確認 | `AGENTS.md`, `README.md`, `doc/` |
| Product & Data Steward | 公開機能、自治体マスタ、データ意味論、公開可否判断 | `data/municipalities/`, `doc/municipality-master.md`, `doc/multi-municipality.md` |
| Election Poster Boards Agent | 掲示場マップ、進捗、LINE認証、掲示場SQLite | `domains/election_poster_boards/`, `app/boards/`, `app/line/`, `data/boards/` |
| Crawler Agent | 会議録・例規集の収集、差分取得、取得元保持 | `tools/gijiroku/`, `tools/reiki/`, `work/`, `data/reiki/` |
| ETL & Normalization Agent | slug、自治体コード、日付、本文、重複、文字化けの整理 | `tools/municipality_slugs.py`, `tools/search/scraped_source_records.py`, `lib/municipalities.php` |
| Search Index Agent | OpenSearch mapping、index rebuild/update、alias 切替、検索品質 | `tools/search/`, `lib/opensearch_search.php`, `app/search/` |
| API & OpenAPI Agent | `/api/search`, `/api/document`, スキーマ、後方互換性 | `app/api/`, `app/openapi.json`, `app/openapi.yaml`, `app/api-guide/` |
| MCP Integration Agent | MCP endpoint、AI ツール定義、既存 API との接続 | `docker/mcp/`, `doc/mcp.md`, `nginx/` |
| Web UX Agent | トップ、統合検索、詳細、状態表示、モバイル UI | `app/`, `app/assets/`, `lib/site_assets.php` |
| Operations Agent | Docker、デプロイ、リモートスクレイピング、状態管理、復旧 | `docker-compose.yml`, `deploy/`, `doc/status-architecture.md`, `doc/remote-scraping.md` |
| Data QA / Red Team | 検索漏れ、データ欠落、リンク切れ、API 不整合、負荷確認 | `tools/tasks/`, `app/health.php`, `app/status/`, golden fixtures if added |
| Documentation Agent | 利用者ガイド、運用 runbook、変更履歴、AI 利用例 | `README.md`, `doc/`, `app/api-guide/` |

実際の担当 AI 名に固定しません。Codex、Claude、Grok、agy などを使う場合も、上の「役割」に割り当てて考えます。

## Codex Orchestrator

すべての作業の入口です。最初に、その変更がどの領域に触れるかを分類します。

- UI: `app/`, `app/assets/`, `lib/site_assets.php`
- API: `app/api/`, `lib/opensearch_search.php`, `app/openapi.*`
- MCP: `docker/mcp/`, `doc/mcp.md`, `nginx/`
- Search: `tools/search/`, `lib/opensearch_search.php`
- Crawler: `tools/gijiroku/`, `tools/reiki/`, `deploy/scraper_runtime/`
- Election poster boards: `domains/election_poster_boards/`, `app/boards/`, `app/line/`, `data/boards/`
- Data master: `data/municipalities/`, `tools/municipality_slugs.py`
- Operations: `docker-compose.yml`, `deploy/`, `doc/status-architecture.md`

変更後は、影響した領域に応じて README、`doc/`、OpenAPI、MCP 説明、リリース手順の更新漏れを確認します。

## Product & Data Steward

自治体データの意味と公開機能の整合性を守ります。

- canonical slug、自治体コード、都道府県、自治体名の扱いを確認する。
- 自治体合併、名称変更、同名自治体の衝突をマスタ側で扱う。
- トップページや公開検索に出すべき自治体かを判断する。
- 「会議録」「例規集」「ポスター掲示場」の機能フラグを混同しない。

主な確認観点:

- `data/municipalities/municipality_master.tsv` と派生ファイルが矛盾していない。
- URL で受け取った slug が canonical slug へ解決される。
- 公開画面に出る件数や状態表示が、管理テーブルの状態と一致する。

## Election Poster Boards Agent

選挙ポスター掲示場の位置情報と作業進捗を扱います。この領域は自治体文書検索から独立しています。

- HTTP実装・認証・SQLite・保守ツールの正本は `domains/election_poster_boards/` に置く。
- `app/boards/` と `app/line/` は既存公開URLの互換アダプターに限定する。
- 実行時データは `data/boards/` に閉じ、会議録・例規集の保存先やOpenSearchへ投入しない。
- 共有してよいのは自治体コード、名称、canonical slug、共通サイトアセットだけとする。
- `boards.sqlite`、`tasks.sqlite`、`users.sqlite` のデプロイ保護と移行手順を壊さない。
- 「掲示板」ではなく「選挙ポスター掲示場」と表記し、一般の掲示板機能と誤認させない。

## Crawler Agent

自治体サイトから会議録・例規集を収集します。

- 会議録: `tools/gijiroku/scrape_all_minutes.py`
- 例規集: `tools/reiki/scrape_all_reiki.py`
- 会議録 scraper: `tools/gijiroku/scrapers/`
- 例規集 scraper: `tools/reiki/scrapers/`
- リモート実行: `deploy/scraper_runtime/`

ルール:

- robots、アクセス間隔、ホスト単位同時実行を守る。
- 取得元 URL、取得時刻、source system、レジューム状態を残す。
- 途中失敗から再実行できるようにする。
- 取得済み成果物を無意味に消さない。
- CMS や会議録システムごとの差分は scraper adapter に閉じ込める。

完了条件:

- 対象 slug の成果物が `work/gijiroku/{slug}` または `work/reiki/{slug}` に残る。
- 取得件数、失敗件数、スキップ件数を説明できる。
- 次の OpenSearch update に渡せる状態になっている。

## ETL & Normalization Agent

取得物を検索可能な文書へ変換します。

- slug、自治体コード、都道府県を付与する。
- 会議日、公布日、施行日、更新日、sort date の意味を分ける。
- 会議名、発言者、条例名、文書タイトルを推測で創作しない。
- Unicode 正規化、空本文、文字化け、重複を検出する。
- 変換前データへ戻れるようにする。

特に `tools/search/scraped_source_records.py` は、検索投入前の意味づけに関わるため、変更時は会議録と例規集の両方を確認します。

## Search Index Agent

OpenSearch の index と検索品質を担当します。

- mapping/settings: `tools/search/index_mappings.json`, `tools/search/index_settings.json`
- index build: `tools/search/build_opensearch_index.py`
- runtime search: `lib/opensearch_search.php`

ルール:

- 公開検索 API は OpenSearch alias を検索する。
- OpenSearch が使えないときは 503 を返す。SQLite FTS fallback は復活させない。
- 通常更新は slug 単位の delete+bulk で差し替える。
- 全量再構築は versioned index を作り、投入完了後に alias を切り替える。
- `miyabe-minutes-current`, `miyabe-reiki-current`, `miyabe-documents-current` の関係を壊さない。

重点テスト:

- `doc_type=minutes` と `doc_type=reiki`
- キーワードなし、0件、大量ヒット
- 都道府県、自治体、日付、並び順
- `api_document_url` から全文取得できること
- source URL が原典確認に使えること

## API & OpenAPI Agent

AI と外部利用者に対する契約を守ります。

- Search API: `app/api/search.php`
- Document API: `app/api/document.php`
- runtime: `lib/opensearch_search.php`
- schema: `app/openapi.json`, `app/openapi.yaml`
- guide: `app/api-guide/index.php`

ルール:

- 既存フィールドを安易に削除しない。
- `doc_type`, `id`, `source_url`, `api_document_url`, `excerpt` の意味を守る。
- API 変更時は OpenAPI JSON/YAML と API Guide を同期する。
- エラーは AI エージェントが解釈しやすい JSON にする。
- 検索結果の抜粋と全文取得の関係を壊さない。

互換性を壊す可能性がある変更は、Product & Data Steward と Documentation Agent も巻き込みます。

## MCP Integration Agent

MCP は、公開 API の読み取り専用ラッパーとして維持します。

- MCP service: `docker/mcp/src/server.ts`
- built output: `docker/mcp/dist/server.js`
- guide: `doc/mcp.md`

ルール:

- MCP サービスに検索ロジックを複製しない。
- `/api/search` と `/api/document` を内部 HTTP で呼ぶ。
- tool description は短く、誤用しにくくする。
- 重要な内容は `get_municipal_document` で全文確認させる。
- AI には、必要に応じて原典 URL の確認も促す。

## Web UX Agent

人間向け画面を、API と同じ意味で操作できるようにします。

- トップ: `app/index.php`, `app/api/home.php`, `app/assets/js/home.js`
- 統合検索: `app/search/`, `app/search/assets/`
- 例規集: `app/reiki/`
- 会議録入口: `app/gijiroku/`
- 状態表示: `app/status/`

ルール:

- UI の条件と API parameter を一致させる。
- 会議録と例規集で表示項目を混同しない。
- 検索結果には、文書種別、自治体、日付、抜粋、詳細、原典を辿れる情報を出す。
- モバイルでも検索条件と結果が破綻しない。
- 画面だけの独自検索仕様を作らない。

## Operations Agent

本番運用、状態管理、復旧手順を担当します。

- Docker: `docker-compose.yml`, `docker/`
- Deploy: `deploy.sh`, `deploy/`
- Status: `doc/status-architecture.md`, `tools/tasks/`, `app/status/`
- Remote scraping: `doc/remote-scraping.md`

ルール:

- 実行状態の正本は PostgreSQL 管理テーブルとする。
- 重い派生値は派生ビューや管理側に寄せる。
- 本番 index rebuild 前には rollback 方針を持つ。
- リモート側の大容量データ配置を deploy で壊さない。
- 監視対象はクロール失敗、API 5xx、OpenSearch 接続、index 件数急減、ディスク使用量。

## Data QA / Red Team

壊れ方を先に探す役割です。

確認観点:

- 特定自治体が検索対象から落ちていないか。
- 会議録が例規集として、または例規集が会議録として入っていないか。
- 日付や自治体名が誤って正規化されていないか。
- OpenAPI と実レスポンスがずれていないか。
- `api_document_url` の全文取得が 404 になっていないか。
- source URL のリンク切れが急増していないか。
- OpenSearch 503 時に UI/API が分かりやすく失敗するか。

報告形式:

1. 壊れる条件
2. 再現手順
3. 実際の挙動
4. 期待される挙動
5. 最小修正案
6. 回帰テスト案
7. 本番投入可否

## Documentation Agent

利用者、開発者、運用者、AI エージェント向けの説明を同期します。

- 実装されていない機能を書かない。
- README と `doc/` と API Guide の説明を矛盾させない。
- OpenAPI と実 API を一致させる。
- AI 利用例では、検索、全文確認、原典確認の順序を明示する。
- 運用手順には、対象コマンドと確認すべき出力を残す。

## タスク振り分け

| 依頼内容 | 入口担当 | 巻き込む担当 |
| --- | --- | --- |
| 検索結果がおかしい | Search Index Agent | ETL, Data QA |
| 特定自治体を追加したい | Product & Data Steward | Crawler, ETL, Search |
| 選挙ポスター掲示場を追加・変更したい | Election Poster Boards Agent | Product, Operations, Data QA |
| 会議録 scraper を増やしたい | Crawler Agent | ETL, Data QA, Operations |
| 例規集 scraper を修正したい | Crawler Agent | ETL, Search |
| API field を追加したい | API & OpenAPI Agent | Search, MCP, Docs |
| MCP tool を変えたい | MCP Integration Agent | API, Docs, Data QA |
| 検索 UI を変えたい | Web UX Agent | API, Search |
| 本番 deploy したい | Operations Agent | Codex, Data QA |
| README/API Guide を更新したい | Documentation Agent | 該当領域の担当 |

## Definition of Done

通常の変更:

- 影響範囲を UI/API/MCP/Search/Crawler/Data/Ops に分類した。
- 変更した領域の既存仕様を確認した。
- 掲示場変更なら自治体文書API・OpenSearchへ依存を持ち込んでいない。
- 会議録 `minutes` と例規集 `reiki` の切替を壊していない。
- `/api/search` と `/api/document` への影響を確認した。
- OpenAPI に影響する場合は `app/openapi.json` と `app/openapi.yaml` を更新した。
- MCP に影響する場合は `docker/mcp/src/server.ts` と `doc/mcp.md` を確認した。
- 検索に影響する場合は OpenSearch alias 前提を維持した。
- ドキュメントが実装と矛盾していない。

クローラ・データ更新:

- 取得元 URL を保持した。
- 差分実行または再実行の方法を説明できる。
- 空本文、文字化け、重複、日付誤認を確認した。
- slug と自治体コードを確認した。
- index update 後に件数が不自然に急減していない。

API・AI 連携:

- `api_document_url` または MCP の全文取得導線が維持されている。
- AI に抜粋だけで断定させない説明になっている。
- OpenAPI/API Guide/MCP guide のいずれかに更新漏れがない。

## 最初に整えるとよい追加成果物

必要になった時点で、次の小さな文書やテストを追加します。

- `doc/search-quality.md`: 検索仕様と回帰観点
- `doc/api-contract.md`: API 互換性方針
- `doc/crawler-release.md`: scraper 変更時の確認手順
- `doc/data-quality.md`: データ品質チェック
- `tests/golden/search/`: 検索結果の golden fixtures
- `tests/golden/api/`: API response schema fixtures
