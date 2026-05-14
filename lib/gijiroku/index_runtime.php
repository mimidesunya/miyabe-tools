<?php
declare(strict_types=1);

require_once __DIR__ . DIRECTORY_SEPARATOR . 'index_helpers.php';
require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'gijiroku_search.php';

// リクエスト解釈、SQLite 検索、詳細表示用データの組み立てを担う。
$requestedSlugInput = trim((string)($_GET['slug'] ?? ''));
$resolvedRequestedSlug = $requestedSlugInput !== '' ? gijiroku_search_resolve_ready_slug($requestedSlugInput) : '';
if (
    $requestedSlugInput !== ''
    && $resolvedRequestedSlug !== ''
    && normalize_slug_alias_value($requestedSlugInput) !== normalize_slug_alias_value($resolvedRequestedSlug)
) {
    $path = parse_url((string)($_SERVER['REQUEST_URI'] ?? ''), PHP_URL_PATH);
    $path = is_string($path) && $path !== '' ? $path : '/gijiroku/';
    $query = $_GET;
    $query['slug'] = $resolvedRequestedSlug;
    header('Location: ' . $path . '?' . http_build_query($query), true, 302);
    exit;
}

$slug = $resolvedRequestedSlug;
if ($slug === '') {
    $readySummaries = gijiroku_search_ready_summaries();
    $firstReady = $readySummaries[0] ?? null;
    $slug = is_array($firstReady) ? (string)($firstReady['slug'] ?? '') : '';
}
$requestSlug = $slug;
$municipality = $slug !== '' ? gijiroku_search_ready_municipality($slug) : null;
if ($municipality === null) {
    http_response_code(404);
    echo '自治体が見つかりません。';
    exit;
}
$gijirokuFeature = is_array($municipality['gijiroku'] ?? null) ? $municipality['gijiroku'] : [];
$switcherItems = [];
foreach (gijiroku_search_ready_summaries() as $switcherItem) {
    $switcherItems[] = [
        'slug' => (string)($switcherItem['slug'] ?? ''),
        'name' => (string)($switcherItem['name'] ?? ''),
        'enabled' => true,
        'url' => (string)($switcherItem['url'] ?? ''),
        'title' => (string)($switcherItem['assembly_name'] ?? ''),
    ];
}
$assemblyName = (string)($gijirokuFeature['assembly_name'] ?? ($municipality['name'] . '議会'));
$pageTitle = (string)($gijirokuFeature['title'] ?? ($assemblyName . ' 会議録 全文検索'));
$clearUrl = '/gijiroku/?slug=' . rawurlencode($requestSlug);
$dbPath = (string)($gijirokuFeature['db_path'] ?? '');
$downloadsDir = (string)($gijirokuFeature['downloads_dir'] ?? '');
$indexJsonPath = (string)($gijirokuFeature['index_json_path'] ?? '');
$featureAvailable = !empty($gijirokuFeature['enabled']) && $dbPath !== '' && is_file($dbPath);
$featureNotice = $featureAvailable ? '' : ($assemblyName . 'の会議録は準備中です。');
$q = trim((string)($_GET['q'] ?? ''));
$year = trim((string)($_GET['year'] ?? ''));
$hasRequestedDoc = isset($_GET['doc']) && (int)$_GET['doc'] > 0;
$selectedId = max(0, (int)($_GET['doc'] ?? 0));
$workspaceTabParam = trim((string)($_GET['tab'] ?? ''));
$viewerTabParam = trim((string)($_GET['viewer_tab'] ?? ''));
$page = max(1, (int)($_GET['page'] ?? 1));
$perPage = 20;
$offset = ($page - 1) * $perPage;
$preparedQuery = japanese_search_prepare_query($q);
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
    $hasSourceFiles = ($downloadsDir !== '' && is_dir($downloadsDir)) || ($indexJsonPath !== '' && is_file($indexJsonPath));
    if ($hasSourceFiles) {
        $featureNotice = '検索インデックスを自動準備中です。しばらくしてから再度お試しください。';
    } else {
        $featureNotice = $assemblyName . 'の会議録データは準備中です。';
    }
}

