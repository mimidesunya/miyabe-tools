# Miyabe Tools

みやべ（miyabe）の開発ツール・管理ツール集です。

公開サイト:

- https://tools.miya.be/

## ドキュメント

- [複数自治体対応](doc/multi-municipality.md)
- [ポスター支援ツール](doc/poster-tool.md)
- [例規集ツール](doc/reiki.md)
- [会議録ツール](doc/gijiroku.md)
- [リモートスクレイピング](doc/remote-scraping.md)

## ツール一覧

### 1. ポスター支援ツール

選挙ポスター掲示場の設置・撤去作業を管理・共有するための Web アプリケーションです。

- 画面: `/boards/{slug}/`
- 一覧: `/boards/list.php?slug={slug}`

### 2. 例規集ツール

自治体ごとの例規集データを Web 上で閲覧し、AI 評価結果を確認するツールです。

- 画面: `/reiki/?slug={slug}`
- 横断全文検索: `/reiki/cross.php`

### 3. 会議録ツール

自治体ごとの会議録データを SQLite FTS5 で全文検索するツールです。  
日本語検索語の分かち書きと正規化には `SudachiPy` を使います。

- 画面: `/gijiroku/?slug={slug}`
- 川崎市向け詳細: [tools/gijiroku/README.md](tools/gijiroku/README.md)

## 公開中のWeb画面

- トップ: https://tools.miya.be/
- 川崎市ポスター掲示場: https://tools.miya.be/boards/kawasaki-shi/
- 川崎市例規集 AI評価ビューア: https://tools.miya.be/reiki/?slug=kawasaki-shi
- 例規集横断全文検索: https://tools.miya.be/reiki/cross.php
- 川崎市議会 会議録 全文検索: https://tools.miya.be/gijiroku/?slug=kawasaki-shi

## トップページ更新方式

- トップページ本体は `/api/home.php` を 5 秒ごとに `fetch` して再描画します
- `/api/home.php` は、ポスター掲示場・会議録・例規集の三つがすべて非表示の自治体を返しません
- `/api/home.php` はサーバー側キャッシュを持ち、自治体カタログや公開件数の重い再計算を毎回やり直さないようにしています
- そのため、トップの自治体一覧は API の返却結果がそのまま表示内容になります

## パフォーマンス方針

- トップや横断検索の初期表示では、全自治体ぶんの `COUNT(*)` をリクエスト中に回しません
- 検索用 SQLite は rebuild 前提なので、公開件数や有無の判定は `MAX(id)` と ready 一覧キャッシュを優先します
- 横断検索の自治体一覧は server-side の ready キャッシュを返し、ブラウザ側の同時検索数も控えめにしています

## 全文検索メモ

- 会議録・例規集の検索インデックスは、SQLite FTS5 に SudachiPy で分かち書きした terms カラムを入れて作ります
- 検索時も PHP から同じ SudachiPy ヘルパを呼び、raw query をそのまま `MATCH` へ投げません
- Web コンテナにも Python と SudachiPy が必要なので、この変更を反映するには PHP イメージの再 build を伴う deploy が必要です

## slug の正規化

- `?slug=` や `/boards/{slug}/` には canonical slug を使います
- 自治体コードだけ、自治体名ローマ字だけ、自治体名だけが渡された場合もサーバー側で canonical slug に解決します
- GET の画面は canonical slug の URL へ 302 リダイレクトします

## リモート配置

本番デプロイではサービスディレクトリ配下の `data` をそのまま `/var/www/data` にマウントし、`data/boards`・`data/users.sqlite`・`data/config.json` は従来どおりその場所に置きます。  
容量の大きい `data/reiki` と `data/gijiroku` だけを `/mnt/big/miyabe-tools/reiki` と `/mnt/big/miyabe-tools/gijiroku` から重ねて見せます。  
これらはリモート側でスクレイパが生成する前提で、`deploy.sh` ではローカル開発環境から同期しません。
