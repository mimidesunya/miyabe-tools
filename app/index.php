<?php
declare(strict_types=1);

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'site_assets.php';

function homepage_h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function homepage_asset_url(string $relativePath): string
{
    $normalized = trim(str_replace('\\', '/', $relativePath), '/');
    $publicPath = '/assets/' . $normalized;
    $diskPath = __DIR__ . DIRECTORY_SEPARATOR . 'assets' . DIRECTORY_SEPARATOR
        . str_replace('/', DIRECTORY_SEPARATOR, $normalized);
    $version = is_file($diskPath) ? (string)filemtime($diskPath) : '';
    return $version !== '' ? $publicPath . '?v=' . rawurlencode($version) : $publicPath;
}
?><!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>宮部たつひこの自治体調査</title>
    <?php echo site_render_favicon_links(); ?>
    <link rel="stylesheet" href="<?php echo homepage_h(homepage_asset_url('css/home.css')); ?>">
</head>
<body>
    <div class="shell">
        <section class="hero">
            <div class="eyebrow">Municipal Data Hub</div>
            <h1>宮部たつひこの<br>自治体調査</h1>
            <div class="hero-copy">
                ポスター掲示場、会議録、例規集を自治体単位で整理しています。使える機能はすぐ開けて、準備中のものは進捗つきで追えます。
            </div>
            <div class="hero-actions">
                <a class="hero-cta" href="/gijiroku/cross.php" target="_blank" rel="noopener">
                    <span class="hero-cta-kicker"><span class="hero-cta-icon" aria-hidden="true">🏛️</span><span>会議録</span></span>
                    <span class="hero-cta-body">
                        <span class="hero-cta-title">会議録横断検索を開く</span>
                        <span class="hero-cta-sub">自治体をまたいで全文検索し、気になる自治体へそのまま切り替え</span>
                    </span>
                </a>
                <a class="hero-cta" href="/reiki/cross.php" target="_blank" rel="noopener">
                    <span class="hero-cta-kicker"><span class="hero-cta-icon" aria-hidden="true">⚖️</span><span>例規集</span></span>
                    <span class="hero-cta-body">
                        <span class="hero-cta-title">例規集横断検索を開く</span>
                        <span class="hero-cta-sub">条例・規則・要綱を自治体横断で探し、該当自治体へ移動</span>
                    </span>
                </a>
                <div class="hero-cta-copy">
                    まずは横断検索で該当自治体を見つけて、そこから各自治体の詳細画面へ入る導線です。
                </div>
            </div>
            <div class="hero-meta">
                <span data-home-display-count>表示自治体: 読み込み中</span>
                <span data-home-municipality-count>自治体マスタ: 読み込み中</span>
                <span>切り替え単位: `slug`</span>
                <span>データ参照: 共通レジストリ管理</span>
                <span data-home-generated-at>更新: 読み込み中</span>
                <div class="hero-meta-dynamic" data-home-task-summaries></div>
            </div>
        </section>

        <section class="running-board" hidden data-running-section>
            <div class="running-board-head">
                <div class="eyebrow">Scraping Now</div>
                <div class="running-board-title">実行中のスクレイピング</div>
            </div>
            <div class="running-list" data-running-list></div>
        </section>

        <div class="legend">
            <span>利用可能: 画面とデータを公開中です</span>
            <span>休止中: データはあるものの公開を止めています</span>
            <span>要反映: 取得は完了したが公開用データの反映待ちです</span>
            <span>未公開: データ未生成ですが取得タスクの進捗は確認できます</span>
        </div>

        <section class="municipality-grid" data-home-grid>
            <div class="loading-panel" data-home-loading>自治体一覧を読み込んでいます。</div>
        </section>
    </div>
    <script>
        window.HOMEPAGE_API_URL = '/api/home.php';
    </script>
    <script src="<?php echo homepage_h(homepage_asset_url('js/home.js')); ?>"></script>
</body>
</html>
