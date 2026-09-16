<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'site_assets.php';

function support_h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function support_asset_url(string $relativePath): string
{
    $normalized = trim(str_replace('\\', '/', $relativePath), '/');
    $publicPath = '/search/assets/' . $normalized;
    $diskPath = dirname(__DIR__) . DIRECTORY_SEPARATOR . 'search' . DIRECTORY_SEPARATOR . 'assets' . DIRECTORY_SEPARATOR
        . str_replace('/', DIRECTORY_SEPARATOR, $normalized);
    $version = is_file($diskPath) ? (string)filemtime($diskPath) : '';
    return $version !== '' ? $publicPath . '?v=' . rawurlencode($version) : $publicPath;
}
?><!doctype html>
<html lang="ja">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>サポート・お問い合わせ｜自治体マップ</title>
    <?php echo site_render_page_meta(
        'サポート・お問い合わせ｜自治体マップ',
        '自治体マップの使い方、不具合、掲載内容の修正、AI連携（MCP）についての問い合わせ窓口です。',
        '/support/'
    ); ?>
    <?php echo site_render_favicon_links(); ?>
    <link rel="stylesheet" href="<?php echo support_h(support_asset_url('css/search.css')); ?>">
</head>
<body>
<div class="app-shell docs-shell">
    <header class="topbar">
        <?php echo site_render_brand('/'); ?>
        <nav class="page-links" aria-label="関連ページ">
            <a href="/">地図から探す</a>
            <a href="/search/">記録を検索</a>
            <a href="/api-guide/">AIから使う（MCP）</a>
            <a href="/privacy/">プライバシー</a>
            <a href="/terms/">利用規約</a>
        </nav>
    </header>

    <main class="docs-page">
        <section class="docs-hero">
            <p class="kicker">Support</p>
            <h1>サポート・お問い合わせ</h1>
            <p>
                使い方、不具合、掲載内容の修正、AI連携（MCP）のことは、
                このページの窓口へお寄せください。
            </p>
        </section>

        <?php echo site_render_service_identity(); ?>

        <section class="docs-section">
            <h2>連絡方法</h2>
            <p>
                連絡は次のどちらからでもかまいません。
            </p>
            <ul>
                <li>
                    <a href="https://tatsuhiko.miya.be/contact.html" target="_blank" rel="noopener">お問い合わせフォーム</a>
                    （運営者サイト内）
                </li>
                <li>
                    <a href="https://x.com/K_JINKEN" target="_blank" rel="noopener">X（旧Twitter）@K_JINKEN</a>
                </li>
            </ul>
            <p>
                個人で運営しています。内容を読んだうえで順に返信しますが、
                返信までに日数をいただくことがあります。
            </p>
        </section>

        <section class="docs-section">
            <h2>不具合を知らせるとき</h2>
            <p>
                次のことを書き添えてもらえると、原因を早く見つけられます。
            </p>
            <ul>
                <li>問題が起きたページのURL、または検索した語</li>
                <li>対象の自治体名と文書の種類（会議録か例規集か）</li>
                <li>こうなると思っていた結果と、実際に出た結果</li>
                <li>起きた日時</li>
                <li>AIから使っている場合は、使ったツール名（search_minutes、search_reiki、get_municipal_document）</li>
            </ul>
        </section>

        <section class="docs-section">
            <h2>よくある質問</h2>
            <h3>自分の自治体が見つからない</h3>
            <p>
                取得元のURLがまだ分からない自治体や、形式に対応できていない自治体は出てきません。
                いまの状況は<a href="/status/">収集・公開状況</a>のページで自治体ごとに見られます。
            </p>
            <h3>内容が古い、または取得元と違う</h3>
            <p>
                取得元を定期的に巡回して取り込んでいるので、取得元が更新してからここに出るまでに時間差があります。
                取り込みの失敗や文字の読み取り間違いで、取得元と食い違うこともあります。
                正確さが要るときは、検索結果に出る原典のURLから元の文書を確かめてください。
            </p>
            <h3>掲載を止めてほしい、内容を直してほしい</h3>
            <p>
                取得元の権利者の方からの申し出は、上の窓口へご連絡ください。
                対象の文書のURLを添えてもらえれば、内容を確かめて対応します。
            </p>
            <h3>AI（MCP）からの使い方を知りたい</h3>
            <p>
                接続手順は<a href="/api-guide/">AIから使う（MCP）</a>のページにまとめています。
                MCPサーバーのエンドポイントは <code>https://tools.miya.be/mcp</code> で、認証は不要です。
            </p>
            <h3>大量のデータを取得したい</h3>
            <p>
                こちらと取得元のサーバーに負担がかかるので、まるごと取得するのは控えてください。
                研究や報道で大量に要るときは、目的を添えて相談してください。
            </p>
        </section>

        <section class="docs-section">
            <h2>関連ページ</h2>
            <ul>
                <li><a href="/terms/">利用規約</a> — 使うときの条件</li>
                <li><a href="/privacy/">プライバシーポリシー</a> — 個人情報の扱い</li>
                <li><a href="/status/">収集・公開状況</a> — 自治体ごとの取り込み具合</li>
            </ul>
        </section>
    </main>
</div>
</body>
</html>
