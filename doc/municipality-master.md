# 自治体マスタ

`data/municipalities/municipality_master.tsv` は、会議録や例規集を複数自治体へ広げる前提で使う全国自治体マスタです。`data/municipalities` を git 管理された正本として扱います。

## ソース

- 公式ソース: 国土地理院「全国都道府県市区町村別面積調」
- 取得元: `https://www.gsi.go.jp/KOKUJYOHO/MENCHO/backnumber/R7_10_mencho.csv`
- 基準日: 2025-10-01

## 収録方針

- 都道府県行を含めます
- 市町村行を含めます
- 東京都の特別区は `special_ward` として含めます
- 政令指定都市の行政区は除外します
- 国土地理院 CSV に含まれる北方領土6村はそのまま含めます

## 列

- `source_date`: 元データの基準日
- `entity_type`: `prefecture` / `municipality` / `special_ward`
- `jis_code`: 標準地域コード
- `parent_jis_code`: 親の都道府県コード (`XX000`)
- `pref_code`: 2桁の都道府県コード
- `pref_name`: 都道府県名
- `district_name`: 郡・支庁・振興局等
- `name`: 都道府県名または市町村名
- `name_kana`: `name` の読み仮名。例: `ゆうばりし`, `きょうとふ`
- `full_name`: `都道府県名 + 半角スペース + 名称`
- `name_romaji`: slug 用のローマ字名称。例: `hakodate-shi`, `kyoto-fu`

## 再生成

```powershell
pwsh -File tools/municipalities/build_municipality_master_tsv.ps1
```

続けて読み仮名とローマ字列を付ける場合:

```bash
python tools/municipalities/enrich_municipality_master_tsv.py
```

`name_kana` は `localgovjp` / `prefjp` のかな列から補完し、`name_romaji` は `municipality_homepages.csv` と既存 `config.json` の slug を使って補完します。
