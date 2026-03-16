<?php
declare(strict_types=1);

require_once __DIR__ . '/includes/functions.php';
require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'municipalities.php';

// ─────────────────────────────────────
// Path Configuration
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
$dataDir = (string)($reikiFeature['data_dir'] ?? '');
$cleanHtmlDir = (string)($reikiFeature['clean_html_dir'] ?? '');
$classificationDir = (string)($reikiFeature['classification_dir'] ?? '');
$dbPath = (string)($reikiFeature['db_path'] ?? '');
$reikiImageUrl = (string)($reikiFeature['image_url'] ?? '/data/reiki/kawasaki_images');
$pageTitle = (string)($reikiFeature['title'] ?? ($municipality['name'] . '例規集 AI評価ビューア'));
$clearUrl = '/reiki/?slug=' . rawurlencode($slug);
$featureNotice = $featureAvailable ? '' : ($municipality['name'] . 'の例規集は準備中です。');

// ─────────────────────────────────────
// Query Parameters
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
// Database Connection
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
            'path' => $dataDir . DIRECTORY_SEPARATOR . $name,
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
    // Fallback to file scanning (Legacy)
    if ($featureAvailable && is_dir($dataDir)) {
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
// Selected Record
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
            'path' => $dataDir . DIRECTORY_SEPARATOR . $file,
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
// Load Selected Record Content
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
    } else {
        $selectedHtml = read_text_auto($selectedRecord['path']);
        $selectedTitle = extract_title($selectedHtml, $selectedRecord['name']);
        $selectedContentHtml = extract_law_content_html($selectedHtml, $reikiImageUrl);
    }
    
    if (empty($selectedText)) {
        $selectedHtml = $selectedHtml ?: read_text_auto($selectedRecord['path']);
        $selectedText = normalize_text($selectedHtml);
    }
    
    $selectedClassification = load_classification_for_record($selectedRecord, $dataDir, $classificationDir);
}

foreach ($pagedRecords as $record) {
    resolve_record_title($record, $titleCache);
}

$isLanding = ($selectedRecord === null && $q === '' && $page === 1 && !$hasClassFilterParam && !$hasStanceFilterParam && !$hasDocTypeFilterParam);
?><!doctype html>
<html lang="ja">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title><?php echo h($pageTitle); ?></title>
    <?php $cssVer = @filemtime(__DIR__ . '/assets/css/reiki.css') ?: 1; ?>
    <?php $jsVer  = @filemtime(__DIR__ . '/assets/js/reiki.js')  ?: 1; ?>
    <link rel="stylesheet" href="/reiki/assets/css/reiki.css?v=<?php echo $cssVer; ?>">
</head>
<body data-reiki-slug="<?php echo h($slug); ?>">
<header class="header">
    <div style="display:flex; align-items:center; gap:10px;">
        <h1><a href="<?php echo h($clearUrl); ?>" style="color:inherit; text-decoration:none;"><?php echo h($pageTitle); ?></a></h1>
        <button id="menu-toggle" type="button">🔍 検索・一覧</button>
    </div>
    <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap; justify-content:flex-end;">
        <select aria-label="自治体切り替え" onchange="if (this.value) { window.location.href = this.value; }" style="padding:8px 10px; border:1px solid #cbd5e1; border-radius:999px; font-size:13px;">
            <?php foreach ($switcherItems as $item): ?>
                <?php $switchMunicipality = municipality_entry((string)$item['slug']); ?>
                <?php $switchUrl = (string)($switchMunicipality['reiki']['url'] ?? ''); ?>
                <option value="<?php echo h($switchUrl); ?>" <?php echo $item['slug'] === $slug ? 'selected' : ''; ?>>
                    <?php echo h($item['name'] . (!empty($item['enabled']) ? '' : ' (準備中)')); ?>
                </option>
            <?php endforeach; ?>
        </select>
        <div class="meta"><?php echo $featureAvailable ? '全' . h((string)$total) . '本' : '準備中'; ?></div>
    </div>
