# 会議録URL調査

`data/municipalities/assembly_minutes_system_urls.tsv` は、全国自治体マスタ (`data/municipalities/municipality_master.tsv`) に対応する地方議会会議録URLと取得可否のレジストリです。取得できない自治体もURLは消さず、除外理由を同じ行に保持します。

## robots.txt の扱い

**robots.txt を取得可否の根拠にしません。** 議事録と法令は国民の財産であり、
公開されている以上は取得します。robots.txt は法的な制限ではなく検索エンジン
向けの慣行にすぎない、という運営判断です（2026-08-28 決定、2026-09-06 再確認）。
方針は `tools/gijiroku/crawl_policy.py` の冒頭に書いてあります。robots.txt を読んで
判定するコードは 2026-10-01 に削除しました。

個別の自治体で robots.txt が拒否していても方針は変えません。2026-09-06 の
点検では浦幌町の robots.txt が会議録 PDF の置き場所を拒否していましたが、
取得する判断としました。

相手側への配慮は robots ではなく、**ホスト単位のレート制限と正直な
User-Agent** で行います。

`excluded` は robots とは別の理由にだけ使います。本文が存在しない
（`not_published` / `video_only`）、認証が要る（`login_required`）などです。
台帳の変更検出（`tools/gijiroku/audit_minutes_registry.py`）は、除外の理由が
書かれた行に触れません。

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
- `crawl_status=enabled` の行は、URLや `system_type` が変わってもそのまま取得します
- 取得しないのは、本文が無い・認証が要るなど **robots とは別の理由**があるときだけです

## 列

- `jis_code`
- `url`
- `system_type`
- `crawl_status`: `enabled` / `excluded` / `review_required` / `unresolved`
- `exclusion_reason`: `not_published`、`video_only`、`login_required`、`source_url_unresolved` などの機械可読な理由
- `exclusion_detail`: 何が無いのか、なぜ取れないのかを日本語で書きます
- `policy_checked_at`: 取得可否を確認した日（ISO日付）
- `policy_fingerprint`: URL・`system_type`・必須取得経路の変更検出値（システム管理。手編集しない）

`crawl_status` の意味は次のとおりです。

- `enabled`: 自動取得の対象
- `excluded`: 取得しても本文が得られない、または認証が要る
- `review_required`: 人の確認待ち
- `unresolved`: 会議録代表URLを未特定

自動スクレイピングは `enabled` の行だけを対象にします。`excluded` と `review_required` のURLは登録情報や保存済み文書の原典情報から削除しません。

## 通常の追加・変更手順

既存自治体は同じ `jis_code` の行を更新し、重複行は追加しません。通常の自動判定では `url` と `system_type` を更新します。その他の状態列はシステムが管理します。

1. `assembly_minutes_system_urls.tsv` の `url` と `system_type` を追加または変更する
2. 通常どおりデプロイする
3. `crawl_status=enabled` なら、稼働中の Celery dispatcher が取得サイクルを即時投入する

本文が無い・認証が要ると分かった自治体は、`crawl_status` を `excluded` にし、
`exclusion_reason` と `exclusion_detail` に理由を書きます。毎周回で失敗させ
続けると、直せる失敗がその中に埋もれます。

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
- `shizuoka-notes`、`chuo-kugikai`、`nakano-kugikai`、`echizen-search`、`yoshinogawa-asp`、`izumi-cake`、`oumu-dbpocket`、`kin-jsp`、`iwate-kengikai`（年別一覧 → 文書ページだけの独自サイト）

## スクレイパ系統との対応

`system_type` は観測した URL / 画面の種類を残しています。スクレイパは次の系統に寄せて扱います。

- `gijiroku.com` 系: `gijiroku.com`, `voices`
- `dbsr` 系: `dbsr`, `db-search`, `kaigiroku-indexphp`
- 単独系: `kaigiroku.net`, `kensakusystem`
- AmiVoice 系: `amivoice`
- msearch 静的会議録系: `msearch`
- PDF・静的ページ系: `kami-city-pdf`, `site-gikai-pdf`, `static-kaigiroku-dir`
- `独自`: 汎用 PDF クロールへ送る。ただし会議録以外の PDF を拾う可能性があるため個別 QA が必要
- 年別一覧 → 文書ページだけの独自サイト: `shizuoka-notes`, `chuo-kugikai`, `nakano-kugikai`, `echizen-search`, `yoshinogawa-asp`, `izumi-cake`, `oumu-dbpocket`, `kin-jsp`, `voicetechno`, `iwate-kengikai`（`tools/gijiroku/scrapers/html_list_sites.py` のアダプタ）
- 未対応: `discussvision`（録画のみ）

正確な対応表は `tools/gijiroku/scrape_all_minutes.py` の `SUPPORTED_SYSTEMS` です。

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

揖斐川町は会議録の代表URLを特定できておらず `unresolved` です（robots とは関係ありません）。

## 台帳の変更検出（手動）

デプロイ後は取得 worker が自動で行います（`doc/remote-scraping.md`）。手元で確かめる場合はドライランから始めます。

```powershell
python tools/gijiroku/audit_minutes_registry.py
python tools/gijiroku/audit_minutes_registry.py --stale-only --write
```

特定自治体だけを見る場合は `--codes 01361,05346` のように指定します。`enabled` の行は監査しません（運用者の明示許可なので）。

## 空欄自治体の再探索

`tools/gijiroku/discover_minutes_urls.py` で公式ホームページから候補を出します（使い方は `tools/gijiroku/README.md`）。Celery の `sweep-gijiroku-source-discovery` も URL が空の行を定期的に探索し、見つけた取得元を `work/gijiroku/discovered_sources.json` に控えて台帳に重ねます。

## 再生成（App Mints から作り直す）

```powershell
pwsh -File dev/municipalities/build_assembly_minutes_system_urls_tsv.ps1 `
  -SeedTsv data/municipalities/assembly_minutes_system_urls.tsv `
  -OutFile work/assembly_minutes_system_urls.regenerated.tsv
```

既定の出力先は正本の TSV そのものです。`-SeedTsv` を付けずに回すと、手で足した系統（`html_list_sites.py` のアダプタや `iwate-kengikai`）と除外の理由が消えます。出力は別ファイルにして、差分を確かめてから取り込みます。取り込んだ後は、上の変更検出で状態を記録し直します。

既存一覧のうち `独自` 行だけを再検証して、案内ページの先にある共通会議録システムを拾い直す場合:

```powershell
pwsh -File dev/municipalities/build_assembly_minutes_system_urls_tsv.ps1 `
  -SeedTsv data/municipalities/assembly_minutes_system_urls.tsv `
  -OutFile work/assembly_minutes_system_urls.regenerated.tsv `
  -RefineCustomOnly
```
