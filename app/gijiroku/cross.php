<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'gijiroku_search.php';

// 横断検索ページの初期表示データを組み立てる。
redirect_to_canonical_query_slug_if_needed();

function h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

$searchMunicipalities = [];
foreach (gijiroku_search_ready_summaries() as $municipality) {
    $searchMunicipalities[] = $municipality;
}

$selectedSlug = get_slug((string)($_GET['slug'] ?? ''));
$selectedSlugValid = false;
foreach ($searchMunicipalities as $item) {
    if ((string)($item['slug'] ?? '') === $selectedSlug) {
        $selectedSlugValid = true;
        break;
    }
}
if (!$selectedSlugValid) {
    $selectedSlug = '';
}

$boot = [
    'apiUrl' => '/gijiroku/api.php',
    'query' => trim((string)($_GET['q'] ?? '')),
    'selectedSlug' => $selectedSlug,
    'municipalities' => $searchMunicipalities,
];

$fallbackUrl = $searchMunicipalities[0]['url'] ?? '/gijiroku/';
$cssVer = @filemtime(dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'app' . DIRECTORY_SEPARATOR . 'gijiroku' . DIRECTORY_SEPARATOR . 'assets' . DIRECTORY_SEPARATOR . 'css' . DIRECTORY_SEPARATOR . 'cross.css') ?: 1;
$jsVer = @filemtime(dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'app' . DIRECTORY_SEPARATOR . 'gijiroku' . DIRECTORY_SEPARATOR . 'assets' . DIRECTORY_SEPARATOR . 'js' . DIRECTORY_SEPARATOR . 'cross.js') ?: 1;
?><!doctype html>
<html lang="ja">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>議事録 横断全文検索</title>
    <link rel="stylesheet" href="/gijiroku/assets/css/cross.css?v=<?php echo h((string)$cssVer); ?>">
</head>
<body>
<div class="shell">
    <section class="hero panel">
        <div class="hero-main">
            <div class="eyebrow">Cross-Municipality Minutes Search</div>
            <h1>会議録を、<br>自治体をまたいで全文検索</h1>
            <p class="hero-copy">キーワードを一度投げると、検索可能な自治体の会議録 DB を順に走査して、ヒットした自治体へそのまま切り替えられます。自治体別ページへ飛ばずに、まず全体の当たりを付けるための入口です。</p>
            <div class="hero-tags">
                <span class="hero-tag">SQLite FTS5</span>
                <span class="hero-tag">自治体ごとに動的切替</span>
                <span class="hero-tag">進捗つき非同期検索</span>
            </div>
            <div class="hero-links">
                <a href="/">トップへ戻る</a>
                <a href="<?php echo h((string)$fallbackUrl); ?>">自治体別検索へ</a>
            </div>
        </div>
        <div class="hero-side">
            <div class="hero-metric">
                <span>検索対象自治体</span>
                <strong><?php echo h((string)count($searchMunicipalities)); ?></strong>
            </div>
            <div class="hero-metric">
                <span>検索の流れ</span>
                <strong>集計して切替</strong>
            </div>
            <div class="hero-metric">
                <span>詳細閲覧</span>
                <strong>既存の自治体ページへ接続</strong>
            </div>
        </div>
    </section>

    <section class="layout">
        <aside class="side-stack">
            <section class="panel controls-panel">
                <div class="section-head">
                    <h2>検索条件</h2>
                    <p>まずは本文キーワードだけ投げて、どの自治体で議論されているかをざっと絞ります。</p>
                </div>
                <form id="cross-search-form" class="search-form">
                    <label class="field" for="cross-query">
                        <span>キーワード</span>
                        <input id="cross-query" name="q" type="text" value="<?php echo h((string)$boot['query']); ?>" placeholder="例: 補正予算 / 学校 AND 空調 / 子育て NEAR/5 支援" autocomplete="off">
                    </label>
                    <div class="form-actions">
                        <button id="cross-search-button" class="button" type="submit">横断検索する</button>
                        <a class="button-secondary" href="/gijiroku/cross.php">リセット</a>
                    </div>
                </form>
                <div class="examples">
                    <button type="button" class="chip" data-example-query="補正予算">補正予算</button>
                    <button type="button" class="chip" data-example-query="学校 AND 空調">学校 AND 空調</button>
                    <button type="button" class="chip" data-example-query="子育て OR 保育">子育て OR 保育</button>
                    <button type="button" class="chip" data-example-query="防災 NEAR/5 トイレ">防災 NEAR/5 トイレ</button>
                </div>
            </section>

            <section class="panel progress-panel">
                <div class="section-head">
                    <h2>検索状況</h2>
                    <p id="search-progress-copy">キーワードを入れると、対象自治体を順に走査します。</p>
                </div>
                <div class="progress-track" aria-hidden="true">
                    <div id="search-progress-bar" class="progress-bar"></div>
                </div>
                <div id="search-progress-summary" class="summary-grid"></div>
                <div class="live-activity">
                    <div class="live-activity-head">
                        <span>検索中の自治体</span>
                        <strong id="search-active-count">待機中</strong>
                    </div>
                    <div id="search-active-list" class="active-search-list">
                        <div class="active-search-empty">キーワードを入れると、ここに現在検索中の自治体が表示されます。</div>
                    </div>
                </div>
            </section>

            <section class="panel municipality-panel">
                <div class="section-head">
                    <h2>自治体切り替え</h2>
                    <p>ヒット件数の多い自治体が上に寄ります。検索中でも先に見たい自治体へ切り替えられます。</p>
                </div>
                <div id="municipality-list" class="municipality-list"></div>
            </section>
        </aside>

        <main class="panel results-panel">
            <div class="results-head">
                <div>
                    <div class="eyebrow">Result Workspace</div>
                    <h2 id="selected-title">まずキーワードを入れてください</h2>
                    <p id="selected-meta" class="results-meta">会議録 DB を横断して、該当自治体と上位ヒットを並べます。</p>
                </div>
                <div class="results-links">
                    <a id="selected-open-link" class="button-secondary is-disabled" href="#" aria-disabled="true">自治体ページを開く</a>
                </div>
            </div>

            <div id="results-summary" class="summary-grid"></div>
            <div id="results-body" class="results-body">
                <div class="empty-state">
                    <strong>横断検索の準備ができています。</strong>
                    <span>キーワードを入れて実行すると、ヒットした自治体ごとに結果を切り替えられます。</span>
                </div>
            </div>
            <div id="results-pagination" class="pager"></div>
        </main>
    </section>
</div>

<script id="minutes-cross-boot" type="application/json"><?php echo json_encode($boot, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES); ?></script>
<script src="/gijiroku/assets/js/cross.js?v=<?php echo h((string)$jsVer); ?>"></script>
</body>
</html>
