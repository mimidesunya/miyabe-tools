# 会議録URL調査

`data/municipalities/assembly_minutes_system_urls.tsv` は、全国自治体マスタ (`data/municipalities/municipality_master.tsv`) に対応する地方議会会議録URLと取得可否のレジストリです。robots.txtで取得経路が拒否されていてもURLは消さず、除外理由を同じ行に保持します。

## ソース

- 参照元: `https://app-mints.com/kaigiroku/`
- 都道府県別ページを走査して、議会ごとの `会議録検索` URL を収集
- `db-search.com` の死に URL は、自治体種別と都道府県スラッグから `*.dbsr.jp` へ補修
- `和歌山県議会` は公式会議録ページを個別確認して補完
- 候補URLには実際にアクセスし、到達できたURLだけを採用
- `App Mints` で拾えなかった自治体は `data/municipalities/municipality_homepages.csv` を起点に再探索
- 再探索では公式ホームページ内を最大3階層まで辿り、`議会` / `会議録` / `議事録` 系リンクを優先します

## 収録ルール

- キーは自治体コード (`jis_code`)
- URL は会議録サイトの代表URLを1件だけ記録
- `system_type` は URL と実ページの構造から付与
- `会議録検索（PC版）` があれば優先
- 次に `会議録検索` / `議事録検索` を採用
- 公式ホームページ再探索では、自治体サイト内の `議会` ページや `会議録` ページを優先し、見つかった代表ページを採用
- ページに `提供なし` とある自治体、または一覧に見当たらない自治体は空欄
- 空欄は「この調査手順でURLを確定できなかった」ことを意味し、Web 上での不存在を断定するものではありません
- 代表URLが許可されていても、本文取得に必須のAPI・一覧URLが拒否されていれば `excluded` とします
- URLまたは `system_type` を更新した直後は、保存済み判断を無効化してrobots差分監査が済むまで自動取得しません

## 列

- `jis_code`
- `url`
- `system_type`
- `crawl_status`: `enabled` / `excluded` / `review_required` / `unresolved`
- `exclusion_reason`: `robots_disallowed`、`robots_unreachable`、`source_url_unresolved` などの機械可読な理由
- `exclusion_detail`: robots.txt URL、拒否された必須経路、確認不能の内容
- `policy_checked_at`: 取得可否を確認した日（ISO日付）
- `policy_fingerprint`: URL・`system_type`・必須取得経路の変更検出値（システム管理。手編集しない）

`crawl_status` の意味は次のとおりです。

- `enabled`: robots監査済みで自動取得可能
- `excluded`: robots.txtが現在のスクレイパの必須経路を明示的に拒否
- `review_required`: robots.txtを取得できない、またはURL更新後の自動再監査待ち
- `unresolved`: 会議録代表URLを未特定

自動スクレイピングは `enabled` の行だけを対象にします。`excluded` と `review_required` のURLは登録情報や保存済み文書の原典情報から削除しません。

## 通常の追加・変更手順

運用時に手編集するのは原則として `jis_code`、`url`、`system_type` の3列だけです。既存自治体は同じ `jis_code` の行を更新し、重複行は追加しません。残りの状態列はシステムが管理します。

1. `assembly_minutes_system_urls.tsv` の `url` と `system_type` を追加または変更する
2. 通常どおりデプロイする
3. 稼働中の Celery dispatcher が1分以内にフィンガープリント差分を検出し、変更行だけrobots.txtを監査する
4. 許可なら `enabled` にして初回取得を即時投入し、拒否なら `excluded` と拒否経路を記録する

robots.txtを取得できなかった場合は `review_required` となり、取得処理には入りません。監査結果をローカル正本へ確定してコミットしたい場合や手動再確認には、下記の監査コマンドを使います。

## `system_type` の値

- `kaigiroku.net`
- `dbsr`
- `kensakusystem`
- `gijiroku.com`
- `voices`
- `amivoice`
- `kaigiroku-indexphp`
- `voicetechno`
- `db-search`
- `msearch`
- `discussvision`
- `kami-city-pdf`
- `site-gikai-pdf`
- `static-kaigiroku-dir`
- `独自`

