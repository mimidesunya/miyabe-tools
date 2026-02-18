<?php
declare(strict_types=1);

// Shared logic for score display (Color and Label)
function get_score_style_and_label(string $key, float|int $val): array {
    $style = '';
    $suffix = '';
    
    // Default color logic
    if ($key === 'necessity_score') {
        if ($val == -1) {
            $style = 'color:#94a3b8; font-style:italic;'; // Slate-400 (N/A)
            $suffix = '(対象外)';
        } elseif ($val >= 80) {
            $style = 'color:#15803d; font-weight:bold;'; // Green (High Need)
            $suffix = '(高)';
        } elseif ($val >= 50) {
            $style = 'color:#334155;'; // Slate (Normal)
        } elseif ($val >= 21) {
            $style = 'color:#b45309; font-weight:bold;'; // Amber (Low)
            $suffix = '(低)';
        } else {
            $style = 'color:#dc2626; font-weight:bold; background:#fef2f2; padding:0 2px; border-radius:3px;'; // Red (Very Low)
            $suffix = '(不要?)';
        }
    } elseif ($key === 'fiscal_impact_score') {
        if ($val >= 4.0) {
            $style = 'color:#dc2626; font-weight:bold; background:#fef2f2; padding:0 2px; border-radius:3px;'; // Red (High Cost)
            $suffix = '(重)';
        } elseif ($val >= 2.0) {
            $style = 'color:#334155;'; 
        } else {
            $style = 'color:#15803d; font-weight:bold;'; // Green (Low Cost)
            $suffix = '(軽)';
        }
    } elseif ($key === 'regulatory_burden_score') {
        if ($val >= 4.0) {
            $style = 'color:#dc2626; font-weight:bold; background:#fef2f2; padding:0 2px; border-radius:3px;'; // Red (High Burden)
            $suffix = '(重)';
        } elseif ($val >= 2.0) {
            $style = 'color:#334155;'; 
        } else {
            $style = 'color:#15803d; font-weight:bold;'; // Green (Low Burden)
            $suffix = '(軽)';
        }
    } elseif ($key === 'policy_effectiveness_score') {
        if ($val >= 4.0) {
            $style = 'color:#15803d; font-weight:bold;'; // Green (High Effect)
            $suffix = '(高)';
        } elseif ($val >= 2.0) {
            $style = 'color:#334155;';
        } else {
            $style = 'color:#dc2626; font-weight:bold; background:#fef2f2; padding:0 2px; border-radius:3px;'; // Red (Low Effect)
            $suffix = '(無効?)';
        }
    }
    
    return [$style, $suffix];
}

function h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function get_stance_label(string $stance): string
{
    return match ($stance) {
        '合致', '適合' => '適合',
        '一部合致', '概ね適合' => '概ね適合',
        '中立/不明', '判断保留' => '判断保留',
        '衝突', '要見直し' => '要見直し',
        default => $stance,
    };
}

function read_text_auto(string $path): string
{
    $encodings = ['UTF-8', 'SJIS-win', 'CP932', 'EUC-JP', 'ISO-2022-JP'];
    $raw = @file_get_contents($path);
    if ($raw === false) {
        return '';
    }

    foreach ($encodings as $enc) {
        $converted = @mb_convert_encoding($raw, 'UTF-8', $enc);
        if ($converted !== false && $converted !== '') {
            return $converted;
        }
    }

    return (string)$raw;
}

function extract_title(string $html, string $fallback): string
{
    if (preg_match('/<title[^>]*>(.*?)<\/title>/is', $html, $m)) {
        $title = trim(strip_tags($m[1]));
        if ($title !== '') {
            return $title;
        }
    }

    if (preg_match('/○([^\r\n<]{2,120})/u', $html, $m)) {
        $title = trim($m[1]);
        if ($title !== '') {
            return $title;
        }
    }

    return $fallback;
}

function normalize_text(string $html): string
{
    $text = preg_replace('/<script\b[^>]*>.*?<\/script>/is', '', $html);
    $text = preg_replace('/<style\b[^>]*>.*?<\/style>/is', '', (string)$text);
    $text = preg_replace('/<br\s*\/?>/i', "\n", (string)$text);
    $text = strip_tags((string)$text);
    $text = html_entity_decode((string)$text, ENT_QUOTES | ENT_HTML5, 'UTF-8');
    $text = preg_replace("/\r\n|\r/", "\n", (string)$text);
    $text = preg_replace("/\n{3,}/", "\n\n", (string)$text);
    return trim((string)$text);
}

// Helper to get formatted score HTML (Value + Text Label colorized)
function get_score_html(string $key, float|int $val) {
    list($style, $suffix) = get_score_style_and_label($key, $val);
    return "<span style=\"{$style}\">" . h((string)$val) . " <span style=\"font-size:0.9em; opacity:0.9;\">{$suffix}</span></span>";
}

function inner_html(DOMNode $node): string {
    $html = '';
    foreach ($node->childNodes as $child) {
        $html .= $node->ownerDocument?->saveHTML($child) ?? '';
    }
    return $html;
}

function resolve_record_title(array $record, array &$cache): string {
    $name = (string)($record['name'] ?? '');
    if ($name !== '' && isset($cache[$name])) {
        return $cache[$name];
    }
    // Optimization: if title is already in record (from DB), use it.
    if (!empty($record['title'])) {
        $cache[$name] = $record['title'];
        return $record['title'];
    }
    
    $html = read_text_auto((string)$record['path']);
    $title = extract_title($html, $name !== '' ? $name : '無題');
    if ($name !== '') {
        $cache[$name] = $title;
    }
    return $title;
}

