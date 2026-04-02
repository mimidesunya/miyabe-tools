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

このコマンドは既定で `docker-compose.scraping.yml` をリモートに配置し、会議録・例規のスクレイパサービスを `up -d --force-recreate` します。コードだけ同期して自動再起動したくない場合は `--no-restart-services` を付けます。

## リモートでの議事録取得

`assembly_minutes_system_urls.tsv` のうち、実装済みの `gijiroku.com` / `voices` / `kaigiroku.net` / `dbsr` / `db-search` / `kaigiroku-indexphp` / `kensakusystem` を対象にします。  
同一ホストには既定で 1 自治体ずつしか当てません。

既定ではデプロイ時に自動起動・再起動されます。状態確認:

```bash
cd ~/services/miyabe-tools
docker compose -f docker-compose.scraping.yml ps
docker compose -f docker-compose.scraping.yml logs -f scraper-gijiroku
```

手動で再起動したい場合:

```bash
docker compose -f docker-compose.scraping.yml restart scraper-gijiroku
```

`scraper-gijiroku` は各サイクルの先頭で `build_missing_minutes_indexes.py` を走らせ、`要反映`、完了間近、未着手、反映済みの順で `minutes.sqlite` の不足分補完を進めます。通常の全国スクレイプに入る前に反映漏れを優先的に埋める運用です。

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
```

手動で再起動したい場合:

```bash
docker compose -f docker-compose.scraping.yml restart scraper-reiki
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
- サービスは `unless-stopped` で起動し、各サイクル完了後は既定 6 時間スリープして次の巡回に入ります。
- `work/gijiroku` / `work/reiki` を同期した場合は、既存のレジューム状態をそのまま利用できます。
