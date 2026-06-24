<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'municipalities.php';
require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'site_assets.php';

function search_h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
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

$requestedSlug = trim((string)($_GET['slug'] ?? ''));

$requestedDocType = strtolower(trim((string)($_GET['doc_type'] ?? ($_GET['type'] ?? 'minutes'))));
$selectedDocType = $requestedDocType === 'reiki' ? 'reiki' : 'minutes';
$requestedStartDate = search_normalize_date_input((string)($_GET['start_date'] ?? ''));
$requestedEndDate = search_normalize_date_input((string)($_GET['end_date'] ?? ''));
$requestedStartYear = $requestedStartDate !== ''
    ? substr($requestedStartDate, 0, 4)
    : search_normalize_year_input((string)($_GET['start_year'] ?? ''));
$requestedEndYear = $requestedEndDate !== ''
    ? substr($requestedEndDate, 0, 4)
    : search_normalize_year_input((string)($_GET['end_year'] ?? ''));
$yearOptions = search_year_options([$requestedStartYear, $requestedEndYear]);

$boot = [
    'apiUrl' => '/api/search',
    'municipalitiesApiUrl' => '/api/municipalities.php',
    'query' => trim((string)($_GET['q'] ?? '')),
    'docType' => $selectedDocType,
    'slug' => $requestedSlug,
    'prefCode' => trim((string)($_GET['pref_code'] ?? ($_GET['pref'] ?? ''))),
    'startDate' => $requestedStartDate,
    'endDate' => $requestedEndDate,
    'startYear' => $requestedStartYear,
    'endYear' => $requestedEndYear,
    'sort' => trim((string)($_GET['sort'] ?? 'date')),
    'prefectures' => $prefectures,
    'municipalities' => [],
];
?><!doctype html>
<html lang="ja">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>全国自治体 横断検索</title>
    <?php echo site_render_favicon_links(); ?>
    <link rel="stylesheet" href="<?php echo search_h(search_asset_url('css/search.css')); ?>">
