<?php
declare(strict_types=1);

require_once 'reiki/view_helpers.php';
require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'municipalities.php';

// ここでは検索条件の解釈、一覧データの取得、詳細表示用データの組み立てを行う。

// ─────────────────────────────────────
// 表示対象の自治体と例規集データの保存先を解決する
// ─────────────────────────────────────
$slug = get_slug();
if ($slug === '') {
    $slug = get_default_slug();
}
$municipality = municipality_entry($slug);
if ($municipality === null) {
    http_response_code(404);
    echo '自治体が見つかりません。';
    exit;
}
$reikiFeature = municipality_feature($slug, 'reiki') ?? [];
$featureAvailable = (bool)($reikiFeature['enabled'] ?? false);
$switcherItems = municipality_switcher_items('reiki');
$cleanHtmlDir = (string)($reikiFeature['clean_html_dir'] ?? '');
$classificationDir = (string)($reikiFeature['classification_dir'] ?? '');
$dbPath = (string)($reikiFeature['db_path'] ?? '');
$reikiImageUrl = (string)($reikiFeature['image_url'] ?? '/data/reiki/14130-kawasaki-shi/images');
$pageTitle = (string)($reikiFeature['title'] ?? ($municipality['name'] . '例規集 AI評価ビューア'));
$clearUrl = '/reiki/?slug=' . rawurlencode($slug);
$featureNotice = $featureAvailable ? '' : ($municipality['name'] . 'の例規集は準備中です。');

// ─────────────────────────────────────
// クエリ文字列を正規化し、複数選択フィルタも配列へ揃える
// ─────────────────────────────────────
$q = trim((string)($_GET['q'] ?? ''));
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
// まずは SQLite を優先し、無ければ旧データ用のファイル走査へ落とす
// ─────────────────────────────────────
$pdo = null;
if ($featureAvailable && $dbPath !== '' && file_exists($dbPath)) {
    try {
        $pdo = new PDO('sqlite:' . $dbPath);
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    } catch (Exception $e) {
        $pdo = null;
    }
}

// ─────────────────────────────────────
// Data Retrieval
// ─────────────────────────────────────
$records = [];
$titleCache = [];
$total = 0;

if ($pdo) {
    $where = ['1=1'];
    $params = [];

    if ($q !== '') {
        $where[] = '(title LIKE :q OR filename LIKE :q)';
        $params[':q'] = '%' . $q . '%';
    }
    
    if (!empty($filterClasses)) {
        $placeholders = [];
        foreach ($filterClasses as $i => $cls) {
            $ph = ":class_$i";
            $placeholders[] = $ph;
            $params[$ph] = $cls;
        }
        $where[] = 'primary_class IN (' . implode(',', $placeholders) . ')';
    }

    if (!empty($filterStances)) {
        $placeholders = [];
        foreach ($filterStances as $i => $val) {
            $ph = ":stance_$i";
            $placeholders[] = $ph;
            $params[$ph] = $val;
        }
        $where[] = 'combined_stance IN (' . implode(',', $placeholders) . ')';
    }

    if (!empty($filterDocTypes)) {
        $placeholders = [];
        foreach ($filterDocTypes as $i => $val) {
            $ph = ":doctype_$i";
            $placeholders[] = $ph;
            $params[$ph] = $val;
        }
        $where[] = "(CASE document_type WHEN '条例' THEN '条例' WHEN '規則' THEN '規則' WHEN '規程' THEN '規程' WHEN '要綱' THEN '要綱' ELSE 'その他' END) IN (" . implode(',', $placeholders) . ')';
    }

    $dirSql = $direction === 'asc' ? 'ASC' : 'DESC';
    $orderBy = "enactment_date $dirSql, sortable_kana ASC";
    
    if ($sort === 'kana') {
        $orderBy = "sortable_kana $dirSql";
    } elseif ($sort === 'score_necessity') {
        $orderBy = "CASE WHEN necessity_score = -1 THEN 1 ELSE 0 END ASC, necessity_score $dirSql, sortable_kana ASC";
    } elseif ($sort === 'score_fiscal') {
        $orderBy = "fiscal_impact_score $dirSql, sortable_kana ASC";
    } elseif ($sort === 'score_burden') {
        $orderBy = "regulatory_burden_score $dirSql, sortable_kana ASC";
    } elseif ($sort === 'score_effectiveness') {
        $orderBy = "policy_effectiveness_score $dirSql, sortable_kana ASC";
    } elseif ($sort === 'date') {
        $orderBy = "enactment_date $dirSql, sortable_kana ASC";
    }

    $sqlCount = "SELECT COUNT(*) FROM ordinances WHERE " . implode(' AND ', $where);
    $stmt = $pdo->prepare($sqlCount);
    $stmt->execute($params);
    $total = (int)$stmt->fetchColumn();

    $offset = ($page - 1) * $perPage;
    $sql = "SELECT filename, title, reading_kana, sortable_kana, primary_class, necessity_score, fiscal_impact_score, regulatory_burden_score, policy_effectiveness_score, responsible_department, enactment_date, updated_at, document_type FROM ordinances WHERE " . implode(' AND ', $where) . " ORDER BY $orderBy LIMIT :limit OFFSET :offset";
    $stmt = $pdo->prepare($sql);
    foreach ($params as $k => $v) {
        $stmt->bindValue($k, $v);
    }
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    
    while ($row = $stmt->fetch(PDO::FETCH_ASSOC)) {
        $name = $row['filename'] . '.html';
        $records[] = [
            'name' => $name,
            'path' => $cleanHtmlDir . DIRECTORY_SEPARATOR . $name,
            'mtime' => strtotime($row['updated_at']), 
            'title' => decode_html_text((string)($row['title'] ?? '')),
            'reading_kana' => $row['reading_kana'],
            'primary_class' => $row['primary_class'],
            'necessity_score' => $row['necessity_score'],
            'fiscal_impact_score' => $row['fiscal_impact_score'] ?? 0,
            'regulatory_burden_score' => $row['regulatory_burden_score'] ?? 0,
            'policy_effectiveness_score' => $row['policy_effectiveness_score'] ?? 0,
            'responsible_department' => $row['responsible_department'],
            'enactment_date' => $row['enactment_date'],
            'document_type' => normalize_document_type((string)($row['document_type'] ?? '')),
        ];
    }
} else {
    // SQLite 未整備の旧データでも最低限の一覧を出すため、HTML ファイルを直接走査する。
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
}

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
if ($pdo) {
    try {
        $stmt = $pdo->query("SELECT DISTINCT primary_class FROM ordinances WHERE primary_class != '' ORDER BY primary_class ASC");
        while ($row = $stmt->fetch(PDO::FETCH_ASSOC)) {
            $value = (string)$row['primary_class'];
            if (!in_array($value, $allClasses, true)) {
                $allClasses[] = $value;
            }
        }
        $stmt = $pdo->query("SELECT DISTINCT combined_stance FROM ordinances WHERE combined_stance != '' ORDER BY combined_stance ASC");
        while ($row = $stmt->fetch(PDO::FETCH_ASSOC)) {
            $allStances[] = $row['combined_stance'];
        }
    } catch (Exception $e) { }
}

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
