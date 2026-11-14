<?php
declare(strict_types=1);

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'site_assets.php';
require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'municipalities.php';
require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'news.php';
require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'hyoka_display.php';

function home_h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

// 各地のお知らせ。一覧がまだ無ければ（取得の worker が 1 回も書いていない手元など）出さない。
$homeNews = news_latest_load('all');

// 事務事業評価を検索できる自治体。「自分のまちから探す」で、文書のある自治体にだけ入口を出す。
$homeHyokaSlugs = [];
foreach (miyabe_hyoka_display_status_by_slug() as $slug => $row) {
    if ((int)($row['indexed_documents'] ?? 0) > 0) {
        $homeHyokaSlugs[] = (string)$slug;
    }
}

// 試しに引く言葉。資料の種類ごとに、その資料でよく当たる言葉を並べる。
$homeExamples = [
    'minutes' => ['空き家', '学校統廃合', 'メガソーラー', '給食費', '官製談合'],
    'reiki' => ['空家', '太陽光発電', '政務活動費', 'ハラスメント', '手数料'],
    'hyoka' => ['敬老', '補助金', '委託料', '廃止', '見直し'],
];

?><!doctype html>
<html lang="ja">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>自治体マップ｜全国の会議録・例規集を横断検索</title>
    <?php echo site_render_page_meta(
        '自治体マップ｜全国の会議録・例規集を横断検索',
        '全国の自治体が公開している議会の会議録と例規集（条例・規則）を、ひとつの検索窓でまとめて探せます。ClaudeやChatGPTなどのAIからはMCPでそのまま使えます。地図から自治体ごとの公開状況も見られ、原典のURLまでたどれます。無料・登録不要。',
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
    <?php echo site_render_stylesheet(); ?>
    <link rel="stylesheet" href="<?php echo home_h(site_asset_url('assets/css/home.css')); ?>">
</head>
<body>
<?php echo site_render_header('home'); ?>

<main id="main" class="page home">
    <div class="home-top">
    <section class="search-panel" aria-labelledby="home-title">
        <h1 id="home-title">全国の自治体の文書を検索</h1>
        <p class="lead">
            議会で何が話されたか、どんな条例があるか、事業がどう評価されたか。全国の自治体が公開している文書を、まとめて調べられます。無料で、登録は要りません。
        </p>

        <form class="search-box" action="/search/" method="get" role="search" aria-label="自治体の文書を検索">
            <fieldset class="doc-tabs">
                <legend class="visually-hidden">探す資料</legend>
                <label class="doc-tab" data-doc="minutes">
                    <input type="radio" name="doc_type" value="minutes" checked>
                    <span class="doc-tab-main">議会の発言</span>
                    <span class="doc-tab-sub">会議録</span>
                </label>
                <label class="doc-tab" data-doc="reiki">
                    <input type="radio" name="doc_type" value="reiki">
                    <span class="doc-tab-main">条例・規則</span>
                    <span class="doc-tab-sub">例規集</span>
                </label>
                <label class="doc-tab" data-doc="hyoka">
                    <input type="radio" name="doc_type" value="hyoka">
                    <span class="doc-tab-main">事業の評価</span>
                    <span class="doc-tab-sub">事務事業評価</span>
                </label>
            </fieldset>

            <div class="search-box-body">
                <p class="doc-about" data-doc-about="minutes">議員や市長・職員が、議会で話した内容の記録です。</p>
                <p class="doc-about" data-doc-about="reiki">自治体が決めた条例・規則・要綱の本文です。</p>
                <p class="doc-about" data-doc-about="hyoka">自治体が自分の事業を点検した表です。費用や今後の方向が書いてあります。</p>

                <div class="search-row">
                    <label class="visually-hidden" for="home-record-search">調べたい言葉</label>
                    <input id="home-record-search" name="q" type="search" enterkeyhint="search" autocomplete="off" placeholder="調べたい言葉を入れる">
                    <button class="button" type="submit">検索</button>
                </div>

                <?php foreach ($homeExamples as $exampleType => $exampleWords): ?>
                <p class="search-examples" data-doc-examples="<?php echo home_h($exampleType); ?>">
                    <span>たとえば</span>
                    <?php foreach ($exampleWords as $exampleWord): ?>
                    <a href="/search/?<?php echo home_h(http_build_query(['q' => $exampleWord, 'doc_type' => $exampleType])); ?>"><?php echo home_h($exampleWord); ?></a>
                    <?php endforeach; ?>
                </p>
                <?php endforeach; ?>

                <details class="search-tips">
                    <summary>検索のコツ</summary>
                    <ul>
                        <li>言葉を空白で区切ると、その全部が出てくる文書を探します。例：<code>空き家 補助金</code></li>
                        <li><code>"</code>で囲むと、その言い回しどおりに探します。例：<code>"学校給食費の無償化"</code></li>
                        <li>どちらかが出てくる文書は <code>OR</code>、出てこない文書は <code>NOT</code> でつなぎます。例：<code>盛土 OR 土砂</code></li>
                        <li>都道府県・自治体・期間で絞るのは、検索したあとの画面でできます。</li>
                    </ul>
                </details>
            </div>
        </form>

        <p class="search-coverage" data-home-coverage-line>いま検索できる自治体の数を読み込んでいます。</p>
    </section>

    <section class="town-picker" aria-labelledby="town-title">
        <div class="section-head">
            <h2 id="town-title">自分のまちから探す</h2>
        </div>
        <div class="town-picker-body">
            <label class="field" for="home-town-input">
                <span>市区町村か都道府県の名前</span>
                <input id="home-town-input" type="search" autocomplete="off" placeholder="例：川崎市" data-home-town-input aria-controls="home-town-suggest">
            </label>
            <div class="town-suggest" id="home-town-suggest" data-home-town-suggest hidden></div>
            <div class="town-result" data-home-town-result hidden aria-live="polite"></div>
        </div>
    </section>
    </div>

    <?php if ($homeNews !== null): ?>
    <section class="section news-latest" aria-labelledby="home-news-title" data-home-news>
        <div class="section-head">
            <h2 id="home-news-title">各地のお知らせ</h2>
            <label class="control-field" for="home-news-prefecture">
                <span>都道府県</span>
                <select id="home-news-prefecture" data-home-news-prefecture>
                    <option value="all">全国</option>
                    <?php foreach (municipality_prefecture_names() as $prefCode => $prefName): ?>
                    <option value="<?php echo home_h((string)$prefCode); ?>"><?php echo home_h($prefName); ?></option>
                    <?php endforeach; ?>
                </select>
            </label>
        </div>
        <ol class="news-list" data-home-news-list aria-live="polite"><?php echo news_render_items($homeNews); ?></ol>
        <button type="button" class="button-quiet news-more" data-home-news-more hidden>残りも見る</button>
        <p class="note news-note">
            自治体のホームページの新着情報から、3 時間おきに集めています。この 7 日のお知らせを、日付の新しい順に並べています（全国は 1 自治体 1 件、都道府県を選ぶと 1 自治体 3 件まで）。題名を押すと自治体のサイトが開きます。
            <span data-home-news-updated><?php echo home_h(news_render_updated($homeNews)); ?></span>
        </p>
    </section>
    <?php endif; ?>

    <section class="section map-section" id="map" aria-labelledby="map-title">
        <div class="section-head">
            <h2 id="map-title">地図で見る</h2>
            <p data-home-display-count></p>
        </div>
        <p class="lead">点を押すと、その自治体で何を検索できるか、元の資料がどこにあるかが出ます。</p>

        <div class="toolbar">
            <div class="control-field">
                <span id="home-feature-label">地図に出す資料</span>
                <div class="feature-switch" role="group" aria-labelledby="home-feature-label">
                    <button type="button" class="is-active" data-doc="minutes" data-feature-filter="gijiroku">会議録</button>
                    <button type="button" data-doc="reiki" data-feature-filter="reiki">例規集</button>
                </div>
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
                    <option value="ready">検索できる</option>
                    <option value="issues">エラー・警告あり</option>
                    <option value="pending">準備中・未公開</option>
                </select>
            </label>
            <label class="control-field control-field-search" for="home-municipality-search">
                <span>自治体名で絞る</span>
                <input id="home-municipality-search" type="search" placeholder="例：熱海" data-home-search>
            </label>
        </div>

        <div class="map-workspace">
            <div class="map-stage">
                <div class="map-stage-head">
                    <p data-home-filter-hint>地図を読み込んでいます。</p>
                    <div class="legend" aria-label="点の色">
                        <span><i class="legend-dot legend-dot-minutes"></i>会議録</span>
                        <span><i class="legend-dot legend-dot-reiki"></i>例規集</span>
                        <span><i class="legend-dot legend-dot-pending"></i>準備中（色が薄い点）</span>
                    </div>
                </div>
                <div class="map-frame">
                    <div id="coverage-map" class="coverage-map" role="region" aria-label="自治体ごとの収集状況の地図（国土地理院の地図）" data-coverage-map></div>
                    <div class="map-loading" data-home-loading>自治体のデータを読み込んでいます。</div>
                </div>
            </div>

            <aside class="detail-panel" data-home-detail aria-live="polite">
                <div class="detail-empty">
                    <h3>自治体を選んでください</h3>
                    <p>地図の点か、下の一覧の名前を押すと、ここに出ます。</p>
                </div>
            </aside>
        </div>

        <div class="municipality-results">
            <h3 id="index-title">地図に出ている自治体の一覧</h3>
            <div class="municipality-list" data-home-grid>
                <div class="loading-panel">自治体のデータを読み込んでいます。</div>
            </div>
        </div>
        <p class="note">
            <span data-home-municipality-count>自治体の数を読み込んでいます</span>・<span data-home-generated-at>更新の時刻を読み込んでいます</span>
        </p>
    </section>

    <section class="section more-ways" aria-label="ほかの調べ方">
        <div class="more-way">
            <h2>AIに調べてもらう</h2>
            <p>Claude や ChatGPT にこのサイトをつなぐと、「〇〇市の議会で、空き家について何が話されたか調べて」のように頼めます。</p>
            <a class="button-quiet" href="/api-guide/">つなぎ方を見る</a>
        </div>
        <div class="more-way">
            <h2>元の資料を確かめる</h2>
            <p>どの文書にも、自治体のサイトへのリンクを付けています。自治体ごとの公開先は一覧にまとめています。</p>
            <a class="button-quiet" href="/municipalities/">自治体一覧を見る</a>
        </div>
        <div class="more-way">
            <h2>どこまで集まっているか</h2>
            <p>まだ集められていない自治体や、検索に載っていない文書もあります。いまの状況を自治体ごとに公開しています。</p>
            <a class="button-quiet" href="/status/">収集状況を見る</a>
        </div>
    </section>

    <p class="note home-boards-note">
        選挙ポスター掲示場のページは、この文書検索とは別のものです。<a href="/boards/">選挙ポスター掲示場を開く</a>
    </p>
</main>

<?php echo site_render_footer(); ?>

<script>
window.HOMEPAGE_API_URL = '/api/home.php';
// 実行中の処理は /status/ でだけ扱う。初めて来た人はまず検索したいので、
// 3秒ごとの状態ポーリングと1分ごとの全件再取得はトップでは行わない。
window.HOMEPAGE_TASK_STATUS_POLL_MS = 0;
window.HOMEPAGE_REFRESH_MS = 600000;
</script>
<script id="home-hyoka-slugs" type="application/json"><?php echo json_encode($homeHyokaSlugs, JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT); ?></script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" defer></script>
<script src="<?php echo home_h(site_asset_url('assets/js/municipality-coordinates.js')); ?>" defer></script>
<script src="<?php echo home_h(site_asset_url('assets/js/home.js')); ?>" defer></script>
<script src="<?php echo home_h(site_asset_url('assets/js/home-news.js')); ?>" defer></script>
</body>
</html>
