# 包括外部監査URL調査

自治体が公開する**包括外部監査の結果報告書**の入口 URL を全国で調べ、報告書を
取得する手順です。台帳は `data/municipalities/gaibu_kansa_system_urls.tsv`、
道具は `tools/kansa/` にあります。

## 包括外部監査とは

監査委員とは別の外部の監査人（公認会計士・弁護士など）が、毎年テーマを選んで
行う監査です。結果は「包括外部監査結果報告書」として公開されます。地方自治法
252 条の 36 で、**都道府県・政令指定都市・中核市の 129 団体**には契約が
義務付けられています（`tools/kansa/core_cities.py`。中核市は台帳に印が無いので
jis_code で持ちます）。ほかの市区町村は条例で任意に導入できます。

**個別外部監査は別の制度**です（住民の請求などを受けて個別の事項を監査する）。
混ぜないよう、探索・取得とも見分けて扱います。

## 会議録・例規集との違い

共通のベンダ系システムはありません。事務事業評価（[hyoka-url-survey.md](hyoka-url-survey.md)）と
同じく、各自治体が自前のページに PDF を置くので、ページの言葉で見分けます。
公開の形はおおむね次の階層です。

    ホームページ → 市政/行政情報 → 監査委員（監査事務局）→ 外部監査
    → 包括外部監査結果報告書（年度ごとの PDF）

## 台帳

列は会議録・例規集・事務事業評価と同じです（`jis_code` / `url` / `system_type` /
`crawl_status` / `exclusion_reason` / `exclusion_detail`）。全 1,794 自治体の行を
必ず持ち、まだ調べていない自治体は `unresolved` で置きます。BOM は付けません
（PHP 側で `jis_code` を引けなくなる）。

2026-09-17 時点の内訳は、`enabled` 133（義務付けの団体と、任意導入の区市）、
`review_required` 4、`unresolved` 1,657 です。

探索の確信度と台帳の状態の対応（`tools/kansa/build_kansa_registry.py`）:

| 確信度 | 根拠 | 台帳 |
| --- | --- | --- |
| `high` | リンク文字や題名が包括外部監査を名乗る | `enabled` |
| `medium` | 外部監査・監査結果のページの本文に「包括外部監査」がある | `review_required`（人の確認待ち） |
| `low` / `none` | 監査のページは見つかったが言い切れない／見つからない | `unresolved` |

義務付けの団体で見つからないものは、探索の側の問題として `exclusion_detail` に印を残します。

## 手順

```bash
# 探索（既定は義務付けの 129 団体。--all で全自治体）
python tools/kansa/discover_kansa_urls.py --mandatory --limit 10
python tools/kansa/discover_kansa_urls.py --codes 13000 14100 --verbose

# 探索結果の CSV から台帳を書き出す
python tools/kansa/build_kansa_registry.py --from-csv work/kansa/discovery_xxx.csv --dry-run

# 報告書の取得（台帳の enabled。--include-review で review_required も）
python tools/kansa/scrape_kansa_reports.py --slug 01100-sapporo-shi
python tools/kansa/scrape_kansa_reports.py --all --limit 5
```

取得物は `work/kansa/<slug>/` に置きます。`manifest.json` に文書ごとの原典 URL・
年度・種別・SHA・取得時刻、`source/` に PDF、`text/` に抜き出した本文
（頭に題名・年度・出典の行）。原典の URL と見つけたページは必ず残します。

取得元への配慮として、1 件ごとに間隔を空け（既定 1.5 秒）、一度取ったものは
30 日問い合わせ直さず、問い合わせるときも ETag / Last-Modified の条件付きにします。
同じホストで接続を続けて切られたら、そのホストを数時間休みます。

## いまの扱い

**手動の道具です。** 巡回（Celery）にも検索 index にも公開画面にも、まだつないで
いません。台帳の形の検査だけ `tools/tasks/test_registry_consistency.py` に入っています。
