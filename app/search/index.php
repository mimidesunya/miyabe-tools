<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'municipalities.php';
require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'site_assets.php';

function search_h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

// クエリの値は配列でも来る（?q[]=x）。そのまま (string) にすると Warning が画面に出るので、文字列だけを受ける。
function search_query_param(string $key, string $default = ''): string
{
    $value = $_GET[$key] ?? $default;
    return is_scalar($value) ? trim((string)$value) : $default;
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

function search_normalize_date_input(string $value): string
{
    $value = trim($value);
    if (preg_match('/^(\d{4})-(\d{2})-(\d{2})$/', $value, $matches) !== 1) {
        return '';
    }
    $year = (int)$matches[1];
    $month = (int)$matches[2];
    $day = (int)$matches[3];
    if ($year < 1 || $year > 9999 || !checkdate($month, $day, $year)) {
        return '';
    }
    return sprintf('%04d-%02d-%02d', $year, $month, $day);
}

function search_normalize_year_input(string $value): string
{
    $value = trim($value);
    if (preg_match('/^\d{1,4}$/', $value) !== 1) {
        return '';
    }
    return (string)max(1, min(9999, (int)$value));
}

function search_era_year_label(string $eraName, int $yearInEra): string
{
    return $eraName . ($yearInEra === 1 ? '元' : (string)$yearInEra) . '年';
}

function search_japanese_era_labels(int $year): array
{
    if ($year === 2019) {
        return [search_era_year_label('平成', 31), search_era_year_label('令和', 1)];
    }
    if ($year >= 2020) {
        return [search_era_year_label('令和', $year - 2018)];
    }
    if ($year === 1989) {
        return [search_era_year_label('昭和', 64), search_era_year_label('平成', 1)];
    }
    if ($year >= 1990) {
        return [search_era_year_label('平成', $year - 1988)];
    }
    if ($year === 1926) {
        return [search_era_year_label('大正', 15), search_era_year_label('昭和', 1)];
    }
    if ($year >= 1927) {
        return [search_era_year_label('昭和', $year - 1925)];
    }
    if ($year === 1912) {
        return [search_era_year_label('明治', 45), search_era_year_label('大正', 1)];
    }
    if ($year >= 1913) {
        return [search_era_year_label('大正', $year - 1911)];
    }
    if ($year >= 1868) {
        return [search_era_year_label('明治', $year - 1867)];
    }
    return [];
}

function search_year_option_label(int $year): string
{
    $eraLabels = search_japanese_era_labels($year);
    return (string)$year . '年' . ($eraLabels !== [] ? '（' . implode('/', $eraLabels) . '）' : '');
}

function search_year_options(array $selectedYears): array
{
    $tokyo = new DateTimeZone('Asia/Tokyo');
    $currentYear = (int)(new DateTimeImmutable('now', $tokyo))->format('Y');
    $years = range($currentYear, 1947);
    foreach ($selectedYears as $selectedYear) {
        $normalized = search_normalize_year_input((string)$selectedYear);
        if ($normalized !== '') {
            $years[] = (int)$normalized;
        }
    }
    $years = array_values(array_unique(array_filter($years, static fn($year): bool => $year >= 1 && $year <= 9999)));
    rsort($years, SORT_NUMERIC);
    return $years;
}

$prefectures = [];
foreach (municipality_prefecture_names() as $code => $name) {
    $prefectures[] = ['code' => (string)$code, 'name' => (string)$name];
}

// コード（14130）・名前（川崎市）・古い slug でも来るので、正規の slug にそろえて渡す。
// そろえないと自治体の選択肢に無い値になり、画面は「全国」と出して次の検索で絞り込みを落とす。
$requestedSlug = search_query_param('slug');
$resolvedSlug = $requestedSlug !== '' ? resolve_municipality_slug($requestedSlug) : '';
if ($resolvedSlug !== '') {
    $requestedSlug = municipality_public_slug($resolvedSlug);
}

$requestedDocType = strtolower(search_query_param('doc_type', search_query_param('type', 'minutes')));
$selectedDocType = in_array($requestedDocType, ['reiki', 'hyoka'], true) ? $requestedDocType : 'minutes';
$requestedFiscalYear = search_query_param('fiscal_year');
$requestedFiscalYear = preg_match('/^[1-9]\d{3}$/', $requestedFiscalYear) === 1 ? $requestedFiscalYear : '';
$hyokaKinds = [
    'project_evaluation' => '事務事業評価',
    'policy_evaluation' => '施策・政策評価',
    'external_review' => '外部評価',
    'overview' => '概要・一覧',
];
$requestedKind = search_query_param('kind');
$requestedKind = isset($hyokaKinds[$requestedKind]) ? $requestedKind : '';
$requestedStartDate = search_normalize_date_input(search_query_param('start_date'));
$requestedEndDate = search_normalize_date_input(search_query_param('end_date'));
$requestedStartYear = $requestedStartDate !== ''
    ? substr($requestedStartDate, 0, 4)
    : search_normalize_year_input(search_query_param('start_year'));
$requestedEndYear = $requestedEndDate !== ''
    ? substr($requestedEndDate, 0, 4)
    : search_normalize_year_input(search_query_param('end_year'));
$yearOptions = search_year_options([$requestedStartYear, $requestedEndYear]);

$boot = [
    'apiUrl' => '/api/search',
    'municipalitiesApiUrl' => '/api/municipalities.php',
    'query' => search_query_param('q'),
    'docType' => $selectedDocType,
    'slug' => $requestedSlug,
    'prefCode' => search_query_param('pref_code', search_query_param('pref')),
    'startDate' => $requestedStartDate,
    'endDate' => $requestedEndDate,
    'startYear' => $requestedStartYear,
    'endYear' => $requestedEndYear,
    'fiscalYear' => $requestedFiscalYear,
    'kind' => $requestedKind,
    'sort' => search_query_param('sort', 'date'),
    // 共有した URL や再読み込みで同じ頁を開けるように。API は 10,000 件目まで（20 件ずつで 500 頁）。
    'page' => max(1, min(500, (int)search_query_param('page', '1'))),
    'prefectures' => $prefectures,
    'municipalities' => [],
];
?><!doctype html>
<html lang="ja">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>全国自治体 横断検索｜自治体マップ</title>
    <?php echo site_render_page_meta(
        '全国自治体 横断検索｜自治体マップ',
        '全国の自治体の議会会議録・例規集（条例・規則）・事務事業評価を全文検索できます。都道府県・自治体などで絞り込み、原典URLまでたどれます。無料・登録不要。',
        '/search/'
    ); ?>
    <?php echo site_render_favicon_links(); ?>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
    <?php echo site_render_stylesheet(); ?>
    <link rel="stylesheet" href="<?php echo search_h(search_asset_url('css/search.css')); ?>">
</head>
<body class="search-page" data-doc="<?php echo search_h($selectedDocType); ?>">
<?php echo site_render_header('search'); ?>

<main id="main" class="page">
    <h1 class="visually-hidden">自治体の文書を検索</h1>

    <?php /* 上の箱: 資料の種類と言葉。スマホでも、ここと結果が先に見えるようにする。絞り込みは下の別の箱。 */ ?>
    <form id="search-form" class="search-box" role="search" aria-label="自治体の文書を検索">
        <div class="doc-tabs" role="group" aria-label="探す資料">
            <button type="button" class="doc-tab" data-doc="minutes" data-doc-type="minutes">
                <span class="doc-tab-main">議会の発言</span>
                <span class="doc-tab-sub">会議録</span>
            </button>
            <button type="button" class="doc-tab" data-doc="reiki" data-doc-type="reiki">
                <span class="doc-tab-main">条例・規則</span>
                <span class="doc-tab-sub">例規集</span>
            </button>
            <button type="button" class="doc-tab" data-doc="hyoka" data-doc-type="hyoka">
                <span class="doc-tab-main">事業の評価</span>
                <span class="doc-tab-sub">事務事業評価</span>
            </button>
        </div>
        <div class="search-box-body">
            <div class="search-row">
                <label class="visually-hidden" for="search-query">調べたい言葉</label>
                <input id="search-query" name="q" type="search" enterkeyhint="search" value="<?php echo search_h((string)$boot['query']); ?>" autocomplete="off" placeholder="調べたい言葉を入れる">
                <button class="button" type="submit">検索</button>
            </div>
            <div class="search-row-sub">
                <button class="text-button" type="button" data-query-help-open>検索のコツ</button>
                <button class="filter-toggle" type="button" data-filter-toggle aria-expanded="false" aria-controls="search-filters">
                    絞り込み<span data-filter-count></span>
                </button>
            </div>
        </div>
    </form>

    <div class="search-layout">
        <aside class="filters" id="search-filters" data-filters aria-label="絞り込み">
            <h2>絞り込み</h2>
            <label class="field" for="search-pref">
                <span>都道府県</span>
                <select id="search-pref" name="pref_code" form="search-form">
                    <option value="">全国</option>
                    <?php foreach ($prefectures as $prefecture): ?>
                        <option value="<?php echo search_h((string)$prefecture['code']); ?>" <?php echo (string)$prefecture['code'] === (string)$boot['prefCode'] ? 'selected' : ''; ?>>
                            <?php echo search_h((string)$prefecture['name']); ?>
                        </option>
                    <?php endforeach; ?>
                </select>
            </label>
            <div class="field">
                <label class="field-label" for="search-municipality-filter">自治体</label>
                <input id="search-municipality-filter" class="municipality-filter-input" type="search" placeholder="名前を入れて候補を減らす" autocomplete="off" form="search-form">
                <select id="search-slug" name="slug" form="search-form" aria-label="自治体を選ぶ">
                    <option value="">読み込み中</option>
                </select>
                <span id="search-municipality-filter-status" class="field-status" aria-live="polite"></span>
            </div>
            <div id="search-date-fields" class="date-fields"<?php echo $selectedDocType === 'hyoka' ? ' hidden' : ''; ?>>
                <div class="field date-field">
                    <span>いつから</span>
                    <div class="date-filter-control">
                        <select id="search-start-year" name="start_year" aria-label="開始の年" form="search-form">
                            <option value="">指定なし</option>
                            <?php foreach ($yearOptions as $year): ?>
                                <option value="<?php echo search_h((string)$year); ?>" <?php echo (string)$year === (string)$boot['startYear'] ? 'selected' : ''; ?>>
                                    <?php echo search_h(search_year_option_label((int)$year)); ?>
                                </option>
                            <?php endforeach; ?>
                        </select>
                        <label class="calendar-picker" for="search-start-date" title="日にちまで指定する">
                            <span class="calendar-icon" aria-hidden="true"></span>
                            <span class="sr-only">開始の日を指定する</span>
                            <input id="search-start-date" name="start_date" type="date" min="0001-01-01" max="9999-12-31" value="<?php echo search_h((string)$boot['startDate']); ?>" form="search-form">
                        </label>
                    </div>
                    <div id="search-start-date-status" class="date-detail-status" aria-live="polite"></div>
                </div>
                <div class="field date-field">
                    <span>いつまで</span>
                    <div class="date-filter-control">
                        <select id="search-end-year" name="end_year" aria-label="終了の年" form="search-form">
                            <option value="">指定なし</option>
                            <?php foreach ($yearOptions as $year): ?>
                                <option value="<?php echo search_h((string)$year); ?>" <?php echo (string)$year === (string)$boot['endYear'] ? 'selected' : ''; ?>>
                                    <?php echo search_h(search_year_option_label((int)$year)); ?>
                                </option>
                            <?php endforeach; ?>
                        </select>
                        <label class="calendar-picker" for="search-end-date" title="日にちまで指定する">
                            <span class="calendar-icon" aria-hidden="true"></span>
                            <span class="sr-only">終了の日を指定する</span>
                            <input id="search-end-date" name="end_date" type="date" min="0001-01-01" max="9999-12-31" value="<?php echo search_h((string)$boot['endDate']); ?>" form="search-form">
                        </label>
                    </div>
                    <div id="search-end-date-status" class="date-detail-status" aria-live="polite"></div>
                </div>
            </div>
            <div id="search-hyoka-fields" class="hyoka-fields"<?php echo $selectedDocType !== 'hyoka' ? ' hidden' : ''; ?>>
                <label class="field" for="search-fiscal-year">
                    <span>年度（西暦）</span>
                    <input id="search-fiscal-year" name="fiscal_year" type="number" inputmode="numeric" min="1000" max="9999" step="1" placeholder="例：2024" value="<?php echo search_h($requestedFiscalYear); ?>" form="search-form">
                </label>
                <label class="field" for="search-hyoka-kind">
                    <span>評価の種類</span>
                    <select id="search-hyoka-kind" name="kind" form="search-form">
                        <option value="">すべて</option>
                        <?php foreach ($hyokaKinds as $kind => $label): ?>
                            <option value="<?php echo search_h($kind); ?>"<?php echo $requestedKind === $kind ? ' selected' : ''; ?>><?php echo search_h($label); ?></option>
                        <?php endforeach; ?>
                    </select>
                </label>
            </div>
            <label class="field" for="search-sort">
                <span>並べ方</span>
                <select id="search-sort" name="sort" form="search-form">
                    <option value="date">新しい順</option>
                    <option value="relevance">言葉がよく合う順</option>
                </select>
            </label>
            <div class="filter-actions">
                <button class="button" type="submit" form="search-form">この条件で検索</button>
                <button class="text-button" type="button" data-filter-reset>絞り込みをやめる</button>
            </div>

            <section class="facet-panel">
                <h2>多い都道府県</h2>
                <p class="note">押すと、その都道府県に絞ります。</p>
                <div id="facet-list" class="facet-list"></div>
            </section>
        </aside>

        <section class="workspace" aria-label="検索の結果">
            <div id="search-stats" class="result-summary" aria-live="polite"></div>
            <div id="message-area" class="message-area"></div>

            <?php /* 該当した自治体を地図に出す。どこの話なのかは一覧を読んでも掴めない */ ?>
            <section class="hit-map-panel" id="hit-map-panel" hidden>
                <div class="hit-map-head">
                    <h2 id="hit-map-title">見つかった自治体の地図</h2>
                    <span class="hit-map-summary" id="hit-map-summary"></span>
                    <button class="hit-map-toggle" type="button" id="hit-map-toggle"
                            aria-expanded="true" aria-controls="hit-map">たたむ</button>
                </div>
                <div class="hit-map" id="hit-map"></div>
                <p class="hit-map-note" id="hit-map-note"></p>
            </section>

            <div id="results" class="results"></div>
            <div id="pager" class="pager"></div>
        </section>
    </div>
</main>

<?php echo site_render_footer(); ?>

<div class="help-modal" hidden data-query-help-modal>
    <div class="help-modal-backdrop" data-query-help-close></div>
    <section class="help-modal-panel" role="dialog" aria-modal="true" aria-labelledby="query-help-title" tabindex="-1">
        <div class="help-modal-head">
            <h2 id="query-help-title">検索のコツ</h2>
            <button class="help-close-button" type="button" data-query-help-close aria-label="閉じる">×</button>
        </div>
        <div class="help-modal-body">
            <dl class="query-help-list">
                <dt>言葉を 2 つ以上</dt>
                <dd><code>盛土 メガソーラー</code><span>空白で区切ると、全部の言葉が出てくる文書を探します。</span></dd>
                <dt>言い回しどおり</dt>
                <dd><code>"同和団体" 温泉</code><span><code>"</code>で囲んだ中は、ひとまとまりの言い回しとして探します。ほかの言葉とも組み合わせられます。</span></dd>
                <dt>どちらか</dt>
                <dd><code>盛土 OR 土砂</code><span>どちらかが出てくる文書を探します。</span></dd>
                <dt>外す</dt>
                <dd><code>メガソーラー NOT 促進</code><span>後ろの言葉が出てくる文書を外します。</span></dd>
                <dt>組み合わせ</dt>
                <dd><code>(盛土 OR メガソーラー) 条例</code><span>括弧で条件をまとめられます。括弧が無いと、OR は左右の言葉のまとまり全体を分けます（<code>条例 盛土 OR 土砂</code> は「条例と盛土」か「土砂」）。</span></dd>
            </dl>
            <p>全角で書いても同じです。記号（括弧・| など）は検索の対象になりません。OR・NOT という言葉そのものを探したいときは、<code>"</code>で囲んでください。</p>
            <p>1 文字の言葉は、ほかの字と続いて書かれた箇所を取りこぼします。2 文字以上で探してください。自治体を選べば、言葉を入れなくても新しい順の一覧が出ます。</p>
        </div>
    </section>
</div>

<?php /* 検索語など URL の値が入るので、< > & ' " を Unicode の逃がしにして </script> で抜け出せないようにする */ ?>
<script id="search-boot" type="application/json"><?php echo json_encode($boot, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE | JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT); ?></script>
<?php /* 地図はトップと同じ Leaflet と地理院タイル。座標は自治体コードで引く */ ?>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="<?php echo search_h(site_asset_url('assets/js/municipality-coordinates.js')); ?>"></script>
<script src="<?php echo search_h(search_asset_url('js/search.js')); ?>"></script>
</body>
</html>
