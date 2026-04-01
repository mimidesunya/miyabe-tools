# 複数自治体対応

ポスター掲示場、会議録、例規集の3機能を、自治体スラッグ単位で切り替えるための共通設計です。

## 基本方針

- 自治体の定義は `data/config.json` の `MUNICIPALITIES` に集約する
- 全国自治体コードの基礎マスタは `work/municipalities/municipality_master.tsv` を使う
- 画面は `slug` ごとにデータ参照先を切り替える
- 新しく追加する `slug` は `自治体コード-ローマ字名称` を推奨する
- 未対応機能は別自治体のデータを流用せず、「準備中」と表示する

## URL ルール

- 掲示場: `/boards/{slug}/`
- 例規集: `/reiki/?slug={slug}`
- 会議録: `/gijiroku/?slug={slug}`
- `slug` に自治体コードだけ、自治体名ローマ字だけ、自治体名そのものを渡した場合も、サーバー側で canonical slug に解決し、GET 画面は正規 URL へリダイレクトする

## 設定例

```json
{
  "DEFAULT_SLUG": "kawasaki-shi",
  "MUNICIPALITIES": {
    "kawasaki-shi": {
      "name": "川崎市",
      "boards": {
        "enabled": true,
        "allow_offset": false
      },
      "reiki": {
        "enabled": true,
        "source_dir": "reiki/kawasaki-shi/source",
        "classification_dir": "reiki/kawasaki-shi/json",
        "db_path": "reiki/kawasaki-shi/ordinances.sqlite"
      },
      "gijiroku": {
        "enabled": true,
        "assembly_name": "川崎市議会",
        "data_dir": "gijiroku/kawasaki-shi",
        "db_path": "gijiroku/kawasaki-shi/minutes.sqlite"
      }
    }
  }
}
```

## 追加手順

1. `MUNICIPALITIES` に新しい `slug` を追加する
   例: `01202-hakodate`
2. `boards` / `reiki` / `gijiroku` の各機能で `enabled` とデータパスを定義する
3. 必要なデータファイルを `data/` 配下に配置する
4. 例規集や会議録は対応する初期化スクリプトを `--slug` 付きで実行する

## 実装ポイント

- PHP 側は `lib/municipalities.php` が共通レジストリ
- 掲示場の権限 (`allow_offset`) も自治体設定から読む
- 例規集のフィードバック集計は `slug` を含むキーで分離する
- トップページは `/api/home.php` が visible な自治体・機能だけを JSON で返し、`app/assets/js/home.js` がその結果を描画する
- 空の自治体カードは API 側で除外し、クライアント側では「返ってきた配列だけ」を描画する
- `/api/home.php` は短時間のサーバー側キャッシュを持ち、同じ集計結果を数秒だけ使い回してトップの読み込みを軽くする
