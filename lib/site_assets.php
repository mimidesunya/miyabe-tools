<?php
declare(strict_types=1);

// 公開ページ共通の軽量 asset helper。favicon のようなサイト共通資産を一元管理する。

function site_asset_url(string $relativePath): string
{
    $normalized = trim(str_replace('\\', '/', $relativePath), '/');
    $publicPath = '/' . $normalized;
    $diskPath = dirname(__DIR__) . DIRECTORY_SEPARATOR . 'app' . DIRECTORY_SEPARATOR
        . str_replace('/', DIRECTORY_SEPARATOR, $normalized);
    $version = is_file($diskPath) ? (string)filemtime($diskPath) : '';
    return $version !== '' ? $publicPath . '?v=' . rawurlencode($version) : $publicPath;
}

function site_render_favicon_links(): string
{
    $faviconUrl = htmlspecialchars(site_asset_url('assets/favicon.svg'), ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
    return '<link rel="icon" type="image/svg+xml" href="' . $faviconUrl . '">' . "\n"
        . '    <link rel="shortcut icon" href="' . $faviconUrl . '">' . "\n"
        . '    <meta name="theme-color" content="#0f5c4d">';
}
