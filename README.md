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

## トップページ更新方式

- トップページ本体は `/api/home.php` を 5 秒ごとに `fetch` して再描画します
- `/api/home.php` は、ポスター掲示場・会議録・例規集の三つがすべて非表示の自治体を返しません
- トップページの自治体カードは都道府県ごとにグループ化して表示します
- トップページの時刻表示は `Asia/Tokyo` に揃えます
- トップページでは `🪧 掲示板` `📝 会議録` `📚 例規集` のアイコンで機能を見分けやすくしています
- `/api/home.php` はサーバー側キャッシュを持ち、自治体カタログや公開件数の重い再計算を毎回やり直さないようにしています
- そのため、トップの自治体一覧は API の返却結果がそのまま表示内容になります
- トップページの進捗は、スクレイピング済みファイル・manifest・タスク状態から復元します
- 会議録・例規集の公開検索への反映は、通常はスクレイプ完了自治体だけを OpenSearch alias 上で差し替えます
- 全量再構築が必要なときは versioned index を作って alias を切り替える方式です

## 検索基盤

- 公開検索 API は `/api/search` です
- `/api/search` は OpenSearch alias だけを検索します
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

本番デプロイではサービスディレクトリ配下の `data` をそのまま `/var/www/data` にマウントし、`data/boards`・`data/users.sqlite`・`data/config.json` は従来どおりその場所に置きます。  
容量の大きい `data/reiki` と `data/gijiroku` だけを `/mnt/big/miyabe-tools/reiki` と `/mnt/big/miyabe-tools/gijiroku` から重ねて見せます。  
これらはリモート側でスクレイパが生成する前提で、`deploy.sh` ではローカル開発環境から同期しません。
次回デプロイ時には旧 `src` と旧検索用 SQLite ファイルをリモート側でも削除します。
