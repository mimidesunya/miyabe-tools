<?php
declare(strict_types=1);

require_once dirname(__DIR__, 3) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'opensearch_search.php';
require_once dirname(__DIR__, 3) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'site_assets.php';

function search_detail_h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function search_detail_asset_url(string $relativePath): string
{
    $normalized = trim(str_replace('\\', '/', $relativePath), '/');
    $publicPath = '/search/assets/' . $normalized;
    $diskPath = dirname(__DIR__) . DIRECTORY_SEPARATOR . 'assets' . DIRECTORY_SEPARATOR
        . str_replace('/', DIRECTORY_SEPARATOR, $normalized);
    $version = is_file($diskPath) ? (string)filemtime($diskPath) : '';
    return $version !== '' ? $publicPath . '?v=' . rawurlencode($version) : $publicPath;
}

function search_detail_display_date(array $document): string
{
    foreach (['held_on', 'promulgated_on', 'sort_date', 'updated_at'] as $key) {
        $value = trim((string)($document[$key] ?? ''));
        if ($value !== '') {
            return $value;
        }
    }
    return '';
}

$id = trim((string)($_GET['id'] ?? ''));
$docType = miyabe_search_normalize_doc_type((string)($_GET['doc_type'] ?? ($_GET['type'] ?? 'minutes')));
$query = trim((string)($_GET['q'] ?? ''));
$document = null;
$error = '';

try {
    $document = miyabe_search_fetch_detail_document($id, $docType);
    if ($document === null) {
        http_response_code($id === '' ? 422 : 404);
        $error = $id === '' ? '詳細表示に必要な ID がありません。' : '文書が見つかりませんでした。';
    }
} catch (MiyabeOpenSearchException $exception) {
    http_response_code($exception->httpStatus);
    $error = 'OpenSearch search is unavailable.';
} catch (Throwable $exception) {
    error_log('[search/detail] ' . $exception->getMessage());
    http_response_code(500);
    $error = '詳細の取得に失敗しました。';
}

$title = trim((string)($document['title'] ?? '会議録詳細'));
$sourceUrl = trim((string)($document['source_url'] ?? ''));
$meta = array_values(array_filter([
    trim((string)($document['pref_name'] ?? '')),
    trim((string)($document['municipality_name'] ?? '')),
    trim((string)($document['assembly_name'] ?? '')),
    trim((string)($document['meeting_name'] ?? '')),
    trim((string)($document['year_label'] ?? '')),
    search_detail_display_date($document ?? []),
], static fn(string $value): bool => $value !== ''));

$boot = [
    'query' => $query,
    'document' => $document,
    'error' => $error,
];
?><!doctype html>
<html lang="ja">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title><?php echo search_detail_h($title); ?> - 会議録詳細</title>
    <?php echo site_render_favicon_links(); ?>
    <link rel="stylesheet" href="<?php echo search_detail_h(search_detail_asset_url('css/search.css')); ?>">
</head>
<body class="detail-page">
<div class="app-shell">
    <header class="topbar">
        <a class="brand" href="/search/">全国自治体 横断検索</a>
        <?php if ($sourceUrl !== ''): ?>
            <a class="result-link detail-source-link" href="<?php echo search_detail_h($sourceUrl); ?>" target="_blank" rel="noopener noreferrer">原サイト</a>
        <?php endif; ?>
    </header>

    <main class="detail-layout">
        <?php if ($error !== ''): ?>
            <div class="message is-error"><?php echo search_detail_h($error); ?></div>
        <?php else: ?>
            <section class="detail-head">
                <p class="kicker">会議録詳細</p>
                <h1><?php echo search_detail_h($title); ?></h1>
                <?php if ($meta !== []): ?>
                    <div class="result-meta detail-meta">
                        <?php foreach ($meta as $value): ?>
                            <span><?php echo search_detail_h($value); ?></span>
                        <?php endforeach; ?>
                    </div>
                <?php endif; ?>
            </section>

            <section class="detail-search" aria-label="ページ内検索">
                <label class="field" for="detail-search-input">
                    <span>ページ内検索</span>
                    <input id="detail-search-input" type="search" autocomplete="off">
                </label>
                <div class="detail-search-actions">
                    <button id="detail-prev" type="button">前へ</button>
                    <button id="detail-next" type="button">次へ</button>
                    <span id="detail-count" class="detail-count">0 / 0</span>
                </div>
            </section>

            <article id="detail-body" class="detail-body" aria-label="会議録全文"></article>
        <?php endif; ?>
    </main>
</div>

<script id="search-detail-boot" type="application/json"><?php echo json_encode($boot, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE | JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT); ?></script>
<script src="<?php echo search_detail_h(search_detail_asset_url('js/detail.js')); ?>"></script>
</body>
</html>
