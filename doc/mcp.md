# MCP連携

会議録・例規集検索は MCP の Streamable HTTP エンドポイントでも公開します。
PHP 側には MCP SDK を入れず、`docker/mcp` の Node.js サービスが既存の `/api/search` と `/api/document` を内部 HTTP で呼びます。

## エンドポイント

- 本番: `https://tools.miya.be/mcp`
- ローカル: `http://localhost:8301/mcp`

nginx は `/mcp` と `/mcp/` を `mcp:3000/mcp` へ proxy します。MCP サービスは検索ロジックを持たず、OpenSearch への問い合わせは従来どおり PHP の公開 API に集約します。

## ツール

- `search_minutes`: 自治体の会議録を検索します
- `search_reiki`: 条例・規則などの例規集を検索します
- `get_municipal_document`: 検索結果の `id` と `doc_type` から本文を取得します

`get_municipal_document` は既定で本文を 20000 文字まで返します。必要な場合は `max_body_chars` を増やせます。

## 開発環境

```bash
docker compose up -d opensearch php mcp web
```

MCP Inspector などから `http://localhost:8301/mcp` に接続します。

## 設定

- `MIYABE_API_BASE_URL`: MCP サービスが呼ぶ既存 Web API の基底 URL。Docker 内では既定で `http://web`
- `MCP_API_TIMEOUT_MS`: `/api/search` と `/api/document` の呼び出しタイムアウト
- `MCP_ALLOWED_HOSTS`: カンマ区切りで Host ヘッダーを制限します。空なら制限しません
- `MCP_ALLOWED_ORIGINS`: カンマ区切りでブラウザ Origin を制限します。空なら制限しません
