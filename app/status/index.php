<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'site_assets.php';
require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'municipalities.php';
require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'hyoka_display.php';

function status_h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function status_n(mixed $value): string
{
    return number_format((int)($value ?? 0));
}

// 事務事業評価は会議録・例規と違い、実行状態の DB にまだつないでいない。
// 取り込みのあとに tools/hyoka/build_display_data.py が書く控えから、件数だけを出す。
$hyokaStatus = miyabe_hyoka_display_status();
$hyokaByPref = [];
if ($hyokaStatus !== null) {
    foreach ($hyokaStatus['municipalities'] as $row) {
        if (is_array($row)) {
            $hyokaByPref[substr((string)($row['code'] ?? ''), 0, 2)][] = $row;
        }
    }
    ksort($hyokaByPref, SORT_STRING);
}
$hyokaTotals = is_array($hyokaStatus['totals'] ?? null) ? $hyokaStatus['totals'] : [];
$hyokaFetched = (int)($hyokaTotals['fetch_status']['ok'] ?? 0) + (int)($hyokaTotals['fetch_status']['partial'] ?? 0);
$prefectureNames = municipality_prefecture_names();

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
        '全国の自治体の会議録・例規集・事務事業評価について、データの取得・公開・検索インデックス反映の進み具合を自治体ごとに確認できます。',
        '/status/'
    ); ?>
    <?php echo site_render_favicon_links(); ?>
    <link rel="stylesheet" href="<?php echo status_h(status_asset_url('css/home.css')); ?>">
    <link rel="stylesheet" href="<?php echo status_h(status_asset_url('css/municipalities.css')); ?>">
