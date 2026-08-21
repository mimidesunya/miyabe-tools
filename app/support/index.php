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
        '自治体マップの使い方、不具合の報告、掲載内容の修正依頼、AI連携（MCP）に関するお問い合わせ窓口です。',
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
                自治体マップの使い方、不具合の報告、掲載内容の修正依頼、AI連携（MCP）に関するご質問は、
                このページの窓口へお寄せください。
            </p>
        </section>

        <?php echo site_render_service_identity(); ?>

        <section class="docs-section">
            <h2>連絡方法</h2>
            <p>
                運営者への連絡は、次のいずれかをご利用ください。
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
                本サイトは個人が運営しています。内容を確認したうえで順次返信しますが、
                返信までに日数をいただく場合があります。
            </p>
        </section>

        <section class="docs-section">
            <h2>不具合を報告する場合</h2>
            <p>
                次の情報を書き添えていただけると、原因の特定が早くなります。
            </p>
            <ul>
                <li>問題が起きたページのURL、または検索した語</li>
                <li>対象の自治体名と文書の種類（会議録か例規集か）</li>
                <li>期待した結果と、実際に表示された結果</li>
                <li>発生した日時</li>
                <li>AIから利用している場合は、使ったツール名（search_minutes、search_reiki、get_municipal_document）</li>
            </ul>
        </section>

        <section class="docs-section">
            <h2>よくあるお問い合わせ</h2>
            <h3>自分の自治体が見つからない</h3>
            <p>
                収集元のURLを特定できていない自治体や、対応できていない形式の自治体は表示されません。
                現在の収集・公開状況は<a href="/status/">収集・公開状況</a>のページで自治体ごとに確認できます。
            </p>
            <h3>内容が古い、または収集元と違う</h3>
            <p>
                本サイトは収集元を定期的に巡回して取り込んでいるため、収集元の更新が反映されるまで時間差があります。
                また、取り込みの失敗や文字認識の誤りにより、収集元と一致しない場合があります。
                正確さが必要な場合は、検索結果に表示される原典URLから収集元の文書をご確認ください。
            </p>
            <h3>掲載を停止してほしい、内容を修正してほしい</h3>
            <p>
                収集元の権利者の方からの申し出は、上記の窓口までご連絡ください。
                対象の文書のURLを添えていただければ、内容を確認のうえ対応します。
            </p>
            <h3>AI（MCP）からの使い方を知りたい</h3>
            <p>
                接続手順は<a href="/api-guide/">AIから使う（MCP）</a>のページにまとめています。
                MCPサーバーのエンドポイントは <code>https://tools.miya.be/mcp</code> で、認証は不要です。
            </p>
            <h3>大量のデータを取得したい</h3>
            <p>
                本サイトや収集元のサーバーへの負荷を避けるため、網羅的な取得はご遠慮いただいています。
                研究や報道などで大量のデータが必要な場合は、目的を添えてご相談ください。
            </p>
        </section>

        <section class="docs-section">
            <h2>関連ページ</h2>
            <ul>
                <li><a href="/terms/">利用規約</a> — 本サイトを利用する際の条件</li>
                <li><a href="/privacy/">プライバシーポリシー</a> — 個人情報の取り扱い</li>
                <li><a href="/status/">収集・公開状況</a> — 自治体ごとの収集状況</li>
            </ul>
        </section>
    </main>
</div>
</body>
</html>
