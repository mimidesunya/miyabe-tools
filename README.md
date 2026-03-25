# Miyabe Tools

みやべ（miyabe）の開発ツール・管理ツール集です。

公開サイト:

- https://tools.miya.be/

## ドキュメント

- [複数自治体対応](doc/multi-municipality.md)
- [ポスター支援ツール](doc/poster-tool.md)
- [例規集ツール](doc/reiki.md)
- [会議録ツール](doc/gijiroku.md)

## ツール一覧

### 1. ポスター支援ツール

選挙ポスター掲示場の設置・撤去作業を管理・共有するための Web アプリケーションです。

- 画面: `/boards/{slug}/`
- 一覧: `/boards/list.php?slug={slug}`

### 2. 例規集ツール

自治体ごとの例規集データを Web 上で閲覧し、AI 評価結果を確認するツールです。

- 画面: `/reiki/?slug={slug}`

### 3. 会議録ツール

自治体ごとの会議録データを SQLite FTS5 で全文検索するツールです。

- 画面: `/gijiroku/?slug={slug}`
- 川崎市向け詳細: [tools/gijiroku/README.md](tools/gijiroku/README.md)

## 公開中のWeb画面

- トップ: https://tools.miya.be/
- 川崎市ポスター掲示場: https://tools.miya.be/boards/kawasaki-shi/
- 川崎市例規集 AI評価ビューア: https://tools.miya.be/reiki/?slug=kawasaki-shi
- 川崎市議会 会議録 全文検索: https://tools.miya.be/gijiroku/?slug=kawasaki-shi