$rows = [];
$detail = null;
$detailDocument = null;
$detailMatches = [];
$focusAnchor = '';
$stats = ['documents' => 0, 'years' => 0, 'first_date' => null, 'last_date' => null];
$yearOptions = [];
$total = 0;

$indexSummary = $indexJsonPath !== '' ? gijiroku_index_summary_from_json($indexJsonPath) : null;
if (is_array($indexSummary)) {
    $summaryStats = $indexSummary['stats'] ?? null;
    if (is_array($summaryStats)) {
        $stats = array_merge($stats, $summaryStats);
    }
    $summaryYearOptions = $indexSummary['year_options'] ?? null;
    if (is_array($summaryYearOptions)) {
        $yearOptions = array_values(array_filter($summaryYearOptions, 'is_array'));
    }
}

if ($pdo) {
    if ((int)($stats['documents'] ?? 0) <= 0) {
        $stats['documents'] = (int)$pdo->query("SELECT COUNT(*) FROM minutes WHERE doc_type = 'minutes'")->fetchColumn();
    }
    if ((int)($stats['years'] ?? 0) <= 0 && $yearOptions !== []) {
        $stats['years'] = count($yearOptions);
    }
    if (empty($stats['first_date'])) {
        $stats['first_date'] = gijiroku_index_boundary_date($pdo, 'ASC');
    }
    if (empty($stats['last_date'])) {
        $stats['last_date'] = gijiroku_index_boundary_date($pdo, 'DESC');
    }

    if ($q !== '' && $year === '') {
        $searchResult = gijiroku_search_execute($municipality, $q, $page, $perPage, 0, 0, 'relevance');
        if (($searchResult['status'] ?? '') !== 'ok') {
            $error = (string)($searchResult['error'] ?? '検索結果を読み込めませんでした。');
        }
        $total = (int)($searchResult['total'] ?? 0);
        foreach (($searchResult['rows'] ?? []) as $searchRow) {
            if (!is_array($searchRow)) {
                continue;
            }
            $rows[] = [
                'id' => (int)($searchRow['id'] ?? 0),
                'title' => (string)($searchRow['title'] ?? ''),
                'meeting_name' => (string)($searchRow['meeting_name'] ?? ''),
                'year_label' => (string)($searchRow['year_label'] ?? ''),
                'held_on' => (string)($searchRow['held_on'] ?? ''),
                'rel_path' => (string)($searchRow['rel_path'] ?? ''),
                'source_url' => (string)($searchRow['source_url'] ?? ''),
                'excerpt' => (string)($searchRow['excerpt'] ?? ''),
            ];
        }
    } elseif ($q !== '') {
        // キーワード検索時だけ FTS を使い、未入力時は通常一覧として軽く返す。
        try {
            $exactPhrases = japanese_search_exact_phrases_from_prepared($preparedQuery);
            if ($exactPhrases !== []) {
                $batchSize = max($perPage + 1, 50);
                $rawOffset = 0;
                $exactSeen = 0;
                $hasMore = false;
                $phraseLike = japanese_search_exact_phrase_like_clause(
                    $exactPhrases,
                    ['m.title', 'm.meeting_name', 'm.content'],
                    'exact_phrase'
                );
                $phraseSql = (string)($phraseLike['sql'] ?? '');
                $phraseWhereSql = $phraseSql !== '' ? ' AND ' . $phraseSql : '';
                $phraseParams = is_array($phraseLike['params'] ?? null) ? $phraseLike['params'] : [];
                $sql = 'SELECT m.id, m.title, m.meeting_name, m.year_label, m.held_on, m.rel_path, m.source_url, m.content AS excerpt_source, minutes_fts.rank AS score FROM minutes_fts JOIN minutes m ON m.id = minutes_fts.rowid WHERE minutes_fts MATCH :q';
                $sql .= $phraseWhereSql;
                if ($year !== '') {
                    $sql .= ' AND m.year_label = :year';
                }
                $sql .= ' ORDER BY m.held_on DESC, score, m.id DESC LIMIT :limit OFFSET :offset';

                while (count($rows) <= $perPage) {
                    $stmt = $pdo->prepare($sql);
                    $stmt->bindValue(':q', (string)($preparedQuery['fts_query'] ?? $q), PDO::PARAM_STR);
                    if ($year !== '') {
                        $stmt->bindValue(':year', $year, PDO::PARAM_STR);
                    }
                    $stmt->bindValue(':limit', $batchSize, PDO::PARAM_INT);
                    $stmt->bindValue(':offset', $rawOffset, PDO::PARAM_INT);
                    foreach ($phraseParams as $param => $value) {
                        $stmt->bindValue((string)$param, (string)$value, PDO::PARAM_STR);
                    }
                    $stmt->execute();
                    $candidates = $stmt->fetchAll();
                    if ($candidates === []) {
                        break;
                    }
                    foreach ($candidates as $candidate) {
                        if (!is_array($candidate)) {
                            continue;
                        }
                        $exactHaystack = trim(
                            (string)($candidate['title'] ?? '') . ' '
                            . (string)($candidate['meeting_name'] ?? '') . ' '
                            . (string)($candidate['excerpt_source'] ?? '')
                        );
                        if (!japanese_search_text_matches_exact_phrases($exactHaystack, $exactPhrases)) {
                            continue;
                        }
                        if ($exactSeen++ < $offset) {
                            continue;
                        }
                        $rows[] = $candidate;
                        if (count($rows) > $perPage) {
                            $hasMore = true;
                            break 2;
                        }
                    }
                    $rawOffset += count($candidates);
                    if (count($candidates) < $batchSize) {
                        break;
                    }
                }
                if (count($rows) > $perPage) {
                    $rows = array_slice($rows, 0, $perPage);
                }
                $total = $offset + count($rows) + ($hasMore ? 1 : 0);
            } else {
                $countSql = $year !== ''
                    ? 'SELECT COUNT(*) FROM minutes_fts JOIN minutes m ON m.id = minutes_fts.rowid WHERE minutes_fts MATCH :q AND m.year_label = :year'
                    : 'SELECT COUNT(*) FROM minutes_fts WHERE minutes_fts MATCH :q';

                $countStmt = $pdo->prepare($countSql);
                $countStmt->bindValue(':q', (string)($preparedQuery['fts_query'] ?? $q), PDO::PARAM_STR);
                if ($year !== '') {
                    $countStmt->bindValue(':year', $year, PDO::PARAM_STR);
                }
                $countStmt->execute();
                $total = (int)$countStmt->fetchColumn();

                $sql = 'SELECT m.id, m.title, m.meeting_name, m.year_label, m.held_on, m.rel_path, m.source_url, m.content AS excerpt_source, minutes_fts.rank AS score FROM minutes_fts JOIN minutes m ON m.id = minutes_fts.rowid WHERE minutes_fts MATCH :q';
                if ($year !== '') {
                    $sql .= ' AND m.year_label = :year';
                }
                // 自治体ページの検索結果は、関連度よりまず新しさで追える並びを優先する。
                $sql .= ' ORDER BY m.held_on DESC, score, m.id DESC LIMIT :limit OFFSET :offset';

                $stmt = $pdo->prepare($sql);
                $stmt->bindValue(':q', (string)($preparedQuery['fts_query'] ?? $q), PDO::PARAM_STR);
                if ($year !== '') {
                    $stmt->bindValue(':year', $year, PDO::PARAM_STR);
                }
                $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
                $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
                $stmt->execute();
                $rows = $stmt->fetchAll();
            }
            foreach ($rows as &$row) {
                if (!is_array($row)) {
                    continue;
                }
                $row['excerpt'] = japanese_search_build_excerpt(
                    (string)($row['excerpt_source'] ?? ''),
                    $queryTerms
                );
            }
            unset($row);
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
