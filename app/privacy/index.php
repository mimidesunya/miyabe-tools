<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'site_assets.php';

function privacy_h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function privacy_asset_url(string $relativePath): string
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
    <title>プライバシーポリシー - 宮部たつひこの自治体調査</title>
    <?php echo site_render_favicon_links(); ?>
    <link rel="stylesheet" href="<?php echo privacy_h(privacy_asset_url('css/search.css')); ?>">
</head>
<body>
<div class="app-shell docs-shell">
    <header class="topbar">
        <a class="brand" href="/">宮部たつひこの自治体調査</a>
        <nav class="page-links" aria-label="関連ページ">
            <a href="/">横断検索</a>
            <a href="/api-guide/">API解説</a>
            <a href="/status/">処理状況</a>
        </nav>
    </header>

    <main class="docs-page">
        <section class="docs-hero">
            <p class="kicker">Privacy Policy</p>
            <h1>プライバシーポリシー</h1>
            <p>
                このページは、「宮部たつひこの自治体調査」と、その検索APIをAIやGPTから利用する場合の
                個人情報の取り扱いについて説明するものです。
            </p>
            <p>制定日: 2026年5月18日</p>
        </section>

        <section class="docs-section">
            <h2>取得する情報</h2>
            <p>
                本サイトは、利用者の氏名、メールアドレス、住所、電話番号など、利用者を直接識別する情報の入力を求めていません。
                アカウント登録、ログイン、問い合わせフォーム、決済機能も設けていません。
            </p>
            <p>
                検索APIでは、利用者が指定した検索語、検索対象、都道府県、自治体、ページ番号などのリクエスト内容を、
                検索結果を返すために処理します。
            </p>
        </section>

        <section class="docs-section">
            <h2>アクセスログ</h2>
            <p>
                サーバーの通常の運用として、アクセス日時、リクエストURL、IPアドレス、User-Agent、応答ステータスなどが
                アクセスログに記録される場合があります。これらは障害対応、不正アクセス対策、負荷対策、サービス改善のために利用します。
            </p>
        </section>

        <section class="docs-section">
            <h2>Cookieと外部トラッキング</h2>
            <p>
                現在、検索ページおよび検索APIでは、広告配信、行動追跡、第三者解析のためのCookieやトラッキングタグを使用していません。
            </p>
        </section>

        <section class="docs-section">
            <h2>公開情報の取り扱い</h2>
            <p>
                本サイトとAPIは、自治体などが公開している会議録、例規集、関連情報を検索しやすくするためのものです。
                APIの検索結果や詳細APIの本文には、元の公開資料に含まれる情報が表示される場合があります。
            </p>
            <p>
                公開資料そのものの内容については、原則として各自治体または元の公開元が管理しています。
            </p>
        </section>

        <section class="docs-section">
            <h2>AIやGPTからの利用</h2>
            <p>
                GPT Actionsなどから本APIを利用する場合、AIサービスの提供者が検索語やAPIへのリクエスト内容を処理することがあります。
                AIサービス側でのデータの取り扱いは、それぞれのサービスの規約やプライバシーポリシーをご確認ください。
            </p>
        </section>

        <section class="docs-section">
            <h2>第三者提供</h2>
            <p>
                本サイトは、法令に基づく場合を除き、利用者を直接識別する個人情報を第三者へ販売または提供することはありません。
            </p>
        </section>

        <section class="docs-section">
            <h2>変更</h2>
            <p>
                本ポリシーは、サービス内容の変更や法令上の必要に応じて改定することがあります。
                重要な変更がある場合は、このページで分かるようにします。
            </p>
        </section>

        <section class="docs-section">
            <h2>連絡先</h2>
            <p>
                本サイトに関する連絡は、運営者の公開している連絡先、または
                <a href="https://tatsuhiko.miya.be/" target="_blank" rel="noopener">tatsuhiko.miya.be</a>
                に掲載されている連絡方法をご利用ください。
            </p>
        </section>
    </main>
</div>
</body>
</html>
