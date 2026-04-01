<?php
declare(strict_types=1);

require_once 'gijiroku/index_helpers.php';

// リクエスト解釈、SQLite 検索、詳細表示用データの組み立てを担う。
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
$gijirokuFeature = municipality_feature($slug, 'gijiroku') ?? [];
$featureAvailable = (bool)($gijirokuFeature['enabled'] ?? false);
$switcherItems = municipality_switcher_items('gijiroku');
$assemblyName = (string)($gijirokuFeature['assembly_name'] ?? ($municipality['name'] . '議会'));
$pageTitle = (string)($gijirokuFeature['title'] ?? ($assemblyName . ' 会議録 全文検索'));
$clearUrl = '/gijiroku/?slug=' . rawurlencode($slug);
$featureNotice = $featureAvailable ? '' : ($assemblyName . 'の会議録は準備中です。');
$dbPath = (string)($gijirokuFeature['db_path'] ?? '');
$q = trim((string)($_GET['q'] ?? ''));
$year = trim((string)($_GET['year'] ?? ''));
$hasRequestedDoc = isset($_GET['doc']) && (int)$_GET['doc'] > 0;
$selectedId = max(0, (int)($_GET['doc'] ?? 0));
$workspaceTabParam = trim((string)($_GET['tab'] ?? ''));
$viewerTabParam = trim((string)($_GET['viewer_tab'] ?? ''));
$page = max(1, (int)($_GET['page'] ?? 1));
$perPage = 20;
$offset = ($page - 1) * $perPage;
$queryTerms = extract_query_terms($q);

// 画面描画側では SQL を組み立てないように、検索条件と DB 接続をここで閉じ込める。
$pdo = null;
$error = '';
if ($featureAvailable && $dbPath !== '' && is_file($dbPath)) {
    try {
        $pdo = new PDO('sqlite:' . $dbPath);
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $pdo->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);
    } catch (Exception $e) {
        $error = 'SQLiteの読み込みに失敗しました。';
    }
} elseif ($featureAvailable) {
    $error = '検索DBが見つかりません。tools/gijiroku/build_minutes_index.py --slug ' . $slug . ' を実行してください。';
}

$rows = [];
$detail = null;
$detailDocument = null;
$detailMatches = [];
$focusAnchor = '';
$stats = ['documents' => 0, 'years' => 0, 'first_date' => null, 'last_date' => null];
$yearOptions = [];
$total = 0;