function sanitize_law_html(string $html): string
{
    $dom = new DOMDocument();
    libxml_use_internal_errors(true);
    $dom->loadHTML('<?xml encoding="utf-8" ?>' . $html, LIBXML_HTML_NOIMPLIED | LIBXML_HTML_NODEFDTD);
    libxml_clear_errors();

    $xpath = new DOMXPath($dom);
    foreach ($xpath->query('//script|//style') as $node) {
        if ($node && $node->parentNode) {
            $node->parentNode->removeChild($node);
        }
    }

    foreach ($xpath->query('//*') as $el) {
        if (!($el instanceof DOMElement)) {
            continue;
        }

        $toRemove = [];
        foreach ($el->attributes as $attr) {
            $name = strtolower($attr->name);
            if (str_starts_with($name, 'on')) {
                $toRemove[] = $attr->name;
            }
            if (($name === 'href' || $name === 'src') && preg_match('/^\s*javascript:/i', $attr->value)) {
                $toRemove[] = $attr->name;
            }
        }
        foreach ($toRemove as $name) {
            $el->removeAttribute($name);
        }
    }

    // Rewrite image src to point to downloaded images
    foreach ($xpath->query('//img[@src]') as $img) {
        if ($img instanceof DOMElement) {
            $src = $img->getAttribute('src');
            // Only rewrite if it's a filename (not a full URL)
            if ($src && !preg_match('#^(https?://|//)#i', $src)) {
                // Extract just the filename
                $filename = basename($src);
                // Rewrite to absolute path from web root
                $img->setAttribute('src', '/data/reiki/kawasaki_images/' . $filename);
            }
        }
    }

    return $dom->saveHTML() ?: '';
}

function extract_law_content_html(string $html): string
{
    $dom = new DOMDocument();
    libxml_use_internal_errors(true);
    $dom->loadHTML('<?xml encoding="utf-8" ?>' . $html, LIBXML_HTML_NOIMPLIED | LIBXML_HTML_NODEFDTD);
    libxml_clear_errors();

    $xpath = new DOMXPath($dom);
    $nodes = $xpath->query("//div[contains(concat(' ', normalize-space(@class), ' '), ' USER-SET-STYLE ')]");
    if ($nodes instanceof DOMNodeList && $nodes->length > 0) {
        $raw = inner_html($nodes->item(0));
        return sanitize_law_html($raw);
    }

    $body = $xpath->query('//body');
    if ($body instanceof DOMNodeList && $body->length > 0) {
        return sanitize_law_html(inner_html($body->item(0)));
    }

    return '';
}

