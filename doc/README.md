# ドキュメント索引

## ツール別ガイド

- [poster-tool.md](poster-tool.md) — 選挙ポスター掲示場ドメイン。マップ、作業進捗、LINE ログイン、SQLite移行
- [gijiroku.md](gijiroku.md) — 会議録ツール。スクレイプ成果物から OpenSearch index を作る流れと画面・API
- [reiki.md](reiki.md) — 例規集ツール。保存済み HTML / Markdown / JSON からの index 作成と画面・API
- [mcp.md](mcp.md) — MCP 連携。`/mcp` エンドポイント、ツール定義、設定項目
- [tatsuhiko-map.md](tatsuhiko-map.md) — 宮部たつひこマップ。現在地の公開ページと本人用管理ページ（GPS 提供の ON/OFF）

## 設計・運用

- [multi-municipality.md](multi-municipality.md) — 複数自治体対応の共通設計。slug 正規化、URL ルール、自治体追加手順
- [domain-boundaries.md](domain-boundaries.md) — 自治体文書と選挙ポスター掲示場の所有範囲、許可する共有、互換入口
- [home-page.md](home-page.md) — トップページ（自治体マップ）の描画方式と表示ルール
- [status-architecture.md](status-architecture.md) — 実行状態管理。PostgreSQL 正本のテーブル構成、表示ルール、移行手順
- [remote-scraping.md](remote-scraping.md) — リモートスクレイピング運用。事前同期、Celery 巡回、再起動・停止手順
- [virtual-development-team.md](virtual-development-team.md) — AI エージェントと人間で共同開発するときの役割分担

## 自治体マスタ・URL 調査

- [municipality-master.md](municipality-master.md) — 全国自治体マスタ `municipality_master.tsv` の作成方法
- [local-government-homepages.md](local-government-homepages.md) — 自治体公式ホームページ URL 一覧 `municipality_homepages.csv` の作成方法
- [assembly-minutes-url-survey.md](assembly-minutes-url-survey.md) — 地方議会会議録システム URL 一覧 `assembly_minutes_system_urls.tsv` の調査手順
- [reiki-url-survey.md](reiki-url-survey.md) — 自治体例規集システム URL 一覧 `reiki_system_urls.tsv` の調査手順
- [collection-gap-survey.md](collection-gap-survey.md) — 会議録・例規集の収集取りこぼしの全国調査と対応計画（未完了の作業あり）
