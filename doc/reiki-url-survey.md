# 例規集URL調査

`data/municipalities/reiki_system_urls.tsv` は、全国自治体マスタ (`data/municipalities/municipality_master.tsv`) に対応する自治体例規集URL一覧です。

## ソース

- 参照元: `https://www.rilg.or.jp/htdocs/main/zenkoku_reiki/zenkoku_Link.html`
- RILG の都道府県別テーブルから、自治体ごとの例規集URLを収集
- 候補URLには実際にアクセスし、到達できたURLだけを採用
- RILG で URL が空欄、または到達不能だった自治体は `data/municipalities/municipality_homepages.csv` を起点に再探索
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

## 公式導線の個別確認

2026-07-16 に北見市公式ページの導線を確認し、旧 `lg.joureikun.jp` から現行の LegalCrud へ更新しました。

| 自治体コード | 自治体 | system_type | 代表 URL |
|---|---|---|---|
| `01208` | 北見市 | `legalcrud` | `https://public2.legalcrud.com/kitami_city/reiki/` |

## 版番号を URL に持つ取得元（北海道町村会）

`houmu.h-chosonkai.gr.jp/~reikidb` は 130 余りの町村を 1 つの DB に同居させ、
`/~reikidb/data/{choson_no}/{版}/reiki.html` のように**版番号を URL に持ちます**。
自治体が新版を出すと版が繰り上がり、**旧ディレクトリごと 404 になります**。
目録も本文も 1 件も取れなくなりますが、前回のマニフェストは残るので、
件数だけはそろって見えます。2026-09-06 の点検では登録 105 行のうち 21 行
（`h-chosonkai` 13 行、同じホストの `taikei` 8 行）が失効し、およそ 6,600 件が
更新されないままでした。

登録簿を引き直すには次を使います。`system_type` は変えません。

```powershell
python dev/municipalities/resolve_h_chosonkai_urls.py --dry-run --only-dead
python dev/municipalities/resolve_h_chosonkai_urls.py --only-dead
```

引き直しは入口ページ（`?choson_no=N`）から現行の版を読みます。入口は
セッションを持つので、`http` で当てると `https` へ転送される際にクエリが
落ちます。必ず `https` で当ててください。

巡回中は `tools/reiki/source_url_recovery.py` が同じ手順で自動復旧します。
登録簿は git 管理なので実行中には書き換えず、引き直した URL を
`work/reiki/source_url_overrides.json` へ積み、対象を読むときに差し替えます。
上書きは「置き換える前の URL」を覚えているので、TSV を直せば自動で外れます。
自動復旧が働いたときは `[WARN] ... 引き直しました` がログに出るので、
**TSV も直してください**。上書きに頼り続けると、登録簿が実態から離れます。

## 再生成

```powershell
pwsh -File dev/municipalities/build_reiki_system_urls_tsv.ps1
```

必要に応じて `-HomepageCsv data/municipalities/municipality_homepages.csv` を明示できます。
