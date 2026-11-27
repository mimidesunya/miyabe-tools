# 行政文書（/gyosei/）

情報公開請求などで手に入れた行政文書を、PDF の画像と頁ごとの文字で読めるようにする場所。

- 2026-11-27: 1 件目として、川崎市の池上地区環境改善連絡会の開示（20 件・387 頁）を置いた。
  まだナビ・トップ・sitemap に出さず、頁は `noindex, nofollow`（`app/gyosei/index.php` の `GYOSEI_NOINDEX`）。
- 先の目標は 2 つ。`I:\行政文書` の共有の候補を全部置いて検索できるようにすること（索引・API・MCP）と、
  ほかの人から文書を受け付ける仕組み。どちらもまだ作っていない（下の「残り」）。

## 流れ

```
I:\行政文書\{国|自治体|その他}\...\{案件}\   元（case.json・PDF・_paged.md）。整理の決まりは I:\行政文書\README.md
        │  tools/gyosei/publish.json に書いた案件だけ
        ▼
python tools/gyosei/build_public.py           公開用の束を作る → work/gyosei/public/<id>/
        ▼
python deploy/sync_gyosei.py deploy.json      本番の services/miyabe-tools/data/gyosei/ へ rsync（--delete）
        ▼
https://tools.miya.be/gyosei/                 app/gyosei/index.php ＋ lib/gyosei.php
```

PDF は重く、元は手元の I: にしか無いので、リポジトリにも `deploy.py` の同期にも入れない（`data/` は git の外）。

## 公開する案件の台帳（`tools/gyosei/publish.json`）

| 項目 | 中身 |
|---|---|
| `id` | URL に出す名前。英小文字・数字・ハイフン。`{団体コード}-{請求日}-{短い英字}`（例 `14130-2026-10-15-ikegami`）。一度出したら変えない |
| `case_dir` | `I:\行政文書` からの案件フォルダ |
| `summary` | 一覧と案件の頁に出す説明（何の文書か。飾らない） |
| `omit` | 出さないファイルと、そのわけ（画面にも「載せていません」と出す） |

## 公開用の束（`tools/gyosei/build_public.py`）

- **PDF は頁ごとに画像にして作り直す**（200dpi、白黒の頁はグレーの JPEG、色のある頁はカラーの JPEG、品質 75）。
  墨消しの下の文字・文字の層・注釈・作成者などのメタデータ（Word の PDF には職員の名前が入っている）を残さないため。
  書き出した後に、文字の層と注釈が無いことを確かめ、あれば止まる。
- **出さない物**: `omit` のファイル、`_paged.md` 以外の付属物。請求書と、費用の納入通知（請求者の住所・電話番号が載る）は出さない。
  決定通知の宛名（請求者の氏名）は出る。
- 文字起こし（`_paged.md`）は `### -- Begin Page N --` で頁に分けて `<nn>.json` にする。黒塗りの頁は元の文字起こしで
  「（頁の大部分が黒塗り）」としてあり、読みのかけらは残っていない（I: 側で消してある）。
- 文字起こしに郵便番号（〒）があれば `[確認]` と出す。請求者の住所でないかを目で見る。
- `case.json` から出す項目は `agency`・`agency_code`・`body`・`route`・`request_kind`・`legal_basis`・`requested_on`・
  `title`・`request_text`・`decisions`・`appeals`・`status` だけ。`notes`・`requester`・`source_dirs`・`public` は出さない。
- 文書の並びは種類（決定通知 → 送付状 → 開示文書 …、`KIND_ORDER`）→ 日付 → 別紙は後ろ → 補足（数は数として並べる）。
  番号（`01.pdf`…）は並びで振るので、文書を足すと番号がずれる。公開した後に足すときは気をつける（残り）。
- 元の PDF の SHA-256 を `work/gyosei/build_state.json` に控え、変わっていなければ作り直さない。

束の中身（本番は `data/gyosei/<id>/`）:

| ファイル | 中身 | 出し方 |
|---|---|---|
| `case.json` | 案件の情報・文書の一覧（ファイル・名前・日付・種類・頁数・大きさ・SHA-256）・出さなかった物・公開した日 | PHP だけが読む |
| `<nn>.pdf` | 作り直した PDF | nginx が `/data/gyosei/<id>/<nn>.pdf` で出す（`/data/` の pdf の規則） |
| `<nn>.json` | 頁ごとの文字起こし（Markdown） | PHP だけが読む（nginx は `/data/` の json を 404） |

## 画面（`app/gyosei/index.php`）

- `/gyosei/` 案件の一覧（請求日の新しい順）。
- `/gyosei/?case=<id>` 案件。請求した日・種類・請求先・根拠・請求した文書（請求書の文言）、決定（日付・番号・結果・出さなかった部分）、
  文書の一覧（種類ごと。「読む」と「PDF」）、載せ方（作り直したこと・機械の読み取り・出さなかった物とわけ）。
- `/gyosei/?case=<id>&doc=<nn>` 文書。PDF の保存（`download` の名前は `{団体}_{件名}_{元のファイル名}.pdf`）と、
  頁ごとの文字（各頁に「画像で見る」= `PDF#page=N`）。前後の文書へ移れる。
- 文字起こしの Markdown は `gyosei_render_ocr()` が見出し・表・太字・改行だけを HTML にする。ほかはすべてエスケープ。
- 手元では `.claude/launch.json` の `php-site` で、`work/gyosei/public` をそのまま読む（`dev/preview/router.php` が PDF も出す）。
- 試験は `php tests/gyosei_test.php`。

## 残り

1. **ナビ・トップに出す、noindex を外す**: ユーザーが決める。外すときは `GYOSEI_NOINDEX` を false にし、sitemap に足す。
   PDF そのものには `X-Robots-Tag` を付けていない（頁が nofollow なので、リンクはたどられない）。
2. **検索**: 文字起こしを OpenSearch の索引に入れ、`/api/` と MCP の道具を作る（MCP を先に。`doc/mcp.md`）。
3. **案件を増やす**: `I:\行政文書` の case.json の `public` が「可」の物から。「要確認」は 1 件ずつ見る。
   他人名義の請求（`requester`）は、請求者に断ってから。文書の番号がずれないよう、公開した後に足す文書は末尾に付ける仕組みにする。
4. **受け付ける仕組み**: ほかの人が文書を送れるようにする。送られた物は公開の前に人が見る
   （住所・墨消し漏れ・提供者が特定されないか）。置き場・受付の画面・確認の手順を決める。