</head>
<body>
<div class="app-shell">
    <header class="topbar">
        <a class="brand" href="/">宮部たつひこの自治体調査</a>
        <nav class="topnav" aria-label="検索範囲">
            <button type="button" data-doc-type="minutes">会議録</button>
            <button type="button" data-doc-type="reiki">例規集</button>
        </nav>
    </header>

    <main class="search-layout">
        <aside class="filters">
            <form id="search-form" class="search-form">
                <label class="field" for="search-query">
                    <span class="field-head">
                        <span>キーワード</span>
                        <button class="inline-help-button" type="button" data-query-help-open>検索構文</button>
                    </span>
                    <input id="search-query" name="q" type="search" value="<?php echo search_h((string)$boot['query']); ?>" autocomplete="off" autofocus>
                </label>
                <label class="field" for="search-pref">
                    <span>都道府県</span>
                    <select id="search-pref" name="pref_code">
                        <option value="">全国</option>
                        <?php foreach ($prefectures as $prefecture): ?>
                            <option value="<?php echo search_h((string)$prefecture['code']); ?>" <?php echo (string)$prefecture['code'] === (string)$boot['prefCode'] ? 'selected' : ''; ?>>
                                <?php echo search_h((string)$prefecture['name']); ?>
                            </option>
                        <?php endforeach; ?>
                    </select>
                </label>
                <label class="field" for="search-slug">
                    <span>自治体</span>
                    <input id="search-municipality-filter" class="municipality-filter-input" type="search" placeholder="自治体名で絞り込み" autocomplete="off">
                    <select id="search-slug" name="slug">
                        <option value="">読み込み中</option>
                    </select>
                    <span id="search-municipality-filter-status" class="field-status" aria-live="polite"></span>
                </label>
                <div class="split-fields date-fields">
                    <div class="field date-field">
                        <span>開始日</span>
                        <div class="date-filter-control">
                            <select id="search-start-year" name="start_year" aria-label="開始年">
                                <option value="">指定なし</option>
                                <?php foreach ($yearOptions as $year): ?>
                                    <option value="<?php echo search_h((string)$year); ?>" <?php echo (string)$year === (string)$boot['startYear'] ? 'selected' : ''; ?>>
                                        <?php echo search_h(search_year_option_label((int)$year)); ?>
                                    </option>
                                <?php endforeach; ?>
                            </select>
                            <label class="calendar-picker" for="search-start-date" title="日単位で指定">
                                <span class="calendar-icon" aria-hidden="true"></span>
                                <span class="sr-only">開始日を日単位で指定</span>
                                <input id="search-start-date" name="start_date" type="date" min="0001-01-01" max="9999-12-31" value="<?php echo search_h((string)$boot['startDate']); ?>">
                            </label>
                        </div>
                        <div id="search-start-date-status" class="date-detail-status" aria-live="polite"></div>
                    </div>
                    <div class="field date-field">
                        <span>終了日</span>
                        <div class="date-filter-control">
                            <select id="search-end-year" name="end_year" aria-label="終了年">
                                <option value="">指定なし</option>
                                <?php foreach ($yearOptions as $year): ?>
                                    <option value="<?php echo search_h((string)$year); ?>" <?php echo (string)$year === (string)$boot['endYear'] ? 'selected' : ''; ?>>
                                        <?php echo search_h(search_year_option_label((int)$year)); ?>
                                    </option>
                                <?php endforeach; ?>
                            </select>
                            <label class="calendar-picker" for="search-end-date" title="日単位で指定">
                                <span class="calendar-icon" aria-hidden="true"></span>
                                <span class="sr-only">終了日を日単位で指定</span>
                                <input id="search-end-date" name="end_date" type="date" min="0001-01-01" max="9999-12-31" value="<?php echo search_h((string)$boot['endDate']); ?>">
                            </label>
                        </div>
                        <div id="search-end-date-status" class="date-detail-status" aria-live="polite"></div>
                    </div>
                </div>
                <label class="field" for="search-sort">
                    <span>並び順</span>
                    <select id="search-sort" name="sort">
                        <option value="date">新しい順</option>
                        <option value="relevance">関連度</option>
                    </select>
                </label>
                <button class="primary-button" type="submit">検索</button>
            </form>

            <section class="facet-panel">
                <h2>絞り込み状況</h2>
                <div id="facet-list" class="facet-list"></div>
            </section>
        </aside>

        <section class="workspace">
            <div class="workspace-head">
                <div>
                    <p class="kicker">全国自治体</p>
                    <h1>全国自治体 横断検索</h1>
                    <div class="page-links">
                        <a href="/status/">処理状況</a>
                        <a href="/api-guide/">API解説</a>
                        <a href="/privacy/">プライバシー</a>
                    </div>
                </div>
                <div id="search-stats" class="stats"></div>
            </div>
            <div id="message-area" class="message-area"></div>
            <div id="results" class="results"></div>
            <div id="pager" class="pager"></div>
        </section>
    </main>
</div>

<div class="help-modal" hidden data-query-help-modal>
    <div class="help-modal-backdrop" data-query-help-close></div>
    <section class="help-modal-panel" role="dialog" aria-modal="true" aria-labelledby="query-help-title" tabindex="-1">
        <div class="help-modal-head">
            <h2 id="query-help-title">検索クエリの構文</h2>
            <button class="help-close-button" type="button" data-query-help-close aria-label="閉じる">×</button>
        </div>
        <div class="help-modal-body">
            <dl class="query-help-list">
                <dt>複数語</dt>
                <dd><code>盛土 メガソーラー</code><span>すべての語を含む文書を探します。</span></dd>
                <dt>完全一致</dt>
                <dd><code>"同和団体" 温泉</code><span>引用符内の語句をひとまとまりで探し、ほかの語と組み合わせます。</span></dd>
                <dt>いずれか</dt>
                <dd><code>盛土 OR 土砂</code><span>どちらかを含む文書を探します。</span></dd>
                <dt>除外</dt>
                <dd><code>メガソーラー NOT 促進</code><span>後ろの語を含む文書を除外します。</span></dd>
                <dt>組み合わせ</dt>
                <dd><code>(盛土 OR メガソーラー) 条例</code><span>括弧で条件をまとめられます。</span></dd>
            </dl>
            <p>記号をそのまま検索したい場合は、まず引用符で囲むのが安全です。</p>
        </div>
    </section>
</div>

<script id="search-boot" type="application/json"><?php echo json_encode($boot, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE); ?></script>
<script src="<?php echo search_h(search_asset_url('js/search.js')); ?>"></script>
</body>
</html>