</head>
<body>
    <div class="shell">
        <header class="status-masthead">
            <?php echo site_render_brand('/'); ?>
            <nav aria-label="関連ページ">
                <a href="/">地図から探す</a>
                <a href="/search/">記録を検索</a>
                <a href="/municipalities/">自治体一覧</a>
                <a href="/api-guide/">AIから使う（MCP）</a>
            </nav>
        </header>
        <section class="hero">
            <div class="eyebrow">自治体資料の収集状況</div>
            <h1>収集・公開状況</h1>
            <div class="hero-copy">
                会議録・例規集・事務事業評価をどこまで取り込み、どこまで検索に載せられたかを全国分まとめています。
                会議録と例規集の自治体ごとの状態は<a href="/">自治体マップ</a>で、事務事業評価は<a href="#hyoka">下の表</a>で見られます。
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

        <section class="coverage-dashboard hyoka-status" id="hyoka" aria-labelledby="hyoka-status-title">
            <div class="coverage-head">
                <p class="eyebrow"><span>03</span> 事務事業評価</p>
            </div>
            <h2 class="hyoka-status-title" id="hyoka-status-title">事務事業評価の取り込み</h2>
            <?php if ($hyokaStatus === null): ?>
                <p class="hyoka-status-lead">取り込みの状態をまだ集計していません。</p>
            <?php else: ?>
                <p class="hyoka-status-lead">
                    各自治体の評価の掲載ページから文書を取得し、本文にして<a href="/search/?doc_type=hyoka">検索</a>に載せています。
                    文字の無い画像だけの PDF は OCR で読んでいます。事業ごとの欄（事業名・担当部署・今後の方向性など）に分けたのは一部の自治体です。
                    集計 <?php echo status_h((string)($hyokaStatus['generated_at'] ?? '')); ?>。
                </p>
                <div class="hyoka-status-grid">
                    <section class="cov-feat">
                        <h3>自治体</h3>
                        <ol class="cov-flow">
                            <li><span>取得の対象</span><b><?php echo status_n($hyokaTotals['municipalities'] ?? 0); ?></b></li>
                            <li><span>文書を取得できた</span><b><?php echo status_n($hyokaFetched); ?></b></li>
                            <li class="is-goal"><span>検索できる</span><b><?php echo status_n($hyokaTotals['searchable_municipalities'] ?? 0); ?></b></li>
                        </ol>
                    </section>
                    <section class="cov-feat">
                        <h3>文書</h3>
                        <ol class="cov-flow">
                            <li><span>取得した文書</span><b><?php echo status_n($hyokaTotals['documents'] ?? 0); ?></b></li>
                            <li><span>本文にできた</span><b><?php echo status_n($hyokaTotals['text_ok'] ?? 0); ?></b></li>
                            <li class="is-goal"><span>検索できる</span><b><?php echo status_n($hyokaTotals['indexed_documents'] ?? 0); ?></b></li>
                        </ol>
                        <p class="hyoka-status-sub">
                            うち OCR で読んだ <?php echo status_n($hyokaTotals['ocr_done'] ?? 0); ?> 本。
                            OCR 待ち <?php echo status_n($hyokaTotals['ocr_waiting'] ?? 0); ?> 本、OCR で読めなかった <?php echo status_n($hyokaTotals['ocr_failed'] ?? 0); ?> 本、
                            本文の無い文書（ZIP など）<?php echo status_n($hyokaTotals['no_text'] ?? 0); ?> 本。
                        </p>
                    </section>
                    <section class="cov-feat">
                        <h3>事業ごとの欄</h3>
                        <ol class="cov-flow">
                            <li><span>欄に分けた自治体</span><b><?php echo status_n($hyokaTotals['municipalities_with_records'] ?? 0); ?></b></li>
                            <li class="is-goal"><span>事業の記録</span><b><?php echo status_n($hyokaTotals['records'] ?? 0); ?></b></li>
                        </ol>
                        <p class="hyoka-status-sub">原典の表の欄から機械で書き写したもの（人の確認前）。文書の詳細画面に事業の一覧として出ます。</p>
                    </section>
                </div>
                <details class="hyoka-status-detail">
                    <summary>自治体ごとの状態（<?php echo status_n($hyokaTotals['municipalities'] ?? 0); ?>）</summary>
                    <?php foreach ($hyokaByPref as $prefCode => $rows): ?>
                        <h3 class="prefecture-heading"><?php echo status_h($prefectureNames[$prefCode] ?? $prefCode); ?><span class="prefecture-count"><?php echo count($rows); ?></span></h3>
                        <div class="table-scroll">
                            <table class="municipality-table hyoka-status-table">
                                <caption class="visually-hidden"><?php echo status_h($prefectureNames[$prefCode] ?? $prefCode); ?>の事務事業評価の取り込みの状態</caption>
                                <thead>
                                    <tr>
                                        <th scope="col">自治体</th>
                                        <th scope="col">取得</th>
                                        <th scope="col" class="is-num">文書</th>
                                        <th scope="col" class="is-num">検索できる</th>
                                        <th scope="col" class="is-num">OCR で読んだ</th>
                                        <th scope="col" class="is-num">OCR 待ち・読めず</th>
                                        <th scope="col" class="is-num">事業の記録</th>
                                        <th scope="col">年度</th>
                                        <th scope="col">掲載ページ</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    <?php foreach ($rows as $row): ?>
                                        <?php
                                        $entryUrl = trim((string)($row['entry_url'] ?? ''));
                                        $entryHost = (string)(parse_url($entryUrl, PHP_URL_HOST) ?: '');
                                        $yearMin = $row['year_min'] ?? null;
                                        $yearMax = $row['year_max'] ?? null;
                                        $pendingOcr = (int)($row['ocr_waiting'] ?? 0) + (int)($row['ocr_failed'] ?? 0);
                                        $nameParts = preg_split('/\s+/u', trim((string)($row['name'] ?? ''))) ?: [];
                                        ?>
                                        <tr>
                                            <th scope="row" class="col-name"><a href="/search/?<?php echo status_h(http_build_query(['doc_type' => 'hyoka', 'slug' => (string)($row['slug'] ?? '')])); ?>"><?php echo status_h((string)end($nameParts)); ?></a></th>
                                            <td><?php echo status_h(miyabe_hyoka_display_fetch_label((string)($row['fetch_status'] ?? ''))); ?></td>
                                            <td class="is-num"><?php echo status_n($row['documents'] ?? 0); ?></td>
                                            <td class="is-num"><?php echo ($row['indexed_documents'] ?? null) === null ? '—' : status_n($row['indexed_documents']); ?></td>
                                            <td class="is-num"><?php echo status_n($row['ocr_done'] ?? 0); ?></td>
                                            <td class="is-num"><?php echo $pendingOcr > 0 ? status_n($pendingOcr) : ''; ?></td>
                                            <td class="is-num"><?php echo (int)($row['records'] ?? 0) > 0 ? status_n($row['records']) : ''; ?></td>
                                            <td><?php echo $yearMin !== null ? status_h($yearMin === $yearMax ? (string)$yearMin : $yearMin . '〜' . $yearMax) : ''; ?></td>
                                            <td><?php if ($entryUrl !== ''): ?><a class="source-link" href="<?php echo status_h($entryUrl); ?>" target="_blank" rel="noopener nofollow"><?php echo status_h($entryHost !== '' ? $entryHost : $entryUrl); ?></a><?php endif; ?></td>
                                        </tr>
                                    <?php endforeach; ?>
                                </tbody>
                            </table>
                        </div>
                    <?php endforeach; ?>
                </details>
            <?php endif; ?>
        </section>

        <section class="running-board" hidden data-running-section>
            <div class="running-board-head">
                <div class="eyebrow">実行中の処理</div>
                <div class="running-board-title">いま動いている処理</div>
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
