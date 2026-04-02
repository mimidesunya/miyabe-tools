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
- `独自`

## スクレイパ系統との対応

`system_type` は観測した URL / 画面の種類を残しています。スクレイパは次の系統に寄せて扱います。

- `gijiroku.com` 系: `gijiroku.com`, `voices`
- `dbsr` 系: `dbsr`, `db-search`, `kaigiroku-indexphp`
- 単独系: `kaigiroku.net`, `kensakusystem`
- 未対応: `amivoice`, `voicetechno`, `msearch`, `独自`

## 再生成

```powershell
pwsh -File tools/municipalities/build_assembly_minutes_system_urls_tsv.ps1
```

必要に応じて `-HomepageCsv data/municipalities/municipality_homepages.csv` を明示できます。
