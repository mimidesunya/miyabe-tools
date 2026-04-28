# リモートスクレイピング

リモートサーバー上で会議録・例規集のスクレイピングを回すためのメモです。

## 事前同期

`tools/` のスクレイパ本体、`data/municipalities/` の自治体一覧、スクレイパ用 Dockerfile をリモートへ同期します。

```bash
python deploy/prepare_remote_scraping.py deploy.json --build-image
```

既存の途中状態も持っていきたい場合:

```bash
python deploy/prepare_remote_scraping.py deploy.json --sync-gijiroku-work --sync-reiki-work --build-image
```

このコマンドは既定で `docker-compose.scraping.yml` をリモートに配置し、Redis・Celery beat・会議録 worker・例規集 worker を `up -d --force-recreate` します。`tools/` と `lib/python/` もまとめて同期するので、fresh remote でも Celery task から必要な補助モジュールまで揃います。コードだけ同期して自動再起動したくない場合は `--no-restart-services` を付けます。

既定では、スクレイパ image が未作成か、`docker/scraper/Dockerfile` / `tools/requirements-scraping.txt` の内容が前回 build 時から変わっている場合だけ自動で rebuild します。`--build-image` を付けると差分有無に関係なく強制 rebuild します。

## リモートでの議事録取得

`assembly_minutes_system_urls.tsv` のうち、実装済みの `gijiroku.com` / `voices` / `kaigiroku.net` / `dbsr` / `db-search` / `kaigiroku-indexphp` / `kensakusystem` を対象にします。  
同一ホストには既定で 1 自治体ずつしか当てません。

既定ではデプロイ時に自動起動・再起動されます。状態確認:

```bash
cd ~/services/miyabe-tools
docker compose -f docker-compose.scraping.yml ps
docker compose -f docker-compose.scraping.yml logs -f scraper-gijiroku
docker compose -f docker-compose.scraping.yml logs -f scraper-beat
```

手動で再起動したい場合:

```bash
docker compose -f docker-compose.scraping.yml restart scraper-gijiroku scraper-beat
```

ローカルから単発のリモートコマンドを打ちたい場合は、`deploy.json` の鍵設定を使うヘルパーを使えます。
このヘルパーは `wsl_key_path` / `key_path` を読み、一時鍵へコピーして権限を絞ってから SSH 実行します。

```bash
python3 deploy/remote_exec.py deploy.json -- "cd ~/services/miyabe-tools && docker compose -p miyabe-tools-scraping -f docker-compose.scraping.yml ps"
```

`scraper-beat` は 1 分ごとに dispatcher task を投げ、会議録 worker は「前回の完了から既定 6 時間以上経過しているか」を見て `run_gijiroku_cycle` を queue へ積みます。`run_gijiroku_cycle` は各サイクルの先頭で `build_missing_minutes_indexes.py` を走らせ、`要反映`、完了間近、未着手、反映済みの順で `minutes.sqlite` の不足分補完を進めてから通常の全国スクレイプに入ります。

即時に 1 サイクル走らせたい場合:

```bash
docker compose -f docker-compose.scraping.yml exec scraper-gijiroku \
  python3 tools/remote/celery_enqueue.py gijiroku-cycle
```

会議録の全文検索 DB を明示的に再構築したい場合:

```bash
docker compose -f docker-compose.scraping.yml exec scraper-gijiroku \
  python3 tools/remote/celery_enqueue.py gijiroku-rebuild --filter 01000
```

対象確認だけしたい場合:

```bash
python3 tools/gijiroku/scrape_all_minutes.py --list-targets --max-targets 20
```

## リモートでの例規取得

`reiki_system_urls.tsv` のうち、実装済みの `d1-law` / `taikei` を対象にします。  
`--check-updates` を付けると既存条例も再取得して更新確認します。  
各サイクルの先頭では `build_missing_ordinance_indexes.py` を優先実行し、`要反映`、完了間近、未着手、反映済みの順で `ordinances.sqlite` の不足分補完を進めます。各自治体のスクレイプ完了後にも `ordinances.sqlite` を自動で再構築するので、取得済み HTML のうち未反映だったページもその時点で検索対象に入ります。

状態確認:

```bash
cd ~/services/miyabe-tools
docker compose -f docker-compose.scraping.yml logs -f scraper-reiki
docker compose -f docker-compose.scraping.yml logs -f scraper-beat
```

手動で再起動したい場合:

```bash
docker compose -f docker-compose.scraping.yml restart scraper-reiki scraper-beat
```

即時に 1 サイクル走らせたい場合:

```bash
docker compose -f docker-compose.scraping.yml exec scraper-reiki \
  python3 tools/remote/celery_enqueue.py reiki-cycle
```

例規集の全文検索 DB を明示的に再構築したい場合:

```bash
docker compose -f docker-compose.scraping.yml exec scraper-reiki \
  python3 tools/remote/celery_enqueue.py reiki-rebuild --filter 26214
```

対象確認だけしたい場合:

```bash
python3 tools/reiki/scrape_all_reiki.py --list-targets --max-targets 20
```

## 補足

- スクレイパ本体は `miyabe-tools-scraper` イメージ内で動かします。
- 公開データの書き込み先は `SHARED_DATA_DIR`（既定: `/mnt/big/miyabe-tools`）を `data/reiki` / `data/gijiroku` に重ねて、`boards` と分離したまま共有領域へ保存します。
- デプロイ時の正規化では、旧 `name-only` ディレクトリも `自治体コード-ローマ字名称` へ移動します。背景タスク JSON の slug も同じ正規形に揃えます。
- 会議録・例規とも、ホスト単位の同時実行数と起動間隔で負荷を抑えます。
- サービスは `unless-stopped` で起動し、Celery beat の dispatcher が既定 6 時間ごとに次の巡回を queue へ積みます。
- `work/gijiroku` / `work/reiki` を同期した場合は、既存のレジューム状態をそのまま利用できます。
