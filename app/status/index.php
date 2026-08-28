<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'site_assets.php';

function status_h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function status_asset_url(string $relativePath): string
{
    $normalized = trim(str_replace('\\', '/', $relativePath), '/');
    $publicPath = '/assets/' . $normalized;
    $diskPath = dirname(__DIR__) . DIRECTORY_SEPARATOR . 'assets' . DIRECTORY_SEPARATOR
        . str_replace('/', DIRECTORY_SEPARATOR, $normalized);
    $version = is_file($diskPath) ? (string)filemtime($diskPath) : '';
    return $version !== '' ? $publicPath . '?v=' . rawurlencode($version) : $publicPath;
}
?><!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>収集・公開状況｜自治体マップ</title>
    <?php echo site_render_page_meta(
        '収集・公開状況｜自治体マップ',
        '全国の自治体の会議録・例規集について、データの取得・公開・検索インデックス反映の進み具合を自治体ごとに確認できます。',
        '/status/'
    ); ?>
    <?php echo site_render_favicon_links(); ?>
    <link rel="stylesheet" href="<?php echo status_h(status_asset_url('css/home.css')); ?>">
</head>
<body>
    <div class="shell">
        <header class="status-masthead">
            <?php echo site_render_brand('/'); ?>
            <nav aria-label="関連ページ">
                <a href="/">地図から探す</a>
                <a href="/search/">記録を検索</a>
                <a href="/api-guide/">AIから使う（MCP）</a>
            </nav>
        </header>
        <section class="hero">
            <div class="eyebrow">自治体資料の収集状況</div>
            <h1>収集・公開状況</h1>
            <div class="hero-copy">
                会議録と例規集の取得と検索反映が、全国でどこまで進んでいるかの集計です。
                自治体ごとの状態は<a href="/">自治体マップ</a>で確認できます。
            </div>
            <div class="hero-meta">
                <a href="/search/">記録を検索へ</a>
                <a href="/privacy/">プライバシー</a>
                <a href="/terms/">利用規約</a>
                <a href="/support/">サポート</a>
                <span data-home-municipality-count>自治体マスタ: 読み込み中</span>
                <span data-home-generated-at>更新: 読み込み中</span>
                <div class="hero-meta-dynamic" data-home-task-summaries></div>
            </div>
        </section>

        <section class="coverage-dashboard" aria-label="収録状況">
            <div class="coverage-head">
                <p class="eyebrow"><span>01</span> 全国の進み具合</p>
            </div>
            <aside class="stats-strip stats-strip-page" aria-label="収録状況サマリー" data-home-statbar>
                <div class="stat-card stat-card-loading">収録状況を読み込んでいます。</div>
            </aside>
        </section>

        <section class="state-board" aria-label="状態別の件数">
            <div class="coverage-head">
                <p class="eyebrow"><span>02</span> 状態別の件数</p>
            </div>
            <p class="state-board-note">
                件数を選ぶと、その状態の自治体だけを自治体マップに表示します。
            </p>
            <div class="state-board-groups" data-status-state-counts>
                <div class="loading-panel">状態別の件数を読み込んでいます。</div>
            </div>
        </section>

        <section class="running-board" hidden data-running-section>
            <div class="running-board-head">
                <div class="eyebrow">実行中の処理</div>
                <div class="running-board-title">データ処理の実行状況</div>
                <div class="running-summary-list" data-running-summary-list></div>
            </div>
            <div class="running-list" data-running-list></div>
        </section>


    </div>
    <script>
        // 自治体カードは送らせない。一覧はトップにあり、このページは件数しか使わない。
        window.HOMEPAGE_API_URL = '/api/status-summary.php';
        window.HOMEPAGE_TASK_STATUS_API_URL = '/api/task-status.php';
        window.HOMEPAGE_TASK_STATUS_POLL_MS = 3000;
        window.HOMEPAGE_REFRESH_MS = 60000;
    </script>
    <script src="<?php echo status_h(status_asset_url('js/home.js')); ?>"></script>
</body>
</html>
