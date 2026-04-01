# リモートスクレイピング

リモートサーバー上で会議録・例規集のスクレイピングを回すためのメモです。

## 事前同期

`tools/` のスクレイパ本体、`work/municipalities/` の自治体一覧、スクレイパ用 Dockerfile をリモートへ同期します。

```bash
python deploy/prepare_remote_scraping.py deploy.json --build-image
```

既存の途中状態も持っていきたい場合:

```bash
python deploy/prepare_remote_scraping.py deploy.json --sync-gijiroku-work --sync-reiki-work --build-image
```

## リモートでの議事録取得

`assembly_minutes_system_urls.tsv` のうち、実装済みの `gijiroku.com` / `kaigiroku.net` / `dbsr` を対象にします。  
同一ホストには既定で 1 自治体ずつしか当てません。

```bash
cd ~/services/miyabe-tools
nohup sh ./tools/remote/run_gijiroku_remote.sh \
  --ack-robots \
  --parallel 4 \
  --per-host-parallel 1 \
  > logs/scraping/gijiroku.out 2>&1 &
```

対象確認だけしたい場合:

```bash
python3 tools/gijiroku/scrape_all_minutes.py --list-targets --max-targets 20
```

## リモートでの例規取得

`reiki_system_urls.tsv` のうち、実装済みの `d1-law` / `taikei` を対象にします。  
`--check-updates` を付けると既存条例も再取得して更新確認します。

```bash
cd ~/services/miyabe-tools
nohup sh ./tools/remote/run_reiki_remote.sh \
  --parallel 4 \
  --per-host-parallel 1 \
  --check-updates \
  > logs/scraping/reiki.out 2>&1 &
```

対象確認だけしたい場合:

```bash
python3 tools/reiki/scrape_all_reiki.py --list-targets --max-targets 20
```

## 補足

- スクレイパ本体は `miyabe-tools-scraper` イメージ内で動かします。
- 公開データの書き込み先は `SHARED_DATA_DIR`（既定: `/mnt/big/miyabe-tools`）を `data/reiki` / `data/gijiroku` に重ねて、`boards` と分離したまま共有領域へ保存します。
- 会議録・例規とも、ホスト単位の同時実行数と起動間隔で負荷を抑えます。
- `work/gijiroku` / `work/reiki` を同期した場合は、既存のレジューム状態をそのまま利用できます。
