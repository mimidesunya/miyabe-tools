# 選挙ドットコム下書き投稿ツール

政治家向けボネクタ管理画面へ、Markdown原稿と画像を入力するPlaywright CLIです。

安全上の方針:

- 認証情報はGit管理外のルート `secret.json` からだけ読み、コマンドラインやログへ出しません。
- Cookie等は `work/` 内の専用ブラウザープロファイルに保持します。
- 既定では投稿画面への入力までで、保存しません。
- `--save-draft` を付けても、「下書き保存」と明記されたボタンだけを押します。
- 「公開」「投稿」ボタンは操作しません。
- 画面調査ファイルには入力値・Cookieを含めません。

## セットアップ

既存の `.venv311` にはPlaywrightが入っています。新しく環境を作る場合は次の通りです。

```powershell
py -3.11 -m venv .venv311
.\.venv311\Scripts\python.exe -m pip install -r dev\requirements\senkyo-com.txt
```

既定ではWindowsにインストール済みのMicrosoft Edgeを使います。Chromiumを使う場合だけ次も実行します。

```powershell
.\.venv311\Scripts\python.exe -m playwright install chromium
```

## 認証情報

ルートの `secret.json` に次の構造で設定します。このファイルは `.gitignore` 対象です。

```json
{
  "go2senkyo": {
    "cmsUrl": "https://www.go2senkyo.com/管理画面の入口",
    "id": "ログインID",
    "password": "パスワード"
  }
}
```

値をREADMEやシェル履歴へ貼り付けないでください。

## 1. 自動ログインを確認する

```powershell
.\.venv311\Scripts\python.exe tools\senkyo_com\post_draft.py login
```

`secret.json` の `go2senkyo.cmsUrl` を開き、`id` と `password` で自動ログインします。
ログイン状態は `work/senkyo-com/browser-profile/` に保存され、Gitには入りません。

この手順を省略して `draft` を実行しても、未ログインなら自動ログインして投稿画面の探索を続けます。
サイト側の変更などで自動ログインできない場合だけ `--manual-login` を指定します。
`--manual-login` を付けた場合は `secret.json` を読みません。

新規投稿URLに含まれるアカウント番号は、ログイン後の「活動記録を作成」リンクから自動取得します。
通常は `--editor-url` やセレクター設定を指定する必要はありません。

## 2. 投稿画面を調査する

管理画面の変更などで投稿画面を自動検出できない場合に使います。

```powershell
.\.venv311\Scripts\python.exe tools\senkyo_com\post_draft.py inspect
```

ブラウザーでブログの新規投稿画面まで移動してEnterを押すと、フォームの名前・ラベル・
プレースホルダーだけを `work/senkyo-com/artifacts/form-controls.json` に保存します。

必要なら [selectors.example.json](selectors.example.json) をコピーし、実際の入力欄に合わせます。

## 3. 原稿だけを確認する

サイトへ接続せず、Markdown変換結果を確認します。
この操作では `secret.json` は不要です。

```powershell
.\.venv311\Scripts\python.exe tools\senkyo_com\post_draft.py draft `
  --article work\senkyo-com\drafts\2026-07-07-kawasaki-media-lawsuits.md `
  --image "F:\history\2026\DCIM\07\PXL_20260701_051437427.jpg" `
  --dry-run
```

## 4. 投稿画面へ入力する

この段階ではまだ保存しません。入力後のブラウザーとスクリーンショットを確認します。

```powershell
.\.venv311\Scripts\python.exe tools\senkyo_com\post_draft.py draft `
  --article work\senkyo-com\drafts\2026-07-07-kawasaki-media-lawsuits.md `
  --image "F:\history\2026\DCIM\07\PXL_20260701_051437427.jpg"
```

自動検出できない場合は、投稿画面のURLを明示します。

```powershell
.\.venv311\Scripts\python.exe tools\senkyo_com\post_draft.py draft `
  --article work\senkyo-com\drafts\2026-07-07-kawasaki-media-lawsuits.md `
  --image "F:\history\2026\DCIM\07\PXL_20260701_051437427.jpg" `
  --editor-url "ログイン後の新規投稿画面URL" `
  --selector-config tools\senkyo_com\selectors.local.json
```

`selectors.local.json` にアカウント情報を書かないでください。ローカル専用にする場合は `work/`
配下へ置くのが安全です。

同じタイトルの記事が一覧にある場合は、二重投稿を避けるため新規作成を中止し、既存記事の編集URLを表示します。
意図的に同名記事を新しく作る場合だけ `--allow-duplicate` を付けてください。

## 5. 下書き保存する

入力内容をブラウザーで確認した後だけ、`--save-draft` を追加します。

```powershell
.\.venv311\Scripts\python.exe tools\senkyo_com\post_draft.py draft `
  --article work\senkyo-com\drafts\2026-07-07-kawasaki-media-lawsuits.md `
  --image "F:\history\2026\DCIM\07\PXL_20260701_051437427.jpg" `
  --save-draft
```

実際のボタンが「下書き保存」以外なら、公開ボタンでないことを確認して明示します。

```powershell
  --save-draft --draft-button-text "下書きとして保存"
```

保存後は成功メッセージ（「作成しました」等）または画面遷移を最大15秒待って確認し、
確認できない場合は終了コード2で失敗します。保存後の画面は
`work/senkyo-com/artifacts/saved-draft.png` に残ります。

## 6. 全自動で下書きを作成する

内容確認済みの原稿を、対話なし・ブラウザー非表示で一気に下書き保存します。

```powershell
.\.venv311\Scripts\python.exe tools\senkyo_com\post_draft.py draft `
  --article work\senkyo-com\drafts\2026-07-07-kawasaki-media-lawsuits.md `
  --image "F:\history\2026\DCIM\07\PXL_20260701_051437427.jpg" `
  --save-draft --no-wait --headless
```

タイトル・本文（見出し・箇条書き・太字・リンク対応）・サムネイル画像の入力と
下書き保存までを自動で行います。保存されるのはあくまで下書きで、公開はされません。
画像は選挙ドットコムの現在の入力条件に合わせ、13MB以下のJPEG・PNG・GIFに対応します。

## 7. 既存記事を更新する

`update` コマンドで、既存記事の編集画面へタイトル・本文・画像を入力し直します。
`--post-url` には管理画面の記事編集URL（`…/posts/<記事ID>/edit`）を指定します。
編集画面を直接開けない場合はエラーになり、新規投稿画面へ誤って入力することはありません。

```powershell
.\.venv311\Scripts\python.exe tools\senkyo_com\post_draft.py update `
  --article work\senkyo-com\drafts\2026-05-03-kofu-dowa-loans-update.md `
  --post-url "https://www.go2senkyo.com/cms/politicians/6880/posts/1367869-/edit" `
  --save-draft --no-wait --headless
```

`draft` と同じく、既定では入力までで保存せず、`--save-draft` を付けたときだけ
「下書き保存」ボタンを押します。押すのは「下書き保存」と明記されたボタンだけなので、
公開中の記事の「更新」（公開反映）ボタンは操作しません。

公開はこのツールの対象外です。公開する場合は管理画面で内容を最終確認して手動で行います。
