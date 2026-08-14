# ドメイン境界

Miyabe Tools は、同じ自治体識別子を使う二つの独立領域を同一リポジトリで運用します。

## 1. 自治体文書

会議録 `minutes` と例規集 `reiki` を収集・正規化し、OpenSearch alias、
`/api/search`、`/api/document`、MCPから読み取り専用で提供します。

主な配置:

- `tools/gijiroku/`, `tools/reiki/`, `tools/search/`
- `app/gijiroku/`, `app/reiki/`, `app/search/`, `app/api/`
- `lib/opensearch_search.php`
- `data/reiki/`, `work/gijiroku/`, `work/reiki/`

## 2. 選挙ポスター掲示場

位置情報、作業進捗、LINE認証、ユーザー管理を扱う更新可能なアプリケーションです。
自治体文書の検索・収集処理には参加しません。

主な配置:

- 正式実装: `domains/election_poster_boards/`
- 公開URLアダプター: `app/boards/`, `app/line/`
- 静的公開アセット: `app/boards/assets/`
- 実行時データ: `data/boards/`
- 入力TSVと旧CLI: `dev/boards/`

## 共有してよいもの

- 全国自治体コード、自治体名、都道府県
- canonical slug とそのalias解決
- 共通のサイトブランド・静的アセットURL生成
- トップページ用の読み取り専用カタログ集約

トップページの `/api/home.php` は両領域の概要を表示するポータル用アダプターです。
掲示場データを文書検索APIやOpenSearchへ統合する境界ではありません。

## 禁止する依存

- 掲示場から会議録・例規集スクレイパ、OpenSearch、MCPへの依存
- `/api/search` または `/api/document` への掲示場フィールド追加
- 会議録・例規集のデータディレクトリへの掲示場SQLite配置
- 文書収集ジョブによる掲示場タスク・ユーザーDBの更新

## 互換性

- `/boards/*` と `/line/*` は維持する
- `lib/session.php` は旧関数名の互換アダプターとして維持する
- `dev/boards/*.py` は新しいドメインCLIへの互換入口として維持する
- `data/users.sqlite` は `data/boards/users.sqlite` への移行期間中だけ読み取る
- `/api/search` と `/api/document` の契約は変更しない
