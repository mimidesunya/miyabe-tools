# YouTubeアップロード移設

移設先は https://tatsuhiko.miya.be/youtube/ 。サイト専用管理者パスワードを使う。コード正本は kawasaki_site の www/youtube と lib/youtube。

app/youtube/index.php は303案内、api.phpは410 JSON、プロフィールのリンクは移設先。旧lib/youtubeは復旧用に維持。検索API・MCP・LINE認証・公開用依存パッケージは変更しない。

未配備。先に新サイトの本番ログイン・OAuth設定とアップロードを確認してから旧入口を切り替える。進行中ジョブ・旧トークン・workデータは削除しない。移設先 docs/admin-youtube.md に検証と配備前の残件を記録した。
