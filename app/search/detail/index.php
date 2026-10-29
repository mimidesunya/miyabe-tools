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
    if (($document['doc_type'] ?? '') === 'hyoka') {
        return '';
    }
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

$title = trim((string)($document['title'] ?? ($docType === 'hyoka' ? '事務事業評価詳細' : '会議録詳細')));
$sourceUrl = trim((string)($document['source_url'] ?? ''));
$sourcePageUrl = trim((string)($document['source_page_url'] ?? ''));
$sourcePageLink = $docType === 'hyoka' ? trim((string)($document['source_page_link'] ?? '')) : '';
$sourceLink = $sourcePageLink !== '' ? $sourcePageLink : $sourceUrl;
$pageNumber = $docType === 'hyoka' ? (int)($document['page'] ?? 0) : 0;
$hyokaKindLabels = [
    'project_evaluation' => '事務事業評価',
    'policy_evaluation' => '施策・政策評価',
    'external_review' => '外部評価',
    'overview' => '概要・一覧',
];
$yearLabel = trim((string)($document['year_label'] ?? ''));
if ($docType === 'hyoka' && $yearLabel === '') {
    $yearLabel = !empty($document['fiscal_year']) ? (string)$document['fiscal_year'] . '年度' : '年度不明';
}
$meta = array_values(array_filter([
    trim((string)($document['pref_name'] ?? '')),
    trim((string)($document['municipality_name'] ?? '')),
    trim((string)($document['assembly_name'] ?? '')),
    trim((string)($document['meeting_name'] ?? '')),
    $yearLabel,
    search_detail_display_date($document ?? []),
    $docType === 'hyoka' ? ($hyokaKindLabels[(string)($document['kind'] ?? '')] ?? '') : '',
    $docType === 'hyoka' ? ($pageNumber > 0 ? (string)$pageNumber . '頁' : '頁区切りなし（文書全体）') : '',
], static fn(string $value): bool => $value !== ''));

$docTypeLabel = match ($docType) { 'reiki' => '例規', 'hyoka' => '事務事業評価', default => '会議録' };
$bodyLabel = $pageNumber > 0 ? (string)$pageNumber . '頁の本文' : '全文';

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
    <title><?php echo search_detail_h($title); ?>｜自治体マップ</title>
    <?php if ($document !== null): ?>
        <?php echo site_render_page_meta(
            $title . '｜自治体マップ',
            ($meta !== [] ? implode('・', $meta) . 'の' : '') . $docTypeLabel . '「' . $title . '」の' . $bodyLabel . 'を表示します。原典URLつき。',
            '/search/detail/?id=' . rawurlencode($id) . '&doc_type=' . rawurlencode($docType)
        ); ?>
    <?php else: ?>
        <meta name="robots" content="noindex">
    <?php endif; ?>
    <?php echo site_render_favicon_links(); ?>
    <link rel="stylesheet" href="<?php echo search_detail_h(search_detail_asset_url('css/search.css')); ?>">
</head>
<body class="detail-page">
<div class="app-shell">
    <header class="topbar">
        <?php echo site_render_brand('/'); ?>
        <?php if ($sourceLink !== ''): ?>
            <a class="result-link detail-source-link" href="<?php echo search_detail_h($sourceLink); ?>" target="_blank" rel="noopener noreferrer"><?php echo $docType === 'hyoka' ? ($pageNumber > 0 ? '原典 ' . $pageNumber . '頁' : '原典') : '原サイト'; ?></a>
        <?php endif; ?>
    </header>

    <main class="detail-layout">
        <?php if ($error !== ''): ?>
            <div class="message is-error"><?php echo search_detail_h($error); ?></div>
        <?php else: ?>
            <section class="detail-head">
                <p class="kicker"><?php echo search_detail_h($docTypeLabel); ?>詳細</p>
                <h1><?php echo search_detail_h($title); ?></h1>
                <?php if ($meta !== []): ?>
                    <div class="result-meta detail-meta">
                        <?php foreach ($meta as $value): ?>
                            <span><?php echo search_detail_h($value); ?></span>
                        <?php endforeach; ?>
                    </div>
                <?php endif; ?>
                <?php if ($docType === 'hyoka' && ($document['status'] ?? '') === 'gone'): ?>
                    <p class="message">取得元の掲載ページで見つからない文書です。最後に取得した本文を表示しています。</p>
                <?php endif; ?>
                <?php if ($docType === 'hyoka' && $pageNumber === 0): ?>
                    <p>保存済み本文の頁区切りを確認できないため、文書全体を表示しています。</p>
                <?php endif; ?>
                <?php if ($docType === 'hyoka' && $sourcePageUrl !== ''): ?>
                    <p><a href="<?php echo search_detail_h($sourcePageUrl); ?>" target="_blank" rel="noopener noreferrer">文書を見つけた掲載ページ</a></p>
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

            <article id="detail-body" class="detail-body" aria-label="<?php echo search_detail_h($docTypeLabel . $bodyLabel); ?>"></article>
        <?php endif; ?>
    </main>
</div>

<script id="search-detail-boot" type="application/json"><?php echo json_encode($boot, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE | JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT); ?></script>
<script src="<?php echo search_detail_h(search_detail_asset_url('js/detail.js')); ?>"></script>
</body>
</html>
