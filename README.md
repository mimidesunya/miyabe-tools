# Miyabe Tools

みやべ（miyabe）の開発ツール・管理ツール集です。

公開サイト:

- https://tools.miya.be/

## ドキュメント

一覧と各文書の概要は [doc/README.md](doc/README.md) を参照してください。

- ツール別: [ポスター支援ツール](doc/poster-tool.md) / [例規集ツール](doc/reiki.md) / [会議録ツール](doc/gijiroku.md) / [MCP連携](doc/mcp.md)
- 設計・運用: [ドメイン境界](doc/domain-boundaries.md) / [複数自治体対応](doc/multi-municipality.md) / [トップページ](doc/home-page.md) / [実行状態管理の設計](doc/status-architecture.md) / [リモートスクレイピング](doc/remote-scraping.md) / [仮想開発チーム](doc/virtual-development-team.md)
- 自治体マスタ・URL調査: [自治体マスタ](doc/municipality-master.md) / [公式ホームページ一覧](doc/local-government-homepages.md) / [会議録URL調査](doc/assembly-minutes-url-survey.md) / [例規集URL調査](doc/reiki-url-survey.md)

## ディレクトリ構成

- `domains/` — 独立した業務領域。`election_poster_boards/` が選挙ポスター掲示場のHTTP実装、認証、SQLite、保守ツールを所有
- `app/` — 公開 Web 入口と API（PHP）。`app/boards/`・`app/line/` はURL互換アダプター、`app/api/` は会議録・例規集の公開 API
- `lib/` — PHP 共通ライブラリ（自治体レジストリ、OpenSearch 検索、実行状態管理など）。`lib/python/` は PHP から呼ぶ補助 Python
- `tools/` — 本番系 Python パイプライン。`tools/gijiroku/`・`tools/reiki/` がスクレイパ、`tools/search/` が OpenSearch index 構築、`tools/tasks/` がバッチ実行基盤。直下の `municipality_slugs.py` などは各パイプライン共通のモジュール
- `dev/` — 開発・単発作業用スクリプト。`dev/boards/` のコマンドは掲示場ドメインへの互換入口と元データ置場
- `deploy/` — デプロイとリモートスクレイピング環境の構築。`deploy/scraper_runtime/` は Celery ランタイム
- `docker/` — 各サービスの Dockerfile（php / nginx / mcp / scraper）
- `nginx/` — 公開側 nginx 設定
- `data/` — 公開データと自治体マスタ。`data/municipalities/` のみ git 管理
- `work/` — スクレイピング途中成果物などのローカル作業領域（git 管理外）
- `kmzs/` — ポスター掲示場の KMZ 地図原本（git 管理外）
- `doc/` — ドキュメント

## ツール一覧

### 1. ポスター支援ツール

選挙ポスター掲示場の設置・撤去作業を管理・共有するための Web アプリケーションです。

- 画面: `/boards/{slug}/`
- 一覧: `/boards/list.php?slug={slug}`

### 2. 例規集ツール

自治体ごとの例規集データを Web 上で閲覧し、AI 評価結果を確認するツールです。

- 画面: `/reiki/?slug={slug}`
- 統合検索: `/search/?doc_type=reiki`

### 3. 会議録ツール

自治体ごとの会議録スクレイピング結果を閲覧し、検索は OpenSearch の統合検索 API に集約します。  
OpenSearch の index はスクレイピング済みファイルから再構築できます。

- 画面: `/gijiroku/?slug={slug}`
- 統合検索: `/search/?doc_type=minutes`
- 川崎市向け詳細: [tools/gijiroku/README.md](tools/gijiroku/README.md)

## 公開中のWeb画面

- トップ: https://tools.miya.be/
- 川崎市ポスター掲示場: https://tools.miya.be/boards/14130-kawasaki-shi/
- 川崎市例規集 AI評価ビューア: https://tools.miya.be/reiki/?slug=14130-kawasaki-shi
- 会議録・例規集 統合検索: https://tools.miya.be/search/
- 川崎市議会 会議録 全文検索: https://tools.miya.be/gijiroku/?slug=14130-kawasaki-shi

## トップページ

トップページは `/api/home.php` の結果から会議録・例規集だけの自治体対応マップを描画します。選挙ポスター掲示場は自治体一覧へ混ぜず、独立した `/boards/` で提供します。実行状態の正本は PostgreSQL の管理テーブルです。描画方式・API・表示ルールの詳細は [doc/home-page.md](doc/home-page.md) を参照してください。

## 検索基盤

- 公開検索 API は `/api/search` です
- `/api/search` は OpenSearch alias だけを検索します
- MCP エンドポイントは `/mcp` です。会議録検索、例規集検索、検索結果IDからの本文取得を読み取り専用ツールとして公開します
- OpenSearch が利用できない場合、検索 API は 503 を返します
- SQLite FTS5 への検索フォールバックはありません
- `minutes.sqlite` / `ordinances.sqlite` は公開検索には不要です。削除されていても、スクレイピング済みファイルから OpenSearch index を作れます
- `search_batch` とブラウザ側の自治体ごとの逐次検索は廃止しました

### OpenSearch 開発環境

`docker-compose.yml` に OpenSearch と OpenSearch Dashboards を含めています。接続設定は `.env.example` をコピーして調整します。

```bash
cp .env.example .env
docker compose up -d opensearch php web
```

全量再構築では、スクレイピング済みファイルから versioned index を作り、alias を atomic switch します。

```bash
python tools/search/build_opensearch_index.py --mode rebuild --doc-type all
```

通常の巡回では、スクレイプが終わった自治体だけを current alias へ差し替えます。alias がまだない初回は、その slug だけを入れた index を作ってから、その後の自治体が徐々に追加されます。

```bash
python tools/search/build_opensearch_index.py --mode update --doc-type minutes --slug 14130-kawasaki-shi
```

主な alias:

- `miyabe-minutes-current`
- `miyabe-reiki-current`
- `miyabe-documents-current`

## slug の正規化

- `?slug=` や `/boards/{slug}/` には canonical slug を使います
- 自治体コードだけ、自治体名ローマ字だけ、自治体名だけが渡された場合もサーバー側で canonical slug に解決します
- GET の画面は canonical slug の URL へ 302 リダイレクトします

## リモート配置

本番デプロイではサービスディレクトリ配下の `data` をそのまま `/var/www/data` にマウントし、掲示場データと共有LINEユーザーDBは `data/boards/`、設定は `data/config.json` に置きます。旧 `data/users.sqlite` はデプロイ時に `data/boards/users.sqlite` へ非破壊コピーされ、移行期間中は読み取り互換も維持します。
容量の大きい `data/reiki` と `data/gijiroku` だけを `/mnt/big/miyabe-tools/reiki` と `/mnt/big/miyabe-tools/gijiroku` から重ねて見せます。  
これらはリモート側でスクレイパが生成する前提で、`deploy.sh` ではローカル開発環境から同期しません。
次回デプロイ時には旧 `src` と旧検索用 SQLite ファイルをリモート側でも削除します。
