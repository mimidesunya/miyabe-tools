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
    <title>処理状況 - 宮部たつひこの自治体調査</title>
    <?php echo site_render_favicon_links(); ?>
    <link rel="stylesheet" href="<?php echo status_h(status_asset_url('css/home.css')); ?>">
</head>
<body>
    <div class="shell">
        <section class="hero">
            <div class="eyebrow">Municipal Data Hub</div>
            <h1>宮部たつひこの<br>自治体調査</h1>
            <div class="hero-copy">
                会議録と例規集の取得、公開、検索インデックス反映の進み具合を確認できます。
            </div>
            <div class="hero-meta">
                <a href="/?doc_type=minutes">横断検索へ</a>
                <a href="/api-guide/">API解説</a>
                <a href="/privacy/">プライバシー</a>
                <span data-home-display-count>表示自治体: 読み込み中</span>
                <span data-home-municipality-count>自治体マスタ: 読み込み中</span>
                <span>切り替え単位: `slug`</span>
                <span data-home-generated-at>更新: 読み込み中</span>
                <div class="hero-meta-dynamic" data-home-task-summaries></div>
            </div>
        </section>

        <section class="running-board" hidden data-running-section>
            <div class="running-board-head">
                <div class="eyebrow">Task Status</div>
                <div class="running-board-title">データ処理の実行状況</div>
                <div class="running-summary-list" data-running-summary-list></div>
            </div>
            <div class="running-list" data-running-list></div>
        </section>

        <div class="legend">
            <span>利用可能: 画面とデータを公開中です</span>
            <span>休止中: データはあるものの公開を止めています</span>
            <span>要反映: 取得は完了したが公開用 HTML / 検索反映待ちです</span>
            <span>未公開: データ未生成ですが取得タスクの進捗は確認できます</span>
        </div>

        <section class="prefecture-filter" hidden data-home-filter-section>
            <div class="prefecture-filter-copy">
                <div class="eyebrow">Prefecture Filter</div>
                <div class="prefecture-filter-title">都道府県ごとに自治体を切り替え</div>
                <p data-home-filter-hint>都道府県一覧を読み込んでいます。</p>
            </div>
            <div class="prefecture-filter-controls">
                <label class="prefecture-filter-control">
                    <span>表示する都道府県</span>
                    <select data-home-prefecture-filter>
                        <option value="all">すべての都道府県</option>
                    </select>
                </label>
                <label class="prefecture-filter-control">
                    <span>状態</span>
                    <select data-home-issue-filter>
                        <option value="all">すべて</option>
                        <option value="issues">エラー・警告あり</option>
                        <option value="errors">エラーのみ</option>
                        <option value="warnings">警告のみ</option>
                    </select>
                </label>
            </div>
        </section>

        <section class="municipality-grid" data-home-grid>
            <div class="loading-panel" data-home-loading>自治体一覧を読み込んでいます。</div>
        </section>
    </div>
    <script>
        window.HOMEPAGE_API_URL = '/api/home.php';
    </script>
    <script src="<?php echo status_h(status_asset_url('js/home.js')); ?>"></script>
</body>
</html>
