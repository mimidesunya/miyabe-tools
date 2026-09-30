# アクセスログの置き場所と保存期間

プライバシーポリシー（`/privacy/`）で「アクセスログとエラー記録は記録から30日以内に削除」と約束している。
この約束は、次の仕組みが守っている。どちらかを変えるときは、ポリシーの記述も合わせて直す。

## 仕組み

- web（nginx）はアクセスログを `nginx-logs` ボリュームの `access-YYYY-MM-DD.log`（UTC の日付）へ日ごとに書く。
  エラーログは同じボリュームの `error.log`。設定は `nginx/default.conf`。
- log-pruner（`nginx/prune-logs.sh`）が10分おきに見て、次の2つを行う。
  - 日付が変わったら、`error.log` の中身を `error-YYYY-MM-DD.log` へ移して空にする。
  - 最後の書き込みから29日を過ぎたファイルを消す。1日分のファイルの先頭は、最後の書き込みより最大1日古いので、どの記録も30日以内に消える。
- 以前はアクセスログを標準出力へ流していたため、Docker のログに期限なく残っていた。標準出力に残るのは、nginx の起動などの通知だけになった。

本番の compose は `deploy/deploy.py` が生成する。開発用の `docker-compose.yml` にも同じ web と log-pruner の設定がある。

## 読み方（本番）

```sh
cd ~/services/miyabe-tools
docker compose exec web ls -l /var/log/nginx/site
docker compose exec web tail -n 50 /var/log/nginx/site/access-$(date -u +%F).log
docker compose exec web tail -n 50 /var/log/nginx/site/error.log
```

## ほかに残るもの

- 検索語の解析結果のキャッシュ（`data/background_tasks/japanese_search_query_cache/`）。利用者と結び付かない。1日を過ぎたものは、検索のついでに消える（`lib/japanese_search.php`）。
- php-fpm と MCP の標準出力。php-fpm のアクセス記録（公式イメージの既定の書式）に入るのは、前段のプロキシのアドレスと、クエリ文字列を除いたスクリプトのパスだけ。利用者の IP や検索語は入らない。MCP が出すのは起動とエラーだけ。
