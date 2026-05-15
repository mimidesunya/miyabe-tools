<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'municipalities.php';
require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'site_assets.php';

function search_h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function search_asset_url(string $relativePath): string
{
    $normalized = trim(str_replace('\\', '/', $relativePath), '/');
    $publicPath = '/search/assets/' . $normalized;
    $diskPath = __DIR__ . DIRECTORY_SEPARATOR . 'assets' . DIRECTORY_SEPARATOR
        . str_replace('/', DIRECTORY_SEPARATOR, $normalized);
    $version = is_file($diskPath) ? (string)filemtime($diskPath) : '';
    return $version !== '' ? $publicPath . '?v=' . rawurlencode($version) : $publicPath;
}

$prefectures = [];
foreach (municipality_prefecture_names() as $code => $name) {
    $prefectures[] = ['code' => (string)$code, 'name' => (string)$name];
}

$boot = [
    'apiUrl' => '/api/search',
    'query' => trim((string)($_GET['q'] ?? '')),
    'docType' => trim((string)($_GET['doc_type'] ?? ($_GET['type'] ?? 'all'))),
    'slug' => trim((string)($_GET['slug'] ?? '')),
    'prefCode' => trim((string)($_GET['pref_code'] ?? ($_GET['pref'] ?? ''))),
    'startYear' => trim((string)($_GET['start_year'] ?? '')),
    'endYear' => trim((string)($_GET['end_year'] ?? '')),
    'sort' => trim((string)($_GET['sort'] ?? 'relevance')),
    'prefectures' => $prefectures,
];
?><!doctype html>
<html lang="ja">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>会議録・例規集 統合検索</title>
    <?php echo site_render_favicon_links(); ?>
    <link rel="stylesheet" href="<?php echo search_h(search_asset_url('css/search.css')); ?>">
</head>
<body>
<div class="app-shell">
    <header class="topbar">
        <a class="brand" href="/">宮部たつひこの自治体調査</a>
        <nav class="topnav" aria-label="検索範囲">
            <button type="button" data-doc-type="all">統合</button>
            <button type="button" data-doc-type="minutes">会議録</button>
            <button type="button" data-doc-type="reiki">例規集</button>
        </nav>
    </header>

    <main class="search-layout">
        <aside class="filters">
            <form id="search-form" class="search-form">
                <label class="field" for="search-query">
                    <span>キーワード</span>
                    <input id="search-query" name="q" type="search" value="<?php echo search_h((string)$boot['query']); ?>" autocomplete="off" autofocus>
                </label>
                <label class="field" for="search-slug">
                    <span>自治体 slug</span>
                    <input id="search-slug" name="slug" type="text" value="<?php echo search_h((string)$boot['slug']); ?>" autocomplete="off">
                </label>
                <label class="field" for="search-pref">
                    <span>都道府県</span>
                    <select id="search-pref" name="pref_code">
                        <option value="">全国</option>
                        <?php foreach ($prefectures as $prefecture): ?>
                            <option value="<?php echo search_h((string)$prefecture['code']); ?>" <?php echo (string)$prefecture['code'] === (string)$boot['prefCode'] ? 'selected' : ''; ?>>
                                <?php echo search_h((string)$prefecture['name']); ?>
                            </option>
                        <?php endforeach; ?>
                    </select>
                </label>
                <div class="split-fields">
                    <label class="field" for="search-start-year">
                        <span>開始年</span>
                        <input id="search-start-year" name="start_year" type="number" min="1" max="9999" step="1" value="<?php echo search_h((string)$boot['startYear']); ?>">
                    </label>
                    <label class="field" for="search-end-year">
                        <span>終了年</span>
                        <input id="search-end-year" name="end_year" type="number" min="1" max="9999" step="1" value="<?php echo search_h((string)$boot['endYear']); ?>">
                    </label>
                </div>
                <label class="field" for="search-sort">
                    <span>並び順</span>
                    <select id="search-sort" name="sort">
                        <option value="relevance">関連度</option>
                        <option value="date">日付順</option>
                    </select>
                </label>
                <button class="primary-button" type="submit">検索</button>
            </form>

            <section class="facet-panel">
                <h2>絞り込み状況</h2>
                <div id="facet-list" class="facet-list"></div>
            </section>
        </aside>

        <section class="workspace">
            <div class="workspace-head">
                <div>
                    <p class="kicker">全国自治体</p>
                    <h1>会議録・例規集 統合検索</h1>
                </div>
                <div id="search-stats" class="stats"></div>
            </div>
            <div id="message-area" class="message-area"></div>
            <div id="results" class="results"></div>
            <div id="pager" class="pager"></div>
        </section>
    </main>
</div>

<script id="search-boot" type="application/json"><?php echo json_encode($boot, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE); ?></script>
<script src="<?php echo search_h(search_asset_url('js/search.js')); ?>"></script>
</body>
</html>
