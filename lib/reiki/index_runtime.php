<?php
declare(strict_types=1);

require_once __DIR__ . DIRECTORY_SEPARATOR . 'view_helpers.php';
require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'municipalities.php';
require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'japanese_search.php';

// ここでは検索条件の解釈、一覧データの取得、詳細表示用データの組み立てを行う。
redirect_to_canonical_query_slug_if_needed();

// ─────────────────────────────────────
// 表示対象の自治体と例規集データの保存先を解決する
// ─────────────────────────────────────
$slug = get_slug();
if ($slug === '') {
    $slug = get_default_slug();
}
$requestSlug = municipality_public_slug($slug);
$municipality = municipality_entry($slug);
if ($municipality === null) {
    http_response_code(404);
    echo '自治体が見つかりません。';
    exit;
}
$reikiFeature = municipality_feature($slug, 'reiki') ?? [];
$featureAvailable = municipality_feature_enabled($slug, 'reiki');
$switcherItems = municipality_switcher_items('reiki');
$cleanHtmlDir = (string)($reikiFeature['clean_html_dir'] ?? '');
$classificationDir = (string)($reikiFeature['classification_dir'] ?? '');
// 保存先は掲示板と同じ canonical slug へ統一したので、既定画像 URL も slug から決め打ちできる。
$reikiImageUrl = (string)($reikiFeature['image_url'] ?? ('/data/reiki/' . rawurlencode($slug) . '/images'));
$pageTitle = (string)($reikiFeature['title'] ?? ($municipality['name'] . '例規集 AI評価ビューア'));
$clearUrl = '/reiki/?slug=' . rawurlencode($requestSlug);
$featureNotice = $featureAvailable ? '' : ($municipality['name'] . 'の例規集は準備中です。');

// ─────────────────────────────────────
// クエリ文字列を正規化し、複数選択フィルタも配列へ揃える
// ─────────────────────────────────────
$q = trim((string)($_GET['q'] ?? ''));
if ($q !== '') {
    $query = [
        'doc_type' => 'reiki',
        'q' => $q,
    ];
    if ($requestSlug !== '') {
        $query['slug'] = $requestSlug;
    }
    header('Location: /search/?' . http_build_query($query), true, 302);
    exit;
}
$preparedQuery = japanese_search_prepare_query($q);
$file = $featureAvailable ? trim((string)($_GET['file'] ?? '')) : '';
$sort = trim((string)($_GET['sort'] ?? 'date'));
$direction = trim((string)($_GET['dir'] ?? '')); 
$hasClassFilterParam = array_key_exists('class', $_GET);
$filterClasses = $_GET['class'] ?? [];
if (!is_array($filterClasses)) {
    $str = trim((string)$filterClasses);
    $filterClasses = $str !== '' ? [$str] : [];
}
$filterClasses = array_filter($filterClasses, fn($v) => (string)$v !== '');

$hasStanceFilterParam = array_key_exists('stance', $_GET);
$filterStances = $_GET['stance'] ?? [];
if (!is_array($filterStances)) {
    $str = trim((string)$filterStances);
    $filterStances = $str !== '' ? [$str] : [];
}
$filterStances = array_filter($filterStances, fn($v) => (string)$v !== '');

$documentTypeOptions = ['条例', '規則', '規程', '要綱', 'その他'];
$hasDocTypeFilterParam = array_key_exists('doctype', $_GET);
$filterDocTypes = $_GET['doctype'] ?? [];
if (!is_array($filterDocTypes)) {
    $str = trim((string)$filterDocTypes);
    $filterDocTypes = $str !== '' ? [$str] : [];
}
$filterDocTypes = array_values(array_filter(array_map(
    static fn($v) => normalize_document_type((string)$v),
    $filterDocTypes
), static fn($v) => in_array($v, $documentTypeOptions, true)));
$filterDocTypes = array_values(array_unique($filterDocTypes));

