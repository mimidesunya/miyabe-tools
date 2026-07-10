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

function site_render_favicon_links(): string
{
    $faviconUrl = htmlspecialchars(site_asset_url('assets/favicon.svg'), ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
    return '<link rel="icon" type="image/svg+xml" href="' . $faviconUrl . '">' . "\n"
        . '    <link rel="shortcut icon" href="' . $faviconUrl . '">' . "\n"
        . '    <meta name="theme-color" content="#0f5c4d">';
}
