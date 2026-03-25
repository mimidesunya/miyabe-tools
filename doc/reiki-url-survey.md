# 例規集URL調査

`work/municipalities/reiki_system_urls.tsv` は、全国自治体マスタ (`work/municipalities/municipality_master.tsv`) に対応する自治体例規集URL一覧です。

## ソース

- 参照元: `https://www.rilg.or.jp/htdocs/main/zenkoku_reiki/zenkoku_Link.html`
- RILG の都道府県別テーブルから、自治体ごとの例規集URLを収集
- 候補URLには実際にアクセスし、到達できたURLだけを採用
- RILG で URL が空欄、または到達不能だった自治体は `work/municipalities/municipality_homepages.csv` を起点に再探索
- 再探索では公式ホームページ内を最大3階層まで辿り、`例規集` / `例規` / `条例` / `規則` 系リンクを優先します

## 収録ルール

- キーは自治体コード (`jis_code`)
- URL は例規集サイトの代表URLを1件だけ記録
- `system_type` は URL と実ページの構造から付与
- RILG にリンクがあっても、到達不能なら空欄に戻して再探索します
- 一覧に見当たらない自治体、またはこの調査手順で URL を確定できなかった自治体は空欄
- 空欄は「この調査手順でURLを確定できなかった」ことを意味し、Web 上での不存在を断定するものではありません

## 列

- `jis_code`
- `url`
- `system_type`

## `system_type` の値

- `d1-law`
- `g-reiki`
- `joureikun`
- `legal-square`
- `legalcrud`
- `h-chosonkai`
- `taikei`
- `jourei-v5`
- `reiki_menu`
- `reiki.html`
- `独自`

## 再生成

```powershell
pwsh -File tools/municipalities/build_reiki_system_urls_tsv.ps1
```

必要に応じて `-HomepageCsv work/municipalities/municipality_homepages.csv` を明示できます。