$page = max(1, (int)($_GET['page'] ?? 1));
$perPage = 50;

// Default direction based on sort type
if ($direction === '') {
    if ($sort === 'date') {
        $direction = 'desc';
    } elseif ($sort === 'score_fiscal' || $sort === 'score_burden') {
        $direction = 'desc';
    } else {
        $direction = 'asc';
    }
}
$direction = strtolower($direction) === 'asc' ? 'asc' : 'desc';

// ─────────────────────────────────────
// ─────────────────────────────────────
// Data Retrieval
// ─────────────────────────────────────
$records = [];
$titleCache = [];
$total = 0;

if ($featureAvailable && is_dir($cleanHtmlDir)) {
    $it = new RecursiveIteratorIterator(
        new RecursiveDirectoryIterator($cleanHtmlDir, FilesystemIterator::SKIP_DOTS)
    );
    foreach ($it as $entry) {
        if (!$entry instanceof SplFileInfo || !$entry->isFile()) {
            continue;
        }
        $name = $entry->getFilename();
        if (!preg_match('/_j\.html$/', $name)) {
            continue;
        }
        $records[] = [
            'name' => $name,
            'path' => $entry->getPathname(),
            'mtime' => $entry->getMTime(),
            'title' => '',
        ];
    }
}
$total = count($records);
$records = array_slice($records, ($page - 1) * $perPage, $perPage);

$totalPages = max(1, (int)ceil($total / $perPage));
$pagedRecords = $records;

// ─────────────────────────────────────
// 一覧から選ばれた例規を決める
// ─────────────────────────────────────
$selectedRecord = null;
if ($file !== '') {
    foreach ($pagedRecords as $record) {
        if ($record['name'] === $file) {
            $selectedRecord = $record;
            break;
        }
    }
    if ($selectedRecord === null) {
        $selectedRecord = [
            'name' => $file,
            'path' => $cleanHtmlDir . DIRECTORY_SEPARATOR . $file,
        ];
    }
}

$start = ($page - 1) * $perPage + 1;
$end = min($total, $page * $perPage);

// Canonical class list
$canonicalPrimaryClasses = [
    'A_法定必須_維持前提',
    'B_自治体裁量だが基幹_効率化対象',
    'C_裁量的サービス_縮小統合候補',
    'D_理念宣言中心_実施見直し候補',
    'E_規制許認可中心_規制緩和候補',
    'F_手数料使用料連動_負担軽減候補',
    'G_歴史的・形式的_現状維持',
];

$allClasses = $canonicalPrimaryClasses;
$allStances = [];

// ─────────────────────────────────────
// 選択された例規の本文と AI 評価結果を読み込む
// ─────────────────────────────────────
$selectedHtml = '';
$selectedContentHtml = '';
$selectedText = '';
$selectedTitle = '';
$selectedClassification = null;

if ($selectedRecord !== null) {
    $cleanHtmlPath = $cleanHtmlDir . DIRECTORY_SEPARATOR . $selectedRecord['name'];
    if (is_file($cleanHtmlPath)) {
        $selectedContentHtml = sanitize_law_html(read_text_auto($cleanHtmlPath), $reikiImageUrl);
        if (preg_match('/<div class="law-title">([^<]+)<\/div>/', $selectedContentHtml, $m)) {
            $selectedTitle = decode_html_text($m[1]);
        }
    }
    
    if (empty($selectedText) && is_file($cleanHtmlPath)) {
        $selectedText = normalize_text(read_text_auto($cleanHtmlPath));
    }
    
    $selectedClassification = load_classification_for_record($selectedRecord, $cleanHtmlDir, $classificationDir);
}

foreach ($pagedRecords as $record) {
    resolve_record_title($record, $titleCache);
}

$isLanding = ($selectedRecord === null && $q === '' && $page === 1 && !$hasClassFilterParam && !$hasStanceFilterParam && !$hasDocTypeFilterParam);
