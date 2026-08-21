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
                会議録と例規集の取得、公開、検索インデックス反映の進み具合を確認できます。
            </div>
            <div class="hero-meta">
                <a href="/search/">記録を検索へ</a>
                <a href="/privacy/">プライバシー</a>
                <a href="/terms/">利用規約</a>
                <a href="/support/">サポート</a>
                <span data-home-display-count>表示自治体: 読み込み中</span>
                <span data-home-municipality-count>自治体マスタ: 読み込み中</span>
                <span data-home-generated-at>更新: 読み込み中</span>
                <div class="hero-meta-dynamic" data-home-task-summaries></div>
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

        <div class="legend status-legend" aria-label="収集状態の凡例">
            <span>利用可能: 画面とデータを公開中です</span>
            <span>取得完了（検索可）: 取得元の全一覧を走査し、発見した会議録を取得済みです</span>
            <span>一部検索可（追加取得中・予定）: 取得済み分は検索でき、残りを追加取得します</span>
            <span>一部検索可（エラー停止）: 取得途中のエラーにより、取得済み分だけ検索できます</span>
            <span>検索可（更新エラー）: 前回の全件データは検索できますが、最新確認に失敗しています</span>
            <span>検索可（取得範囲未判定）: 検索データはありますが、全一覧走査済みか確認できません</span>
            <span>未取得: 取得元と取得処理は登録済みですが、まだ公開データがありません</span>
            <span>取得エラー（実装済み）: 取得処理を実行しましたがエラーになっています</span>
            <span>未実装: 取得元は判明していますが、その形式にはまだ対応していません</span>
            <span>取得元未特定: 取得元URLをまだ特定できていません</span>
            <span>取得対象外: 取得方針により自動取得しません</span>
            <span>要反映・検索準備中: 取得済みで、公開または検索への反映待ちです</span>
            <span>検索反映待ち: 取得済みですが、検索できる件数がまだ追いついていません</span>
            <span>会議日: 公開検索で実際に検索できる最古〜最新の会議日です。日付を抽出できない資料は「日付情報なし」と表示します</span>
        </div>

        <section class="prefecture-filter" hidden data-home-filter-section>
            <div class="prefecture-filter-copy">
                <div class="eyebrow">都道府県で絞り込む</div>
                <div class="prefecture-filter-title">都道府県ごとに自治体を切り替え</div>
                <p data-home-filter-hint>都道府県一覧を読み込んでいます。</p>
            </div>
            <div class="prefecture-filter-controls">
                <div class="feature-switch" role="group" aria-label="表示する資料">
                    <button type="button" class="is-active" data-feature-filter="gijiroku">会議録</button>
                    <button type="button" data-feature-filter="reiki">例規集</button>
                </div>
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
                        <option value="ready">利用可能</option>
                        <option value="partial_planned">一部検索可（追加取得中・予定）</option>
                        <option value="index_pending">検索反映待ち</option>
                        <option value="body_not_published">本文なし（目次のみ公開）</option>
                        <option value="not_found">未対応（会議録を見つけられず）</option>
                        <option value="partial_error">一部検索可（エラー停止）</option>
                        <option value="partial_recent_only">一部検索可（取得元が直近分のみ公開）</option>
                        <option value="update_error">検索可（更新エラー）</option>
                        <option value="coverage_unknown">検索可（取得範囲未判定）</option>
                        <option value="unacquired">未取得</option>
                        <option value="runtime_error">取得エラー（実装済み）</option>
                        <option value="unsupported">未実装</option>
                        <option value="source_unresolved">取得元未特定</option>
                        <option value="excluded">取得対象外</option>
                        <option value="review_required">取得可否確認中</option>
                        <option value="publish_pending">要反映</option>
                        <option value="search_pending">検索準備中</option>
                        <option value="suspended">休止中</option>
                        <option value="issues">エラー・警告あり</option>
                        <option value="warning">警告のみ</option>
                    </select>
                </label>
            </div>
        </section>

        <section class="municipality-grid" data-home-grid>
            <div class="loading-panel" data-home-loading>自治体一覧を読み込んでいます。</div>
        </section>
    </div>
    <script>
        window.HOMEPAGE_API_URL = '/api/status.php';
    </script>
    <script src="<?php echo status_h(status_asset_url('js/home.js')); ?>"></script>
</body>
</html>