if ($pdo) {
    // stats / 年度候補 / 一覧 / 詳細の順に必要なデータをまとめて取得する。
    $statsStmt = $pdo->query("SELECT COUNT(*) AS documents, COUNT(DISTINCT year_label) AS years, MIN(held_on) AS first_date, MAX(held_on) AS last_date FROM minutes WHERE doc_type = 'minutes'");
    $stats = $statsStmt->fetch() ?: $stats;

    $yearOptionsStmt = $pdo->query("SELECT year_label, COUNT(*) AS count, MAX(held_on) AS last_date FROM minutes WHERE doc_type = 'minutes' GROUP BY year_label ORDER BY last_date DESC, year_label DESC");
    $yearOptions = $yearOptionsStmt->fetchAll();

    if ($q !== '') {
        // キーワード検索時だけ FTS を使い、未入力時は通常一覧として軽く返す。
        try {
            $countSql = 'SELECT COUNT(*) FROM minutes_fts JOIN minutes m ON m.id = minutes_fts.rowid WHERE minutes_fts MATCH :q';
            if ($year !== '') {
                $countSql .= ' AND m.year_label = :year';
            }

            $countStmt = $pdo->prepare($countSql);
            $countStmt->bindValue(':q', $q, PDO::PARAM_STR);
            if ($year !== '') {
                $countStmt->bindValue(':year', $year, PDO::PARAM_STR);
            }
            $countStmt->execute();
            $total = (int)$countStmt->fetchColumn();

            $sql = 'SELECT m.id, m.title, m.meeting_name, m.year_label, m.held_on, m.rel_path, m.source_url, snippet(minutes_fts, 2, "[[[", "]]]", "…", 18) AS excerpt, bm25(minutes_fts) AS score FROM minutes_fts JOIN minutes m ON m.id = minutes_fts.rowid WHERE minutes_fts MATCH :q';
            if ($year !== '') {
                $sql .= ' AND m.year_label = :year';
            }
            $sql .= ' ORDER BY score ASC, m.held_on DESC, m.id DESC LIMIT :limit OFFSET :offset';

            $stmt = $pdo->prepare($sql);
            $stmt->bindValue(':q', $q, PDO::PARAM_STR);
            if ($year !== '') {
                $stmt->bindValue(':year', $year, PDO::PARAM_STR);
            }
            $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
            $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
            $stmt->execute();
            $rows = $stmt->fetchAll();
        } catch (Exception $e) {
            $error = '検索式の解釈に失敗しました。キーワードを調整してください。';
        }
    } else {
        $countSql = "SELECT COUNT(*) FROM minutes WHERE doc_type = 'minutes'";
        if ($year !== '') {
            $countStmt = $pdo->prepare($countSql . ' AND year_label = :year');
            $countStmt->bindValue(':year', $year, PDO::PARAM_STR);
            $countStmt->execute();
            $total = (int)$countStmt->fetchColumn();
        } else {
            $total = (int)$pdo->query($countSql)->fetchColumn();
        }

        $sql = 'SELECT id, title, meeting_name, year_label, held_on, rel_path, source_url, substr(content, 1, 240) AS excerpt FROM minutes WHERE doc_type = \'minutes\'';
        if ($year !== '') {
            $sql .= ' AND year_label = :year';
        }
        $sql .= ' ORDER BY held_on DESC, id DESC LIMIT :limit OFFSET :offset';

        $stmt = $pdo->prepare($sql);
        if ($year !== '') {
            $stmt->bindValue(':year', $year, PDO::PARAM_STR);
        }
        $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
        $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
        $stmt->execute();
        $rows = $stmt->fetchAll();
    }

    // 詳細指定が無いときは一覧の先頭を既定選択にして、右ペインを空にしない。
    if ($selectedId === 0 && !empty($rows)) {
        $selectedId = (int)$rows[0]['id'];
    }

    if ($selectedId > 0) {
        $detailStmt = $pdo->prepare("SELECT id, title, meeting_name, year_label, held_on, rel_path, source_url, source_fino, content FROM minutes WHERE id = :id AND doc_type = 'minutes'");
        $detailStmt->bindValue(':id', $selectedId, PDO::PARAM_INT);
        $detailStmt->execute();
        $detail = $detailStmt->fetch() ?: null;
        if ($detail) {
            $detailDocument = annotate_document_matches(parse_minutes_document((string)$detail['content']), $queryTerms);
            $detailMatches = $detailDocument['matches'] ?? [];
            $focusAnchor = $detailMatches[0]['anchor'] ?? '';
        }
    }
}

$totalPages = max(1, (int)ceil($total / $perPage));
$start = $total > 0 ? ($offset + 1) : 0;
$end = min($total, $offset + $perPage);
$headerLines = $detailDocument ? array_values(array_filter($detailDocument['preamble']['header'], static fn (string $line): bool => $line !== '' && $line !== (string)($detail['title'] ?? ''))) : [];
$matchPreview = array_slice($detailMatches, 0, 8);
$queryTermPreview = array_slice($queryTerms, 0, 6);
$activeFilters = [];
if ($q !== '') {
    $activeFilters[] = ['label' => '検索語', 'value' => $q];
}
if ($year !== '') {
    $activeFilters[] = ['label' => '年度', 'value' => $year];
}
$resultModeLabel = $q !== '' ? 'キーワード検索' : ($year !== '' ? '年度絞り込み' : '最新順ブラウズ');
$pageSummary = $total > 0 ? ($page . ' / ' . $totalPages) : '0 / 1';
$detailFacts = [];
if ($detail) {
    if (!empty($detail['held_on'])) {
        $detailFacts[] = ['label' => '開催日', 'value' => (string)$detail['held_on']];
    }
    $detailFacts[] = ['label' => '年度', 'value' => (string)$detail['year_label']];
    if (!empty($detail['meeting_name'])) {
        $detailFacts[] = ['label' => '会議名', 'value' => (string)$detail['meeting_name']];
    }
    $detailFacts[] = ['label' => 'ファイル', 'value' => (string)$detail['rel_path']];
    if (!empty($detail['source_fino'])) {
        $detailFacts[] = ['label' => 'FINO', 'value' => (string)$detail['source_fino']];
    }
}
$workspaceTab = in_array($workspaceTabParam, ['results', 'viewer'], true) ? $workspaceTabParam : ($hasRequestedDoc ? 'viewer' : 'results');
if (!$detail) {
    $workspaceTab = 'results';
}
$viewerTab = in_array($viewerTabParam, ['transcript', 'summary', 'matches'], true) ? $viewerTabParam : ($detailMatches !== [] ? 'matches' : 'transcript');
if ($viewerTab === 'matches' && $detailMatches === []) {
    $viewerTab = 'summary';
}
