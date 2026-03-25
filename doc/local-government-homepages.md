# 自治体ホームページ一覧

`work/municipalities/municipality_homepages.csv` は、全国自治体マスタに対応する公式ホームページ URL 一覧です。

## ソース

- 取得元: `https://raw.githubusercontent.com/code4fukui/localgovjp/master/localgovjp-utf8.csv`
- 取得元: `https://raw.githubusercontent.com/code4fukui/localgovjp/master/prefjp-utf8.csv`
- 上流データ: Code for Fukui `localgovjp`
- 更新案内: `https://fukuno.jig.jp/4658`

`localgovjp` は J-LIS の全国自治体マップ検索と国土地理院データを元に整備されている公開データです。現環境では J-LIS 本体の機械取得が Cloudflare に止められるため、この機械可読な上流を利用しています。

## 収録方針

- [municipality_master.tsv](../work/municipalities/municipality_master.tsv) の `jis_code` を基準に突き合わせます
- 都道府県 URL は `prefjp-utf8.csv` から採用します
- 市町村・特別区 URL は `localgovjp-utf8.csv` の `lgcode` 先頭5桁を使って採用します
- 政令指定都市の行政区は全国自治体マスタ側に無いので出力対象外です
- 北方領土6村は上流に URL が無いため空欄になります

## 列

- `jis_code`: 標準地域コード5桁
- `url`: 自治体公式ホームページ URL。見つからない場合は空欄

## 再生成

```powershell
pwsh -File tools/municipalities/build_municipality_homepages_csv.ps1
```

ローカルに取得済みの CSV を使う場合は `-MunicipalitySourceCsvPath` と `-PrefectureSourceCsvPath` を渡します。