</header>
<?php
$layoutClasses = 'layout';
if ($selectedRecord !== null) $layoutClasses .= ' has-detail';
elseif ($isLanding) $layoutClasses .= ' is-landing';
?>
<div class="<?php echo $layoutClasses; ?>">
    <nav class="mobile-back">
        <a href="<?php echo h(query_with(['file' => null])); ?>">← 一覧に戻る</a>
    </nav>
    <aside class="sidebar">
        <form class="search" method="get">
            <input type="hidden" name="slug" value="<?php echo h($slug); ?>">
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

            <div class="filter-block" data-filter-group>
                <div class="filter-title">
                    <span>種別 (複数選択可)</span>
                    <span class="filter-count" data-selected-count>0件選択</span>
                </div>
                <div class="checkbox-grid" data-filter-options>
                    <?php foreach ($documentTypeOptions as $docType): ?>
                        <label class="checkbox-item" data-filter-option>
                            <input type="checkbox" name="doctype[]" value="<?php echo h($docType); ?>"
                                <?php if (!$hasDocTypeFilterParam || in_array($docType, $filterDocTypes, true)) echo 'checked'; ?>>
                            <span><?php echo h($docType); ?></span>
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

            <?php if ($q !== '' || !empty($filterClasses) || !empty($filterStances) || !empty($filterDocTypes)): ?>
                <div style="margin-top:8px; font-size:12px; color:#64748b;">
                    検索結果: <?php echo h((string)$total); ?>件
                    <br>
                    <a href="<?php echo h($clearUrl); ?>" style="color:#275ea3; text-decoration:none;">[×] 条件をクリア</a>
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

                                if ($val == 0 && $k !== 'necessity_score' && !$showAllScores) {
                                    $isSortKey = ($sort === 'score_necessity' && $k === 'necessity_score') ||
                                                 ($sort === 'score_fiscal' && $k === 'fiscal_impact_score') ||
                                                 ($sort === 'score_burden' && $k === 'regulatory_burden_score') ||
                                                 ($sort === 'score_effectiveness' && $k === 'policy_effectiveness_score');
                                    if (!$isSortKey) continue;
                                }

                                list($style, $suffix) = get_score_style_and_label($k, $val);
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
        <?php if (!$featureAvailable): ?>
            <div class="empty-content">
                <div style="font-size:40px; margin-bottom:12px;">🏗</div>
                <div style="font-size:16px; font-weight:600; color:#334155; margin-bottom:8px;">
                    <?php echo h($featureNotice); ?>
                </div>
                <div style="font-size:14px; color:#64748b;">
                    自治体切り替えUIには対応済みです。データを配置すると、この画面からそのまま検索・閲覧できます。
                </div>
            </div>
        <?php elseif ($selectedRecord === null): ?>
            <?php if ($isLanding): ?>
                <?php include __DIR__ . '/includes/guide.php'; ?>
            <?php else: ?>
                <div class="empty-content">
                    <div style="font-size:40px; margin-bottom:12px;">📋</div>
                    <div style="font-size:16px; font-weight:600; color:#334155; margin-bottom:8px;">
                        <?php echo h((string)$total); ?>件の例規が見つかりました
                    </div>
                    <div style="font-size:14px; color:#64748b;">
                        左の一覧から条例を選択すると、ここに評価結果と本文が表示されます。
                    </div>
                </div>
            <?php endif; ?>
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
                    <div class="meta">分類結果ファイル（<?php echo h((string)($reikiFeature['classification_dir_rel'] ?? 'reiki/*_json')); ?>）が未作成、または該当データなし。</div>
                <?php endif; ?>
            </section>

            <!-- Feedback Section -->
            <?php $filenameStem = pathinfo($selectedRecord['name'], PATHINFO_FILENAME); ?>
            <section class="card" id="feedback-section" data-filename="<?php echo h($filenameStem); ?>" data-slug="<?php echo h($slug); ?>">
                <div class="feedback-row">
                    <span style="font-size:14px; font-weight:600; color:#334155;">このAI評価はどうですか？</span>
                    <div class="feedback-buttons">
                        <button type="button" id="btn-good" onclick="submitVote('good')">
                            👍 <span id="count-good">…</span>
                        </button>
                        <button type="button" id="btn-bad" onclick="submitVote('bad')">
                            👎 <span id="count-bad">…</span>
                        </button>
                    </div>
                    <span id="vote-status"></span>
                    <span class="view-count-label">👁 <span id="view-count">…</span> 回閲覧</span>
                </div>
                <div style="margin-top:10px;">
                    <input type="text" id="comment-input" maxlength="200" placeholder="短いコメント（任意・200文字以内）">
                </div>
                <div id="comments-list" style="margin-top:12px; display:none;">
                    <div style="font-size:12px; font-weight:600; color:#475569; margin-bottom:6px;">最近のコメント</div>
                    <ul id="comments-ul" style="margin:0; padding:0; list-style:none; font-size:13px; max-height:200px; overflow-y:auto;"></ul>
                </div>
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
<script src="/reiki/assets/js/reiki.js?v=<?php echo $jsVer; ?>"></script>
</body>
</html>
