<?php
declare(strict_types=1);

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'site_assets.php';

function home_h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

?><!doctype html>
<html lang="ja">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>自治体対応マップ - 宮部たつひこの自治体調査</title>
    <?php echo site_render_favicon_links(); ?>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
    <link rel="stylesheet" href="<?php echo home_h(site_asset_url('assets/css/home.css')); ?>">
</head>
<body>
<div class="app-shell">
    <header class="site-header">
        <a class="brand" href="/">宮部たつひこの自治体調査</a>
        <nav class="site-nav" aria-label="主要ページ">
            <a aria-current="page" href="/">対応マップ</a>
            <a href="/search/">横断検索</a>
            <a href="/api-guide/">API</a>
            <a href="/status/">稼働状況</a>
        </nav>
    </header>

    <main>
        <section class="map-intro" aria-labelledby="page-title">
            <div class="map-intro-copy">
                <p class="eyebrow">Coverage Map</p>
                <h1 id="page-title">自治体対応マップ</h1>
                <p>
                    会議録、例規集、ポスター掲示板の公開状況を国土地理院地図で確認できます。
                    資料本文は横断検索で探せます。
                </p>
            </div>
            <div class="map-intro-actions" aria-label="関連ページ">
                <a class="primary-link" href="/search/">横断検索を開く</a>
                <a href="/api-guide/">AI向けAPI</a>
                <a href="/openapi.json">OpenAPI JSON</a>
            </div>
        </section>

        <section class="stats-strip" aria-label="対応状況サマリー" data-home-statbar>
            <div class="stat-card stat-card-loading">対応状況を読み込んでいます。</div>
        </section>

        <section class="coverage-dashboard" aria-label="自治体対応状況">
            <div class="toolbar">
                <div class="feature-switch" role="group" aria-label="表示する機能">
                    <button type="button" class="is-active" data-feature-filter="all">全て</button>
                    <button type="button" data-feature-filter="gijiroku">会議録</button>
                    <button type="button" data-feature-filter="reiki">例規集</button>
                    <button type="button" data-feature-filter="boards">掲示板</button>
                </div>
                <label class="control-field" for="home-prefecture-filter">
                    <span>都道府県</span>
                    <select id="home-prefecture-filter" data-home-prefecture-filter>
                        <option value="all">全国</option>
                    </select>
                </label>
                <label class="control-field" for="home-issue-filter">
                    <span>状態</span>
                    <select id="home-issue-filter" data-home-issue-filter>
                        <option value="all">すべて</option>
                        <option value="ready">利用可能</option>
                        <option value="issues">エラー・警告あり</option>
                        <option value="pending">準備中・未公開</option>
                    </select>
                </label>
                <label class="control-field control-field-search" for="home-municipality-search">
                    <span>自治体名</span>
                    <input id="home-municipality-search" type="search" placeholder="例: 川崎市" data-home-search>
                </label>
            </div>

            <div class="map-workspace">
                <div class="map-stage">
                    <div class="map-stage-head">
                        <div>
                            <h2>国土地理院マップ</h2>
                            <p data-home-filter-hint>地図を読み込んでいます。</p>
                        </div>
                        <div class="legend" aria-label="凡例">
                            <span><i class="legend-dot legend-dot-minutes"></i>会議録</span>
                            <span><i class="legend-dot legend-dot-reiki"></i>例規集</span>
                            <span><i class="legend-dot legend-dot-boards"></i>掲示板</span>
                            <span><i class="legend-dot legend-dot-pending"></i>準備中</span>
                        </div>
                    </div>
                    <div class="map-frame">
                        <div id="coverage-map" class="coverage-map" role="region" aria-label="自治体対応状況の国土地理院マップ" data-coverage-map></div>
                        <div class="map-loading" data-home-loading>自治体データを読み込んでいます。</div>
                    </div>
                </div>

                <aside class="detail-panel" data-home-detail aria-live="polite">
                    <div class="detail-empty">
                        <h2>自治体を選択</h2>
                        <p>地図上の点を選ぶと、対応している機能と公開先を確認できます。</p>
                    </div>
                </aside>
            </div>
        </section>

        <section class="operations-board" data-running-section hidden>
            <div class="section-head">
                <h2>処理状況</h2>
                <p>スクレイピングと検索インデックス反映の現在値です。</p>
            </div>
            <div class="operation-summary-list" data-running-summary-list></div>
            <div class="running-list" data-running-list hidden></div>
        </section>

        <section class="municipality-results" aria-label="表示中の自治体">
            <div class="section-head">
                <h2>表示中の自治体</h2>
                <p data-home-display-count>表示自治体: 0</p>
            </div>
            <div class="municipality-list" data-home-grid>
                <div class="loading-panel">自治体データを読み込んでいます。</div>
            </div>
        </section>

        <footer class="page-footer">
            <span data-home-municipality-count>自治体マスタ: 読み込み中</span>
            <span data-home-generated-at>更新: 読み込み中</span>
            <span data-home-task-summaries></span>
        </footer>
    </main>
</div>

<script>
window.HOMEPAGE_API_URL = '/api/home.php';
window.HOMEPAGE_TASK_STATUS_API_URL = '/api/task-status.php';
</script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="<?php echo home_h(site_asset_url('assets/js/municipality-coordinates.js')); ?>"></script>
<script src="<?php echo home_h(site_asset_url('assets/js/home.js')); ?>" defer></script>
</body>
</html>
