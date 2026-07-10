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
    <title>自治体マップ｜全国の会議録・例規集を横断検索</title>
    <?php echo site_render_page_meta(
        '自治体マップ｜全国の会議録・例規集を横断検索',
        '全国の自治体が公開する議会の会議録と例規集（条例・規則）をひとつの検索窓で横断検索。ClaudeやChatGPTなどのAIからはMCPで直接使えます。地図で自治体ごとの公開状況も確認でき、原典URLまでたどれます。無料・登録不要。',
        '/'
    ); ?>
    <?php echo site_render_favicon_links(); ?>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "自治体マップ",
        "url": "https://tools.miya.be/",
        "description": "全国の自治体の会議録・例規集を横断検索できるサイト。MCPによるAI連携と、公開状況の地図表示に対応",
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": "https://tools.miya.be/search/?q={search_term_string}"
            },
            "query-input": "required name=search_term_string"
        }
    }
    </script>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
    <link rel="stylesheet" href="<?php echo home_h(site_asset_url('assets/css/home.css')); ?>">
</head>
<body>
<div class="app-shell">
    <header class="site-header">
        <?php echo site_render_brand('/'); ?>
        <div class="header-meta">
            <span class="live-mark"><i></i> データ公開中</span>
            <span>原典URL・取得元情報を収録</span>
        </div>
    </header>

    <nav class="site-nav" aria-label="主要ページ">
        <a href="/search/">記録を検索</a>
        <a href="/api-guide/">AIから使う</a>
        <a aria-current="page" href="#coverage-map">地図から探す</a>
        <a href="/status/">収集・公開状況</a>
        <span class="nav-index">全国自治体資料</span>
    </nav>

    <main>
        <section class="map-intro" aria-label="自治体マップの概要">
            <div class="map-intro-copy">
                <p class="eyebrow"><span>01</span> 全国横断検索</p>
                <h1>全国の<em>会議録・例規集</em>を横断検索</h1>
                <p class="intro-lead">
                    自治体が公開する会議録・例規集・ポスター掲示場の情報を収集し、
                    ひとつの検索窓で全国をまとめて検索できます。
                    ClaudeやChatGPTなどのAIからもMCPで直接使えます。
                </p>
                <div class="map-intro-actions" aria-label="関連ページ">
                    <a class="primary-link" href="/search/">記録を検索する <span aria-hidden="true">→</span></a>
                    <a href="/api-guide/">AIから使う（MCP） <span aria-hidden="true">↗</span></a>
                    <a href="#coverage-map">地図から探す <span aria-hidden="true">↓</span></a>
                </div>
            </div>
            <aside class="stats-strip" aria-label="対応状況サマリー" data-home-statbar>
                <div class="stat-card stat-card-loading">きょうの収録状況を読み込んでいます。</div>
            </aside>
        </section>

        <form class="search-ribbon" action="/search/" method="get" aria-label="自治体資料を検索">
            <label for="home-record-search">自治体名・資料の言葉から探す</label>
            <div class="search-ribbon-control">
                <span aria-hidden="true">⌕</span>
                <input id="home-record-search" name="q" type="search" placeholder="例：川崎市　盛土　メガソーラー">
                <button type="submit">記録を検索</button>
            </div>
        </form>

        <section class="coverage-dashboard" aria-label="自治体対応状況">
            <div class="coverage-head">
                <p class="eyebrow"><span>02</span> 地図から探す</p>
                <span class="coverage-count" data-home-display-count>表示自治体: 0</span>
            </div>
            <div class="toolbar">
                <div class="feature-switch" role="group" aria-label="表示する機能">
                    <button type="button" class="is-active" data-feature-filter="gijiroku">会議録</button>
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
                    <input id="home-municipality-search" type="search" placeholder="市区町村を絞り込む" data-home-search>
                </label>
            </div>

            <div class="map-workspace">
                <div class="map-stage">
                    <div class="map-stage-head">
                        <div>
                            <h2>国土地理院地図</h2>
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
                <div><span>03</span><h2>収集・公開状況</h2></div>
                <p>スクレイピングと検索インデックス反映の現在値です。</p>
            </div>
            <div class="operation-summary-list" data-running-summary-list></div>
            <div class="running-list" data-running-list hidden></div>
        </section>

        <section class="municipality-results" aria-label="表示中の自治体">
            <div class="section-head">
                <div><span>04</span><h2>自治体索引</h2></div>
                <p>地図に表示している自治体を都道府県別に掲載します。</p>
            </div>
            <div class="municipality-list" data-home-grid>
                <div class="loading-panel">自治体データを読み込んでいます。</div>
            </div>
        </section>

        <footer class="page-footer">
            <div class="footer-brand">
                <strong>自治体マップ</strong>
                <nav class="footer-links" aria-label="サイト内ページ">
                    <a href="/search/">記録を検索</a>
                    <a href="/status/">収集・公開状況</a>
                    <a href="/api-guide/">AIから使う（MCP）</a>
                    <a href="/privacy/">プライバシー</a>
                </nav>
            </div>
            <div class="footer-data">
                <span data-home-municipality-count>自治体マスタ: 読み込み中</span>
                <span data-home-generated-at>更新: 読み込み中</span>
                <span data-home-task-summaries></span>
            </div>
        </footer>
    </main>
</div>

<script>
window.HOMEPAGE_API_URL = '/api/home.php';
window.HOMEPAGE_TASK_STATUS_API_URL = '/api/task-status.php';
</script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" defer></script>
<script src="<?php echo home_h(site_asset_url('assets/js/municipality-coordinates.js')); ?>" defer></script>
<script src="<?php echo home_h(site_asset_url('assets/js/home.js')); ?>" defer></script>
</body>
</html>