function load_classification_for_record(array $record, string $dataDir, string $classificationDir): ?array
{
    $sourcePath = (string)($record['path'] ?? '');
    if ($sourcePath === '') {
        return null;
    }
    $sourceReal = realpath($sourcePath);
    $dataReal = realpath($dataDir);
    if ($sourceReal === false || $dataReal === false) {
        return null;
    }

    $prefix = rtrim($dataReal, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR;
    if (!str_starts_with($sourceReal, $prefix)) {
        return null;
    }

    $relative = substr($sourceReal, strlen($prefix));
    if ($relative === false || $relative === '') {
        return null;
    }

    $relativeJson = preg_replace('/\.html$/i', '.json', str_replace(['/', '\\'], DIRECTORY_SEPARATOR, $relative));
    if ($relativeJson === null || $relativeJson === '') {
        return null;
    }

    $classificationPath = rtrim($classificationDir, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR . $relativeJson;
    if (!is_file($classificationPath)) {
        return null;
    }

    $json = read_text_auto($classificationPath);
    $row = json_decode($json, true);
    return is_array($row) ? $row : null;
}


$workspaceRoot = dirname(__DIR__, 2);
$dataDir = $workspaceRoot . DIRECTORY_SEPARATOR . 'data' . DIRECTORY_SEPARATOR . 'reiki' . DIRECTORY_SEPARATOR . 'kawasaki';
$cleanHtmlDir = $workspaceRoot . DIRECTORY_SEPARATOR . 'data' . DIRECTORY_SEPARATOR . 'reiki' . DIRECTORY_SEPARATOR . 'kawasaki_html';
$classificationDir = $workspaceRoot . DIRECTORY_SEPARATOR . 'data' . DIRECTORY_SEPARATOR . 'reiki' . DIRECTORY_SEPARATOR . 'kawasaki_json';
$dbPath = $workspaceRoot . DIRECTORY_SEPARATOR . 'data' . DIRECTORY_SEPARATOR . 'reiki' . DIRECTORY_SEPARATOR . 'ordinances.sqlite';

$q = trim((string)($_GET['q'] ?? ''));
$file = trim((string)($_GET['file'] ?? ''));
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

$page = max(1, (int)($_GET['page'] ?? 1));
$perPage = 50;

// Set default direction based on sort type if not specified
if ($direction === '') {
    if ($sort === 'date') {
        $direction = 'desc'; // Date defaults to descending (Newest first)
    } elseif ($sort === 'score_fiscal' || $sort === 'score_burden') {
        $direction = 'desc'; // High score (5) is Bad (Heavy burden/High cost) -> Show worst first
    } else {
        $direction = 'asc'; // Necessity/Effectiveness (Low=Bad), Kana (A-Z) -> Show worst first
    }
}
$direction = strtolower($direction) === 'asc' ? 'asc' : 'desc';

$pdo = null;
if (file_exists($dbPath)) {
    try {
        $pdo = new PDO('sqlite:' . $dbPath);
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    } catch (Exception $e) {
        $pdo = null;
    }
}

$records = [];
$titleCache = []; // Initialize title cache
$total = 0;

if ($pdo) {
    // DB-driven mode
    $where = ['1=1'];
    $params = [];

    if ($q !== '') {
        // Simple LIKE search on title
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

    // Build Order By
    $dirSql = $direction === 'asc' ? 'ASC' : 'DESC';
    $orderBy = "enactment_date $dirSql, sortable_kana ASC"; // Default
    
    if ($sort === 'kana') {
        $orderBy = "sortable_kana $dirSql";
    } elseif ($sort === 'score_necessity') {
        // -1 (N/A) always appears last
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

    // Count total
    $sqlCount = "SELECT COUNT(*) FROM ordinances WHERE " . implode(' AND ', $where);
    $stmt = $pdo->prepare($sqlCount);
    $stmt->execute($params);
    $total = (int)$stmt->fetchColumn();

    // Fetch records
    $offset = ($page - 1) * $perPage;
    $sql = "SELECT filename, title, reading_kana, sortable_kana, primary_class, necessity_score, fiscal_impact_score, regulatory_burden_score, policy_effectiveness_score, responsible_department, enactment_date, updated_at FROM ordinances WHERE " . implode(' AND ', $where) . " ORDER BY $orderBy LIMIT :limit OFFSET :offset";
    $stmt = $pdo->prepare($sql);
    foreach ($params as $k => $v) {
        $stmt->bindValue($k, $v);
    }
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    
    while ($row = $stmt->fetch(PDO::FETCH_ASSOC)) {
        // Restore .html extension for compatibility with existing logic
        $name = $row['filename'] . '.html';
        $records[] = [
            'name' => $name,
            'path' => $dataDir . DIRECTORY_SEPARATOR . $name,
            'mtime' => strtotime($row['updated_at']), 
            'title' => $row['title'],
            'reading_kana' => $row['reading_kana'],
            'primary_class' => $row['primary_class'],
            'necessity_score' => $row['necessity_score'],
            'fiscal_impact_score' => $row['fiscal_impact_score'] ?? 0,
            'regulatory_burden_score' => $row['regulatory_burden_score'] ?? 0,
            'policy_effectiveness_score' => $row['policy_effectiveness_score'] ?? 0,
            'responsible_department' => $row['responsible_department'],
            'enactment_date' => $row['enactment_date'],
        ];
    }
} else {
    // Fallback to file scanning (Legacy)
    if (is_dir($dataDir)) {
        $it = new RecursiveIteratorIterator(
            new RecursiveDirectoryIterator($dataDir, FilesystemIterator::SKIP_DOTS)
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
                'title' => '', // Will be loaded later
            ];
        }
    }
    
    // Sort logic for fallback... omit for brevity as DB should work
    $total = count($records);
    $records = array_slice($records, ($page - 1) * $perPage, $perPage);
}

$totalPages = max(1, (int)ceil($total / $perPage));
$pagedRecords = $records;  // Logic slightly changed, $records is already paged in DB mode

// Ensure selectedRecord logic still works...
$selectedRecord = null;
if ($file !== '') {
    // If DB mode, fetch specific record info if not in current page
    // For now, simple scan of current page or on-demand object creation
    foreach ($pagedRecords as $record) {
        if ($record['name'] === $file) {
            $selectedRecord = $record;
            break;
        }
    }
    if ($selectedRecord === null) {
        // Create dummy record
         $selectedRecord = [
            'name' => $file,
            'path' => $dataDir . DIRECTORY_SEPARATOR . $file,
        ];
    }
}

$start = ($page - 1) * $perPage + 1;
$end = min($total, $page * $perPage);

// Canonical class list (always show all 7 categories in filter UI)
$canonicalPrimaryClasses = [
    'A_法定必須_維持前提',
    'B_自治体裁量だが基幹_効率化対象',
    'C_裁量的サービス_縮小統合候補',
    'D_理念宣言中心_実施見直し候補',
    'E_規制許認可中心_規制緩和候補',
    'F_手数料使用料連動_負担軽減候補',
    'G_歴史的・形式的_現状維持',
];

// Get Statistics for Filters via DB
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
} else {
    // Fallback: don't populate
}

$selectedHtml = '';
$selectedContentHtml = '';
$selectedText = '';
$selectedTitle = '';
$selectedClassification = null;

if ($selectedRecord !== null) {
    // Try to load pre-generated clean HTML first
    $cleanHtmlPath = $cleanHtmlDir . DIRECTORY_SEPARATOR . $selectedRecord['name'];
    if (is_file($cleanHtmlPath)) {
        $selectedContentHtml = read_text_auto($cleanHtmlPath);
        // Extract title from clean HTML
        if (preg_match('/<div class="law-title">([^<]+)<\/div>/', $selectedContentHtml, $m)) {
            $selectedTitle = trim($m[1]);
        }
    } else {
        // Fallback to original HTML if clean version doesn't exist
        $selectedHtml = read_text_auto($selectedRecord['path']);
        $selectedTitle = extract_title($selectedHtml, $selectedRecord['name']);
        $selectedContentHtml = extract_law_content_html($selectedHtml);
    }
    
    // Always load original for search text (if needed for search functionality)
    if (empty($selectedText)) {
        $selectedHtml = $selectedHtml ?: read_text_auto($selectedRecord['path']);
        $selectedText = normalize_text($selectedHtml);
    }
    
    $selectedClassification = load_classification_for_record($selectedRecord, $dataDir, $classificationDir);
}

foreach ($pagedRecords as $record) {
    resolve_record_title($record, $titleCache);
}

function query_with(array $patch): string
{
    $params = $_GET;
    foreach ($patch as $k => $v) {
        if ($v === null) {
            unset($params[$k]);
        } else {
            $params[$k] = (string)$v;
        }
    }
    return '?' . http_build_query($params);
}
?><!doctype html>
<html lang="ja">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>川崎市条例評価</title>
    <style>
        * { box-sizing: border-box; }
        body {
            margin: 0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans JP', sans-serif;
            background: #f5f7fb;
            color: #1f2937;
        }
        .header {
            position: sticky;
            top: 0;
            z-index: 20;
            background: linear-gradient(90deg, #ffffff 0%, #f9fbff 100%);
            border-bottom: 1px solid #e5e7eb;
            padding: 12px 16px;
            display: flex;
            gap: 12px;
            align-items: center;
            justify-content: space-between;
        }
        .header h1 { margin: 0; font-size: 20px; }
        .meta { color: #6b7280; font-size: 13px; }
        .layout {
            display: grid;
            grid-template-columns: 360px 1fr;
            height: calc(100vh - 58px); /* Fixed height */
            overflow: hidden; /* Prevent body scroll */
        }
        .sidebar {
            border-right: 1px solid #e5e7eb;
            background: #fff;
            overflow-y: auto; /* Scrollable sidebar */
            display: flex;
            flex-direction: column;
        }
        .search {
            padding: 12px;
            border-bottom: 1px solid #eef2f7;
            position: sticky;
            top: 0;
            background: #fff;
            z-index: 5;
            flex-shrink: 0;
        }
        .list { 
            margin: 0; 
            padding: 0; 
            list-style: none;
            flex-grow: 1; /* Take remaining space */
            /* overflow-y: auto;  Already handled by sidebar overflow? No, sidebar handles it. */
        }
        .pager {
            display: flex;
            gap: 6px;
            padding: 10px 12px;
            border-top: 1px solid #eef2f7;
            position: sticky;
            bottom: 0;
            background: #fff;
            flex-shrink: 0;
            z-index: 5;
        }

        /* ... existing ... */

        .content {
            overflow: auto;
            padding: 16px;
        }
        .search input[type="text"] {
            width: 100%;
            padding: 9px 10px;
            border: 1px solid #d1d5db;
            border-radius: 8px;
            font-size: 14px;
        }
        .search select {
            border: 1px solid #d1d5db;
            border-radius: 8px;
            background: #fff;
        }
        .search button {
            margin-top: 8px;
            width: 100%;
            padding: 8px 10px;
            border: 1px solid #275ea3;
            background: #275ea3;
            color: #fff;
            border-radius: 8px;
            cursor: pointer;
        }
        .filter-block {
            margin-top: 8px;
            border: 1px solid #d1d5db;
            border-radius: 8px;
            background: #fff;
            overflow: hidden;
        }
        .filter-title {
            font-size: 12px;
            font-weight: 700;
            color: #475569;
            padding: 8px 10px;
            background: #f8fafc;
            border-bottom: 1px solid #e2e8f0;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
        }
        .filter-count {
            font-size: 11px;
            color: #64748b;
            font-weight: 500;
        }
        .checkbox-grid {
            max-height: 180px;
            overflow-y: auto;
            padding: 6px 8px 10px;
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 6px 10px;
        }
        .checkbox-item {
            display: flex;
            align-items: flex-start;
            gap: 8px;
            font-size: 12px;
            line-height: 1.35;
            cursor: pointer;
            color: #1f2937;
            min-width: 0;
        }
        .checkbox-item input[type="checkbox"] {
            margin-top: 2px;
            width: 16px;
            height: 16px;
            flex: 0 0 16px;
        }
        .checkbox-item span {
            display: block;
            overflow-wrap: anywhere;
        }
        .list { margin: 0; padding: 0; list-style: none; }
        .list a {
            display: block;
            padding: 12px 12px;
            border-bottom: 1px solid #f1f5f9;
            text-decoration: none;
            color: inherit;
        }
        .list a:hover { background: #f8fafc; }
        .list a.active { background: #e8f0fe; border-left: 3px solid #275ea3; }
        .guide h3 {
            margin: 16px 0 8px;
            font-size: 17px;
            color: #0f172a;
        }
        .guide p,
        .guide li {
            font-size: 14px;
            line-height: 1.8;
            color: #1f2937;
        }
        .guide ul {
            margin: 0;
            padding-left: 18px;
        }
        .guide .hint {
            margin-top: 8px;
            padding: 10px;
            border: 1px solid #dbeafe;
            border-radius: 8px;
            background: #eff6ff;
            color: #1e3a8a;
            font-size: 13px;
        }
        .law-title {
            font-size: 14px;
            color: #0f172a;
            font-weight: 600;
            line-height: 1.35;
            display: -webkit-box;
            line-clamp: 2;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }
        .file-name {
            margin-top: 6px;
            font-size: 12px;
            color: #64748b;
            word-break: break-all;
        }
        .file-meta { margin-top: 4px; font-size: 12px; color: #94a3b8; }
        .reading-kana {
            margin-top: 4px;
            font-size: 12px;
            color: #475569;
            letter-spacing: 0.02em;
        }

        .pager {
            display: flex;
            gap: 6px;
            padding: 10px 12px;
            border-top: 1px solid #eef2f7;
            position: sticky;
            bottom: 0;
            background: #fff;
        }
        .pager a, .pager span {
            padding: 5px 8px;
            border: 1px solid #d1d5db;
            border-radius: 6px;
            font-size: 12px;
            text-decoration: none;
            color: #334155;
            background: #fff;
        }

        .content {
            overflow: auto;
            padding: 16px;
        }
        .card {
            background: #fff;
            border: 1px solid #e5e7eb;
            border-radius: 10px;
            padding: 16px;
            margin-bottom: 12px;
            box-shadow: 0 6px 16px rgba(15, 23, 42, 0.04);
        }
        .title { margin: 0 0 8px 0; font-size: 22px; }
        .badge {
            display: inline-block;
            background: #eef2ff;
            color: #3730a3;
            border: 1px solid #c7d2fe;
            border-radius: 999px;
            font-size: 12px;
            padding: 3px 8px;
            margin-right: 6px;
            margin-bottom: 4px;
        }
        .kv {
            display: grid;
            grid-template-columns: 170px 1fr;
            gap: 6px 10px;
            font-size: 14px;
        }
        .kv dt { color: #475569; }
        .kv dd { margin: 0; color: #0f172a; }
        .law-content {
            font-size: 15px;
            line-height: 1.9;
            color: #111827;
        }
        .law-content h1,
        .law-content h2,
        .law-content h3,
        .law-content h4 {
            margin: 0.8em 0 0.4em;
            line-height: 1.4;
        }
        .law-content p,
        .law-content div,
        .law-content li {
            margin: 0.45em 0;
        }
        .law-content table {
            width: 100%;
            border-collapse: collapse;
            margin: 10px 0;
            font-size: 14px;
        }
        .law-content th,
        .law-content td {
            border: 1px solid #dbe4ef;
            padding: 6px 8px;
            vertical-align: top;
        }
        .law-content th {
            background: #f8fafc;
            font-weight: 600;
        }
        .law-content a {
            color: #1d4ed8;
            text-decoration: underline;
            word-break: break-all;
        }

        @media (max-width: 1024px) {
            .layout {
                grid-template-columns: 1fr;
                height: auto; /* Allow natural scroll */
                overflow: visible;
            }
            .sidebar {
                border-right: none;
                border-bottom: 1px solid #e5e7eb;
                max-height: 40vh;
            }
            .content {
                overflow: visible;
                height: auto;
            }
        }

        @media (max-width: 768px) {
            .header { padding: 8px 12px; }
            .header h1 { font-size: 16px; }

            .sidebar {
                display: none; /* Hidden by default on mobile, toggled via JS */
                position: fixed;
                top: 50px; /* Below header approx */
                left: 0;
                right: 0;
                z-index: 100;
                max-height: 80vh;
                box-shadow: 0 4px 12px rgba(0,0,0,0.15);
                border-bottom: none;
            }
            .sidebar.open {
                display: flex;
            }
            
            .kv {
                grid-template-columns: 1fr;
                gap: 4px;
            }
            .kv dt { margin-top: 8px; font-weight: 600; color: #334155; }
            .kv dd { padding-left: 8px; border-left: 1px solid #e2e8f0; }
            
            .law-content table {
                display: block;
                overflow-x: auto;
                -webkit-overflow-scrolling: touch;
                white-space: nowrap;
            }

            .search input[type="text"], .search button, .search select, .search label {
                font-size: 16px !important; /* Prevent iOS zoom */
            }
            .checkbox-grid {
                grid-template-columns: 1fr;
                max-height: 220px;
            }
            .guide h3 {
                font-size: 16px;
            }
            
            /* Mobile Toggle Button */
            #menu-toggle {
                display: block !important;
            }
        }
        
        #menu-toggle {
            display: none;
            background: none;
            border: 1px solid #cbd5e1;
            padding: 6px 10px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            color: #334155;
        }
    </style>
</head>
<body>
<header class="header">
    <div style="display:flex; align-items:center; gap:10px;">
        <h1>川崎市条例評価</h1>
        <button id="menu-toggle" type="button">🔍 検索・一覧</button>
    </div>
    <div class="meta">全<?php echo h((string)$total); ?>本</div>
</header>
<div class="layout">
    <aside class="sidebar">
        <form class="search" method="get">
            <input type="text" name="q" value="<?php echo h($q); ?>" placeholder="タイトル・ファイル名で検索">
            <?php if ($selectedRecord): ?>
                <input type="hidden" name="file" value="<?php echo h($selectedRecord['name']); ?>">
            <?php endif; ?>
            
            <div style="margin-top:8px;">
                <div style="display:flex; gap:4px; margin-bottom:4px;">
                    <select name="sort" style="flex:1; font-size:12px; padding:4px;" onchange="this.form.submit()">
                        <option value="date" <?php if ($sort==='date') echo 'selected'; ?>>制定日</option>
                        <option value="kana" <?php if ($sort==='kana') echo 'selected'; ?>>五十音順</option>
                        <option value="score_necessity" <?php if ($sort==='score_necessity') echo 'selected'; ?>>必要度スコア</option>
                        <option value="score_fiscal" <?php if ($sort==='score_fiscal') echo 'selected'; ?>>財政影響スコア</option>
                        <option value="score_burden" <?php if ($sort==='score_burden') echo 'selected'; ?>>規制負担スコア</option>
                        <option value="score_effectiveness" <?php if ($sort==='score_effectiveness') echo 'selected'; ?>>政策効果スコア</option>
                    </select>
                    <select name="dir" style="width:70px; font-size:12px; padding:4px;" onchange="this.form.submit()">
                        <option value="desc" <?php if ($direction==='desc') echo 'selected'; ?>>降順</option>
                        <option value="asc" <?php if ($direction==='asc') echo 'selected'; ?>>昇順</option>
                    </select>
                </div>
            </div>

            <div class="filter-block" data-filter-group>
                <div class="filter-title">
                    <span>判定結果 (複数選択可)</span>
                    <span class="filter-count" data-selected-count>0件選択</span>
                </div>
                <div class="checkbox-grid" data-filter-options>
                    <?php foreach ($allStances as $st): ?>
                        <label class="checkbox-item" data-filter-option>
                            <input type="checkbox" name="stance[]" value="<?php echo h($st); ?>" 
                                <?php if (!$hasStanceFilterParam || in_array($st, $filterStances, true)) echo 'checked'; ?>>
                            <span><?php echo h(get_stance_label((string)$st)); ?></span>
                        </label>
                    <?php endforeach; ?>
                </div>
            </div>
            
            <div class="filter-block" data-filter-group>
                <div class="filter-title">
                    <span>分類 (複数選択可)</span>
                    <span class="filter-count" data-selected-count>0件選択</span>
                </div>
                <div class="checkbox-grid" data-filter-options>
                    <?php foreach ($allClasses as $cls): ?>
                        <label class="checkbox-item" data-filter-option>
                            <input type="checkbox" name="class[]" value="<?php echo h($cls); ?>" 
                                <?php if (!$hasClassFilterParam || in_array($cls, $filterClasses, true)) echo 'checked'; ?>>
                            <span><?php echo h($cls); ?></span>
                        </label>
                    <?php endforeach; ?>
                </div>
            </div>

            <button type="submit">検索・並べ替え</button>
            
            <div style="margin-top:12px; border-top:1px solid #eef2f7; padding-top:8px;">
                <div style="font-size:11px; color:#64748b; margin-bottom:4px;">クイックフィルタ (最悪順)</div>
                <div style="display:flex; flex-wrap:wrap; gap:4px;">
                    <a href="<?php echo h(query_with(['sort' => 'score_necessity', 'dir' => 'asc', 'page' => null])); ?>" style="font-size:11px; padding:3px 8px; border:1px solid #cbd5e1; border-radius:12px; text-decoration:none; color:#334155; background:#fff;">必要度ワースト</a>
                    <a href="<?php echo h(query_with(['sort' => 'score_effectiveness', 'dir' => 'asc', 'page' => null])); ?>" style="font-size:11px; padding:3px 8px; border:1px solid #cbd5e1; border-radius:12px; text-decoration:none; color:#334155; background:#fff;">効果ワースト</a>
                    <a href="<?php echo h(query_with(['sort' => 'score_burden', 'dir' => 'desc', 'page' => null])); ?>" style="font-size:11px; padding:3px 8px; border:1px solid #cbd5e1; border-radius:12px; text-decoration:none; color:#334155; background:#fff;">規制負担ワースト</a>
                </div>
            </div>

            <?php if ($q !== '' || !empty($filterClasses) || !empty($filterStances)): ?>
                <div style="margin-top:8px; font-size:12px; color:#64748b;">
                    検索結果: <?php echo h((string)$total); ?>件
                    <br>
                    <a href="/reiki/" style="color:#275ea3; text-decoration:none;">[×] 条件をクリア</a>
                </div>
            <?php endif; ?>
        </form>

        <ul class="list">
            <?php foreach ($pagedRecords as $record): ?>
                <?php
                $active = $selectedRecord && $selectedRecord['name'] === $record['name'];
                $class = $active ? 'active' : '';
                $link = query_with(['file' => $record['name'], 'page' => null]);
                ?>
                <li>
                    <a class="<?php echo h($class); ?>" href="<?php echo h($link); ?>">
                        <div class="law-title"><?php echo h($record['title'] !== '' ? $record['title'] : ($titleCache[$record['name']] ?? $record['name'])); ?></div>
                        
                        <div style="display:flex; flex-wrap:wrap; gap:4px; margin-top:6px; align-items:center;">
                            <?php if (!empty($record['primary_class'])): ?>
                                <?php $primaryClassLabel = preg_replace('/^([A-G])_/', '$1 ', (string)($record['primary_class'] ?? '')); ?>
                                <span class="badge" style="font-size:10px; margin:0; padding:2px 6px; border:1px solid #cbd5e1; background:#f1f5f9; color:#475569;">
                                    <?php echo h((string)$primaryClassLabel); ?>
                                </span>
                            <?php endif; ?>
                            
                            <?php 
                            $keys = ['necessity_score', 'fiscal_impact_score', 'regulatory_burden_score', 'policy_effectiveness_score'];
                            $showAllScores = str_starts_with($sort, 'score_');
                            
                            foreach ($keys as $k):
                                if (!isset($record[$k])) continue;
                                $val = $record[$k];
                                // Hide irrelevant scores unless they are significant or sorting key
                                // Significant: necessity <= 50, others >= 2.0? Or just non-zero.
                                // Let's keep logic simple: show if non-zero OR it's the sort key OR it's necessity.
                                
                                if ($val == 0 && $k !== 'necessity_score' && !$showAllScores && $sort !== 'score_'.str_replace('_score','',$k) /* rough check */) {
                                    // More precise sort key check
                                    $isSortKey = ($sort === 'score_necessity' && $k === 'necessity_score') ||
                                                 ($sort === 'score_fiscal' && $k === 'fiscal_impact_score') ||
                                                 ($sort === 'score_burden' && $k === 'regulatory_burden_score') ||
                                                 ($sort === 'score_effectiveness' && $k === 'policy_effectiveness_score');
                                    if (!$isSortKey) continue;
                                }

                                list($style, $suffix) = get_score_style_and_label($k, $val);
                                
                                // Override background style for list brevity? No, user wants consistent colors.
                                // But list items are small. 
                                // The background style in get_score_style_and_label (e.g. Red bg) might be too much for list?
                                // User said "use consistent terms and color coding". So let's use it.
                                
                                $labelMap = ['necessity_score'=>'必要', 'fiscal_impact_score'=>'財政', 'regulatory_burden_score'=>'規制', 'policy_effectiveness_score'=>'効果'];
                                $label = $labelMap[$k] ?? $k;
                            ?>
                                <span style="font-size:11px; margin-right:4px; <?php echo $style; ?>">
                                    <?php echo h($label); ?>:<?php echo h((string)$val); ?> <span style="font-size:10px; opacity:0.8;"><?php echo h($suffix); ?></span>
                                </span>
                            <?php endforeach; ?>
                        </div>

                        <?php if (!empty($record['enactment_date'])): ?>
                            <div class="file-meta" style="margin-top:4px;">
                                制定日: <?php echo h($record['enactment_date']); ?>
                                
                                <?php 
                                    // Calculate elapsed years
                                    $enactmentYear = (int)substr($record['enactment_date'], 0, 4);
                                    if ($enactmentYear > 1000) {
                                        $currentYear = (int)date('Y');
                                        $elapsed = $currentYear - $enactmentYear;
                                        if ($elapsed > 0) {
                                            $style = $elapsed >= 50 ? 'color:#dc2626; font-weight:bold;' : '';
                                            echo "<span style='margin-left:6px; font-size:11px; {$style}'>({$elapsed}年前)</span>";
                                        }
                                    }
                                ?>
                            </div>
                        <?php endif; ?>
                    </a>
                </li>
            <?php endforeach; ?>
            <?php if (empty($pagedRecords)): ?>
                <li><div style="padding:12px; color:#64748b;">該当ファイルがありません。</div></li>
            <?php endif; ?>
        </ul>
        
        <?php /* Pager should be sticky at bottom */ ?>
        <div class="pager">
            <?php if ($page > 1): ?>
                <a href="<?php echo h(query_with(['page' => $page - 1])); ?>">前へ</a>
            <?php endif; ?>
            <span><?php echo h((string)$page); ?> / <?php echo h((string)$totalPages); ?></span>
            <?php if ($page < $totalPages): ?>
                <a href="<?php echo h(query_with(['page' => $page + 1])); ?>">次へ</a>
            <?php endif; ?>
        </div>
    </aside>

    <main class="content">
        <?php if ($selectedRecord === null): ?>
            <section class="card guide">
                <h2 class="title">このページの見方（はじめての方向け）</h2>
                <div style="background:#fffbeb; border:1px solid #f59e0b; border-radius:6px; padding:12px 16px; margin-bottom:16px; font-size:13.5px; line-height:1.7; color:#78350f;">
                    <strong>⚠ ご注意</strong>　ここに表示される評価結果は、AIが例規の<strong>条文テキストのみ</strong>を機械的に読み取り、あらかじめ定義した評価軸に沿って自動判定したものです。
                    制定の背景・運用実態・市民の評価・政治的文脈などは一切考慮しておらず、「冷徹な文面評価」にすぎません。
                    したがって、この結果はあくまで<strong>議論のたたき台や検索の補助道具</strong>として利用されることを想定しています。
                    スコアや分類だけを根拠に条例の要否を判断することは意図していません。
                    また、AIには事実と異なる内容を生成する「ハルシネーション（幻覚）」が起こり得るため、評価理由や根拠の記述が実際の条文と合っているかは必ずご自身でご確認ください。
                </div>
                <p>左側で条件を選んで条例を探し、一覧から1件クリックすると右側に本文と評価結果が出ます。まずは、画面に出てくる用語を短く説明します。</p>

                <h3>1) 分類（A〜G）の意味</h3>
                <ul>
                    <li><strong>A 法定必須_維持前提</strong>：法律でほぼ必須。自治体が止めにくい分野。</li>
                    <li><strong>B 自治体裁量だが基幹_効率化対象</strong>：重要だが、運営方法は見直し可能。</li>
                    <li><strong>C 裁量的サービス_縮小統合候補</strong>：任意性が高く、統合や縮小の余地がある分野。</li>
                    <li><strong>D 理念宣言中心_実施見直し候補</strong>：理念が中心で、実施内容や効果が弱い可能性。</li>
                    <li><strong>E 規制許認可中心_規制緩和候補</strong>：許認可・届出など規制が中心で、緩和検討の対象。</li>
                    <li><strong>F 手数料使用料連動_負担軽減候補</strong>：手数料や使用料の設計を見直せる可能性。</li>
                    <li><strong>G 歴史的・形式的_現状維持</strong>：歴史的経緯が大きく、当面は現状維持寄り。</li>
                </ul>

                <h3>2) スコアの見方</h3>
                <ul>
                    <li><strong>必要度（1〜100）</strong>：高いほど「今も必要」。低いほど見直し候補。</li>
                    <li><strong>財政負担（1.0〜5.0）</strong>：高いほどコストが重い。</li>
                    <li><strong>規制負担（1.0〜5.0）</strong>：高いほど市民・事業者への負担が重い。</li>
                    <li><strong>政策効果（1.0〜5.0）</strong>：高いほど実効性がある。</li>
                </ul>
                <p>色の見方は「必要度・政策効果は高いほど緑」「財政負担・規制負担は高いほど赤」です。</p>

                <h3>3) 判定結果（2つの観点と総合判定）の意味</h3>
                <ul>
                    <li><strong>観点A</strong>：自由・中立性・規制抑制の観点</li>
                    <li><strong>観点B</strong>：効率・実利・費用対効果の観点</li>
                    <li><strong>総合判定</strong>：観点A/Bを合わせた最終評価</li>
                </ul>
                <p>判定は <strong>適合 / 概ね適合 / 判断保留 / 要見直し</strong> の4段階で表示します。どちらか一方の観点で課題が大きい場合は、総合判定も厳しくなります。</p>

                <h3>4) 判定理由と注意点の見方</h3>
                <ul>
                    <li><strong>判定理由</strong>：なぜその評価になったかの要約</li>
                    <li><strong>根拠抜粋</strong>：本文のどの記述を根拠にしたか</li>
                    <li><strong>確度</strong>：AIがどれくらい自信を持っているかの目安</li>
                    <li><strong>注意フラグ</strong>：根拠不足や人手確認が必要なサイン</li>
                </ul>
                <p>確度が高くても、根拠が弱い場合は必ず原文で再確認してください。</p>

                <h3>5) 並び替え・絞り込みの意味</h3>
                <ul>
                    <li><strong>分類チェック</strong>：A〜Gで対象を絞る</li>
                    <li><strong>判定結果チェック</strong>：総合判定で絞る</li>
                    <li><strong>制定日ソート</strong>：新旧で優先順位を見る</li>
                    <li><strong>スコアソート</strong>：問題が大きい候補を先に確認する</li>
                </ul>

                <h3>6) このページの限界</h3>
                <ul>
                    <li>AI評価は補助であり、法的な確定判断ではありません。</li>
                    <li>本文の改正履歴や運用実態までは、この画面だけでは完全に分かりません。</li>
                    <li>最終判断は、原文・担当部署資料・議会資料を合わせて行ってください。</li>
                </ul>

                <div class="hint">注意：この評価は「見直し候補を探すための一次スクリーニング」です。最終判断は必ず原文（条例本文）と実務資料で確認してください。</div>
            </section>
        <?php else: ?>
            <section class="card">
                <h2 class="title"><?php echo h($selectedTitle); ?></h2>

                <?php if (is_array($selectedClassification)): ?>
                    <?php if (!empty($selectedClassification['readingKana'])): ?>
                        <div class="meta" style="margin-bottom:10px; font-size:14px; color:#334155;">
                            読み: <?php echo h((string)$selectedClassification['readingKana']); ?>
                            <?php if (isset($selectedClassification['readingConfidence'])): ?>
                                （確度: <?php echo h((string)$selectedClassification['readingConfidence']); ?>）
                            <?php endif; ?>
                        </div>
                    <?php endif; ?>
                    <?php if (!empty($selectedClassification['responsibleDepartment'])): ?>
                        <div class="meta" style="margin-bottom:10px; font-size:14px; color:#334155;">
                            所管部署（推定）: <?php echo h((string)$selectedClassification['responsibleDepartment']); ?>
                            <?php if (isset($selectedClassification['departmentConfidence'])): ?>
                                （確度: <?php echo h((string)$selectedClassification['departmentConfidence']); ?>）
                            <?php endif; ?>
                        </div>
                    <?php endif; ?>
                    <?php if (!empty($selectedClassification['analyzedAt'])): ?>
                        <div class="meta" style="margin-bottom:10px; font-size:13px; color:#6b7280;">
                            AI評価日時: <?php try { echo h((new DateTime($selectedClassification['analyzedAt']))->setTimezone(new DateTimeZone('Asia/Tokyo'))->format('Y-m-d H:i:s')); } catch(Exception $e) { echo h($selectedClassification['analyzedAt']); } ?>
                            <?php if (!empty($selectedClassification['modelName'])): ?>
                                (Model: <?php echo h((string)$selectedClassification['modelName']); ?>)
                            <?php endif; ?>
                        </div>
                    <?php endif; ?>
                    <div style="margin-bottom:10px;">
                        <?php if (!empty($selectedClassification['primaryClass'])): ?>
                            <span class="badge"><?php echo h((string)$selectedClassification['primaryClass']); ?></span>
                        <?php endif; ?>
                        <?php
                        $secondaryTags = $selectedClassification['secondaryTags'] ?? [];
                        if (is_array($secondaryTags)) {
                            foreach ($secondaryTags as $tag) {
                                echo '<span class="badge">' . h((string)$tag) . '</span>';
                            }
                        }
                        ?>
                    </div>
                    <dl class="kv">
                        <?php 
                        $scoreMap = [
                            'necessityScore' => ['key'=>'necessity_score', 'label'=>'必要度 (1-100)'],
                            'fiscalImpactScore' => ['key'=>'fiscal_impact_score', 'label'=>'財政負担 (1.0-5.0)'],
                            'regulatoryBurdenScore' => ['key'=>'regulatory_burden_score', 'label'=>'規制負担 (1.0-5.0)'],
                            'policyEffectivenessScore' => ['key'=>'policy_effectiveness_score', 'label'=>'政策効果 (1.0-5.0)']
                        ];
                        
                        foreach ($scoreMap as $jsonKey => $info) {
                            $val = $selectedClassification[$jsonKey] ?? null;
                            echo '<dt>' . h($info['label']) . '</dt>';
                            echo '<dd>';
                            if ($val !== null) {
                                // Detail View font size adjustment? 
                                // get_score_html returns a span.
                                // <dd> usually has some font size.
                                // The span will carry the color/weight.
                                echo get_score_html($info['key'], $val);
                            } else {
                                echo '-';
                            }
                            echo '</dd>';
                        }
                        ?>

                        <dt>判定理由</dt>
                        <dd style="line-height:1.6;"><?php echo nl2br(h((string)($selectedClassification['reason'] ?? '-'))); ?></dd>
                    </dl>
                <?php else: ?>
                    <div class="meta">分類結果ファイル（kawasaki_json/*.json）が未作成、または該当データなし。</div>
                <?php endif; ?>
            </section>

            <section class="card">
                <div class="law-content">
                    <?php if ($selectedContentHtml !== ''): ?>
                        <?php echo $selectedContentHtml; ?>
                    <?php else: ?>
                        <pre><?php echo h($selectedText); ?></pre>
                    <?php endif; ?>
                </div>
            </section>
        <?php endif; ?>
    </main>
</div>
</body>
<script>
document.addEventListener('DOMContentLoaded', function() {
    const toggle = document.getElementById('menu-toggle');
    const sidebar = document.querySelector('.sidebar');
    const sidebarScrollKey = 'reiki:sidebar-scroll-top';

    if (sidebar) {
        const savedScrollTop = window.sessionStorage.getItem(sidebarScrollKey);
        if (savedScrollTop !== null) {
            const parsed = Number.parseInt(savedScrollTop, 10);
            if (!Number.isNaN(parsed)) {
                requestAnimationFrame(() => {
                    sidebar.scrollTop = parsed;
                });
            }
        }

        sidebar.addEventListener('scroll', () => {
            window.sessionStorage.setItem(sidebarScrollKey, String(sidebar.scrollTop));
        }, { passive: true });
    }

    if (toggle && sidebar) {
        toggle.addEventListener('click', function() {
            sidebar.classList.toggle('open');
        });
    }
    
    // Close sidebar when clicking a link in it (on mobile)
    if (sidebar) {
        const links = sidebar.querySelectorAll('a');
        links.forEach(link => {
            link.addEventListener('click', () => {
                window.sessionStorage.setItem(sidebarScrollKey, String(sidebar.scrollTop));
                if (window.innerWidth <= 768) {
                    sidebar.classList.remove('open');
                }
            });
        });

        const forms = sidebar.querySelectorAll('form');
        forms.forEach(form => {
            form.addEventListener('submit', () => {
                window.sessionStorage.setItem(sidebarScrollKey, String(sidebar.scrollTop));
            });
        });
    }

    const groups = document.querySelectorAll('[data-filter-group]');
    groups.forEach((group) => {
        const checkboxes = group.querySelectorAll('input[type="checkbox"]');
        const counter = group.querySelector('[data-selected-count]');

        const updateCount = () => {
            const count = Array.from(checkboxes).filter((checkbox) => checkbox.checked).length;
            if (counter) {
                counter.textContent = `${count}件選択`;
            }
        };

        updateCount();
        checkboxes.forEach((checkbox) => checkbox.addEventListener('change', updateCount));
    });
});
</script>
</html>
