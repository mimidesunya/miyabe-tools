# 宮部たつひこマップ

川崎市で活動する宮部たつひこの現在地を表示する特設ページ。
位置情報は本人が管理ページで「GPS による位置提供」を ON にしている間だけ公開される。

## 画面と API

| パス | 内容 |
| --- | --- |
| `/tatsuhiko-map/` | 公開ページ。地理院タイル + Leaflet の地図に現在地マーカーを表示。30 秒ごとに API をポーリング |
| `/tatsuhiko-map/admin.php` | 管理ページ（本人専用・noindex）。パスワードログイン、位置提供の ON/OFF、GPS 送信 |
| `/tatsuhiko-map/api.php` | GET: 公開用の現在地 JSON。OFF 中は座標を返さない。POST: 管理操作（要セッション + CSRF） |

## 認証

- 本人しか使わないためユーザー名はなく、パスワードのみ。
- パスワードは `data/config.json` の `TATSUHIKO_MAP_PASSWORD_HASH`（`password_hash()` の出力）
  または同名の環境変数で設定する。平文は保存しない。

  ```sh
  php -r "echo password_hash('パスワード', PASSWORD_DEFAULT), PHP_EOL;"
  ```

- ログイン失敗が 10 分間に 8 回続くと一時的にロックする
  （`data/tatsuhiko_map/login_failures.json`）。
- セッションキーは `tatsuhiko_map_admin`。POST 操作は CSRF トークン
  （`X-Tmap-Csrf` ヘッダーまたは `csrf` フィールド）を必須とする。

## 位置情報の扱い

- 状態は `data/tatsuhiko_map/state.json` に保存する
  （`sharing` / `lat` / `lng` / `accuracy` / `updated_at`）。
- OFF に切り替えると、保存済みの座標があっても公開 API は座標を一切返さない。
- GPS の取得はブラウザの `watchPosition` で行うため、位置が更新されるのは
  管理ページを開いている間だけ。送信間隔は最短 15 秒。
- 管理ページから「保存済みの位置を消去」でサーバー上の座標も消せる。

## GET レスポンス例

```json
{ "sharing": true, "location": { "lat": 35.5309, "lng": 139.7029, "accuracy": 12.5, "updated_at": "2026-07-10T03:00:00+00:00" } }
```

OFF 中:

```json
{ "sharing": false, "location": null }
```