## スクレイパ系統との対応

`system_type` は観測した URL / 画面の種類を残しています。スクレイパは次の系統に寄せて扱います。

- `gijiroku.com` 系: `gijiroku.com`, `voices`
- `dbsr` 系: `dbsr`, `db-search`, `kaigiroku-indexphp`
- 単独系: `kaigiroku.net`, `kensakusystem`
- AmiVoice 系: `amivoice`
- msearch 静的会議録系: `msearch`
- PDF・静的ページ系: `kami-city-pdf`, `site-gikai-pdf`, `static-kaigiroku-dir`
- `独自`: 汎用 PDF クロールへ送る。ただし会議録以外の PDF を拾う可能性があるため個別 QA が必要
- 未対応: `discussvision`, `voicetechno`

## 公式導線の個別確認

2026-07-16 に公式議会ページから現行検索システムへの導線を確認し、次を補完しました。

| 自治体コード | 自治体 | system_type | 代表 URL |
|---|---|---|---|
| `15225` | 魚沼市 | `kaigiroku.net` | `https://ssp.kaigiroku.net/tenant/uonuma/SpTop.html` |
| `38213` | 四国中央市 | `kaigiroku.net` | `https://ssp.kaigiroku.net/tenant/shikokuchuo/pg/index.html` |
| `43202` | 八代市 | `voices` | `https://www.city.yatsushiro.kumamoto.jp/VOICES/` |
| `06211` | 東根市 | `msearch` | `https://city.higashine.netj.jp/kensaku/mokuji.html` |

2026-08-02 の深掘り再探索では、公式導線から次も補完しました。

| 自治体コード | 自治体 | system_type | 代表 URL |
|---|---|---|---|
| `01361` | 江差町 | `独自` | `https://www.hokkaido-esashi.jp/gikai/gikai.html` |
| `05346` | 藤里町 | `static-kaigiroku-dir` | `https://www.town.fujisato.akita.jp/town/c613/` |
| `20452` | 筑北村 | `static-kaigiroku-dir` | `https://www.vill.chikuhoku.lg.jp/gikai/kaigiroku/` |
| `21401` | 揖斐川町 | `dbsr` | `https://www.town.ibigawa.gifu.dbsr.jp/` |

揖斐川町はURLを登録していますが、DBSRの必須一覧経路がrobots.txtで拒否されるため `excluded` です。

## 再生成

```powershell
pwsh -File dev/municipalities/build_assembly_minutes_system_urls_tsv.ps1
```

再生成直後のURL行は `review_required` です。必ず必須取得経路を監査して状態を確定します。

```powershell
python tools/gijiroku/audit_minutes_robots.py
python tools/gijiroku/audit_minutes_robots.py --write
```

監査はドライランを先に実行します。特定自治体だけを再確認する場合は `--codes 01361,05346` のように指定できます。

変更行だけをローカルで処理する場合:

```powershell
python tools/gijiroku/audit_minutes_robots.py --stale-only --write
```

空欄自治体を公式ホームページから再探索する場合も、まずドライランで候補を確認します。この探索自体もrobots.txtを守ります。

```powershell
python dev/municipalities/discover_blank_minutes_urls.py
python dev/municipalities/discover_blank_minutes_urls.py --write
python tools/gijiroku/audit_minutes_robots.py --write
```

必要に応じて `-HomepageCsv data/municipalities/municipality_homepages.csv` を明示できます。

既存一覧のうち `独自` 行だけを再検証して、案内ページの先にある共通会議録システムを拾い直す場合:

```powershell
pwsh -File dev/municipalities/build_assembly_minutes_system_urls_tsv.ps1 `
  -SeedTsv data/municipalities/assembly_minutes_system_urls.tsv `
  -OutFile data/municipalities/assembly_minutes_system_urls.tsv `
  -RefineCustomOnly
```
