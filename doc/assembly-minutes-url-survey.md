# 会議録URL調査

`data/municipalities/assembly_minutes_system_urls.tsv` は、全国自治体マスタ (`data/municipalities/municipality_master.tsv`) に対応する地方議会会議録URL一覧です。

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

## 列

- `jis_code`
- `url`
- `system_type`

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

## 再生成

```powershell
pwsh -File dev/municipalities/build_assembly_minutes_system_urls_tsv.ps1
```

必要に応じて `-HomepageCsv data/municipalities/municipality_homepages.csv` を明示できます。

既存一覧のうち `独自` 行だけを再検証して、案内ページの先にある共通会議録システムを拾い直す場合:

```powershell
pwsh -File dev/municipalities/build_assembly_minutes_system_urls_tsv.ps1 `
  -SeedTsv data/municipalities/assembly_minutes_system_urls.tsv `
  -OutFile data/municipalities/assembly_minutes_system_urls.tsv `
  -RefineCustomOnly
```
