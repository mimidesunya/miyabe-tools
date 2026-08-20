# YouTube アップロードツール（管理者専用）

`/youtube/`（`app/youtube/index.php`）。LINE でログインした管理者（`config.json` の
`ADMIN_LINE_IDS` 該当者）だけが使える。動画をアップロードすると、サーバー側で
音声を loudnorm 二段階で正規化してから YouTube へ投稿する。正規化の方式は kits の
`TypeScript/media/normalize-mp4-audio.ts` に合わせている。

## 構成

| パス | 役割 |
|---|---|
| `app/youtube/index.php` | 管理者向けアップロード画面（フォーム＋チャンク送信＋進捗ポーリング） |
| `app/youtube/api.php` | JSON/チャンク API（create / chunk / finalize / status）。管理者＋CSRF |
| `lib/youtube/runtime.php` | ジョブ管理、認可、ワーカー起動のヘルパー |
| `lib/youtube/worker.py` | ffmpeg 正規化 + YouTube レジューム アップロードのワーカー |

- 実行基盤: PHP コンテナ（`docker/php/Dockerfile` に `ffmpeg` と Google API の Python
  ライブラリを追加済み）。ワーカーは PHP から `nohup … &` で切り離して起動する。
- ジョブ領域: `work/youtube/jobs/<id>/`（本番では `/mnt/big` にマウント、Web 非公開）。
  アップロードは 8MiB のチャンクで送るので nginx/PHP のサイズ制限に当たらない。
- アップロード成功後、元動画と正規化ファイルは自動削除する。

## 認証（YouTube OAuth）

`data/youtube/oauth-token.json`（authorized_user 形式、`refresh_token` を含む）を置く。
kits と同じ Google OAuth クライアントを流用する。トークンは次で作る。

```bash
# kits で（ブラウザ同意が要る）
cd F:\dev\mimidesunya-private\kits
npm run auth
# → data/youtube-comment-ai/google-token.json が更新される
```

この `google-token.json` を risu の
`~/services/miyabe-tools/data/youtube/oauth-token.json` へ置く。`data/` は deploy の
同期対象外なので、手動で scp する（秘密ファイル）。`data/` 配下は nginx で画像・PDF 以外
404 にしてあるので、トークンは公開されない。

スコープは `youtube.upload` と `youtube.force-ssl`。アップロード先は、そのトークンが
認可されている YouTube チャンネル。

## 制限

- YouTube Data API のクォータ既定は 10,000/日、`videos.insert` は 1 回 1,600 なので
  実質 1 日 6 本程度が上限。
- 1 本の上限は 5 GiB（`api.php` の `YOUTUBE_MAX_SOURCE_BYTES`）。
- 入力は MP4（H.264 映像 + 音声）。映像は再エンコードせず、音声だけ AAC で作り直す。
- 既定の公開設定は「非公開」。公開予約（`publishAt`）は非公開のときだけ指定できる。
