<?php
declare(strict_types=1);

// 公開ページ共通の軽量 asset helper。favicon のようなサイト共通資産を一元管理する。

function site_asset_disk_path(string $normalized): string
{
    $relative = str_replace('/', DIRECTORY_SEPARATOR, $normalized);
    $candidates = [];
    $documentRoot = (string)($_SERVER['DOCUMENT_ROOT'] ?? '');
    if ($documentRoot !== '') {
        $candidates[] = rtrim($documentRoot, '/\\') . DIRECTORY_SEPARATOR . $relative;
    }
    // ローカル開発はリポジトリ直下の app/、本番コンテナは /var/www/lib と並ぶ html/。
    $candidates[] = dirname(__DIR__) . DIRECTORY_SEPARATOR . 'app' . DIRECTORY_SEPARATOR . $relative;
    $candidates[] = dirname(__DIR__) . DIRECTORY_SEPARATOR . 'html' . DIRECTORY_SEPARATOR . $relative;
    foreach ($candidates as $candidate) {
        if (is_file($candidate)) {
            return $candidate;
        }
    }
    return '';
}

function site_asset_url(string $relativePath): string
{
    $normalized = trim(str_replace('\\', '/', $relativePath), '/');
    $publicPath = '/' . $normalized;
    // asset に filemtime を ?v= として付け、nginx の長期 immutable キャッシュと両立させる。
    // 解決に失敗して版なし URL を返すと更新がブラウザに届かなくなるので、候補は複数見る。
    $diskPath = site_asset_disk_path($normalized);
    $version = $diskPath !== '' ? (string)filemtime($diskPath) : '';
    return $version !== '' ? $publicPath . '?v=' . rawurlencode($version) : $publicPath;
}

function site_base_url(): string
{
    return 'https://tools.miya.be';
}

// description / canonical / OGP をまとめて出力する。$canonicalPath は「/search/」のような
// サイト内パス（必要ならクエリ付き）を渡す。
function site_render_page_meta(string $title, string $description, string $canonicalPath): string
{
    $h = static fn(string $value): string => htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
    $canonicalUrl = site_base_url() . $canonicalPath;
    $imageUrl = site_base_url() . '/assets/ken-dog.png';
    return '<meta name="description" content="' . $h($description) . '">' . "\n"
        . '    <link rel="canonical" href="' . $h($canonicalUrl) . '">' . "\n"
        . '    <meta property="og:site_name" content="自治体マップ">' . "\n"
        . '    <meta property="og:type" content="website">' . "\n"
        . '    <meta property="og:title" content="' . $h($title) . '">' . "\n"
        . '    <meta property="og:description" content="' . $h($description) . '">' . "\n"
        . '    <meta property="og:url" content="' . $h($canonicalUrl) . '">' . "\n"
        . '    <meta property="og:image" content="' . $h($imageUrl) . '">' . "\n"
        . '    <meta name="twitter:card" content="summary">';
}

function site_render_favicon_links(): string
{
    $faviconUrl = htmlspecialchars(site_asset_url('assets/ken-dog.png'), ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
    return '<link rel="icon" type="image/png" href="' . $faviconUrl . '">' . "\n"
        . '    <link rel="shortcut icon" href="' . $faviconUrl . '">' . "\n"
        . '    <link rel="apple-touch-icon" href="' . $faviconUrl . '">' . "\n"
        . '    <meta name="theme-color" content="#173845">';
}

// ロゴとサイト名はトップへ、運営者名は tatsuhiko.miya.be へ。入れ子アンカーを避けるため
// 外側はアンカーではなく span にする（.brand img / .brand > span のセレクタは維持）。
function site_render_brand(string $href = '/'): string
{
    $brandHref = htmlspecialchars($href, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
    $mascotUrl = htmlspecialchars(site_asset_url('assets/ken-dog.png'), ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
    return '<span class="brand">'
        . '<a href="' . $brandHref . '" aria-label="自治体マップ トップへ"><img src="' . $mascotUrl . '" alt=""></a>'
        . '<span>'
        . '<a href="' . $brandHref . '"><strong>自治体マップ</strong></a>'
        . '<small><a href="https://tatsuhiko.miya.be/" target="_blank" rel="noopener">宮部たつひこ</a></small>'
        . '</span>'
        . '</span>';
}

// ChatGPTのプラグイン名がサイト名と異なるため、審査や利用者が同一サービスだと
// 確認できるよう、規約・プライバシー・サポートの各ページで名称の対応を明示する。
function site_render_service_identity(): string
{
    return '<section class="docs-section">' . "
"
        . '            <h2>サービスの名称</h2>' . "
"
        . '            <p>' . "
"
        . '                本サイト「自治体マップ」（<a href="https://tools.miya.be/">https://tools.miya.be/</a>）は、宮部たつひこが運営しています。' . "
"
        . '                本サイトの検索機能をChatGPTから利用するためのプラグイン' . "
"
        . '                「日本自治体会議録例規集横断調査」も、同じ運営者による同一のサービスです。' . "
"
        . '                プラグインは本サイトのMCPサーバー（<code>https://tools.miya.be/mcp</code>）に接続しており、' . "
"
        . '                本ページの内容はプラグインの利用にもそのまま適用されます。' . "
"
        . '            </p>' . "
"
        . '        </section>';
}
