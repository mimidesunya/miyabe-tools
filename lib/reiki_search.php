<?php
declare(strict_types=1);

require_once __DIR__ . DIRECTORY_SEPARATOR . 'municipalities.php';
require_once __DIR__ . DIRECTORY_SEPARATOR . 'japanese_search.php';

// 例規集横断検索ページと API で共有する検索ロジック。

function reiki_search_ready_cache_path(): string
{
    return data_path('background_tasks/reiki_ready_municipalities.json');
}

function reiki_search_ready_cache_ttl_seconds(): int
{
    // 例規集の全文検索対応有無は index build のタイミングでしか変わらない。
    return 3600;
}

function reiki_search_open_pdo(string $dbPath): PDO
{
    $pdo = new PDO('sqlite:' . $dbPath);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $pdo->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);
    // 横断検索 API は読み取り専用なので、書込み待ちの影響を受けにくくする。
    $pdo->exec('PRAGMA query_only = ON');
    $pdo->exec('PRAGMA busy_timeout = 1500');
    return $pdo;
}

function reiki_search_last_date(PDO $pdo): ?string
{
    // MAX() 集計より、制定日 index の末尾から拾う形のほうが軽い。
    $stmt = $pdo->query(
        "SELECT enactment_date AS last_date
           FROM ordinances
          WHERE enactment_date IS NOT NULL
            AND enactment_date <> ''
       ORDER BY enactment_date DESC, id DESC
          LIMIT 1"
    );
    $row = $stmt->fetch();
    $value = is_array($row) ? (string)($row['last_date'] ?? '') : '';
    return $value !== '' ? $value : null;
}

// 単に DB があるだけでは不十分で、横断検索には FTS テーブルの存在まで必要。
function reiki_search_db_has_fts(string $dbPath): bool
{
    static $cache = [];
    if (array_key_exists($dbPath, $cache)) {
        return $cache[$dbPath];
    }
    if ($dbPath === '' || !is_file($dbPath)) {
        $cache[$dbPath] = false;
        return false;
    }

    try {
        $pdo = reiki_search_open_pdo($dbPath);
        $stmt = $pdo->query("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ordinances_fts' LIMIT 1");
        $cache[$dbPath] = (bool)$stmt->fetchColumn();
    } catch (Throwable) {
        $cache[$dbPath] = false;
    }
    return $cache[$dbPath];
}

function reiki_search_public_summary(array $municipality): array
{
    $feature = is_array($municipality['reiki'] ?? null) ? $municipality['reiki'] : [];
    return [
        'slug' => (string)($municipality['slug'] ?? ''),
        'code' => (string)($municipality['code'] ?? ''),
        'name' => (string)($municipality['name'] ?? ''),
        'page_title' => (string)($feature['title'] ?? (($municipality['name'] ?? '') . '例規集')),
        'url' => (string)($feature['url'] ?? ''),
    ];
}

function reiki_search_ready_summaries(): array
{
    static $cache = null;
    if (is_array($cache)) {
        return $cache;
    }

    $cachePath = reiki_search_ready_cache_path();
    $cached = read_json_cache_file($cachePath, reiki_search_ready_cache_ttl_seconds());
    if (is_array($cached)) {
        $cache = array_values(array_filter($cached, 'is_array'));
        return $cache;
    }

    $items = [];
    foreach (municipality_catalog() as $slug => $municipality) {
        if (!municipality_feature_enabled((string)$slug, 'reiki')) {
            continue;
        }

        $feature = $municipality['reiki'] ?? null;
        $dbPath = trim((string)($feature['db_path'] ?? ''));
        if (!reiki_search_db_has_fts($dbPath)) {
            continue;
        }

        $items[] = reiki_search_public_summary($municipality);
    }

    write_json_cache_file($cachePath, $items);
    $cache = $items;
    return $cache;
}

function reiki_search_ready_municipalities(): array
{
    $items = [];
    foreach (reiki_search_ready_summaries() as $summary) {
        $slug = trim((string)($summary['slug'] ?? ''));
        if ($slug === '') {
            continue;
        }
        $municipality = municipality_entry($slug);
        if (!is_array($municipality)) {
            continue;
        }
        $items[$slug] = $municipality;
    }

    return $items;
}

// 最初の自治体 DB で一度だけ MATCH を試し、文法エラーを事前に返す。
function reiki_search_validate_query(string $query, array $municipalities): ?string
{
    $preparedQuery = japanese_search_prepare_query($query);
    if (($preparedQuery['raw_query'] ?? '') === '') {
        return 'キーワードを入力してください。';
    }
    if (($preparedQuery['fts_query'] ?? '') === '') {
        return '検索語を解釈できませんでした。キーワードを調整してください。';
    }

    if ($municipalities === []) {
        return '検索可能な例規集データがまだありません。';
    }

    $firstMunicipality = reset($municipalities);
    if (!is_array($firstMunicipality)) {
        return '検索可能な例規集データがまだありません。';
    }

    $feature = is_array($firstMunicipality['reiki'] ?? null) ? $firstMunicipality['reiki'] : [];
    $dbPath = trim((string)($feature['db_path'] ?? ''));
    if (!reiki_search_db_has_fts($dbPath)) {
        return '検索可能な例規集データがまだありません。';
    }

    try {
        $pdo = reiki_search_open_pdo($dbPath);
        $stmt = $pdo->prepare('SELECT rowid FROM ordinances_fts WHERE ordinances_fts MATCH :q LIMIT 1');
        $stmt->bindValue(':q', (string)$preparedQuery['fts_query'], PDO::PARAM_STR);
        $stmt->execute();
    } catch (Throwable) {
        return '検索式の解釈に失敗しました。キーワードを調整してください。';
    }

    return null;
}

function reiki_search_execute(array $municipality, string $query, int $page = 1, int $perPage = 8): array
{
    $preparedQuery = japanese_search_prepare_query($query);
    $summary = reiki_search_public_summary($municipality);
    $feature = is_array($municipality['reiki'] ?? null) ? $municipality['reiki'] : [];
    $dbPath = trim((string)($feature['db_path'] ?? ''));
    $page = max(1, $page);
    $perPage = max(1, min(20, $perPage));
    $offset = ($page - 1) * $perPage;

    if (($preparedQuery['raw_query'] ?? '') === '' || ($preparedQuery['fts_query'] ?? '') === '') {
        return $summary + [
            'status' => 'query_error',
            'error' => '検索語を解釈できませんでした。キーワードを調整してください。',
            'rows' => [],
            'total' => 0,
            'page' => $page,
            'per_page' => $perPage,
            'total_pages' => 0,
            'start' => 0,
            'end' => 0,
            'stats' => [
                'last_date' => null,
            ],
        ];
    }

    if ($dbPath === '' || !is_file($dbPath)) {
        return $summary + [
            'status' => 'missing_db',
            'error' => '全文検索対応の SQLite が見つかりません。',
            'rows' => [],
            'total' => 0,
            'page' => $page,
            'per_page' => $perPage,
            'total_pages' => 0,
            'start' => 0,
            'end' => 0,
            'stats' => [
                'last_date' => null,
            ],
        ];
    }

    try {
        $pdo = reiki_search_open_pdo($dbPath);
    } catch (Throwable) {
        return $summary + [
            'status' => 'db_error',
            'error' => 'SQLiteの読み込みに失敗しました。',
            'rows' => [],
            'total' => 0,
            'page' => $page,
            'per_page' => $perPage,
            'total_pages' => 0,
            'start' => 0,
            'end' => 0,
            'stats' => [
                'last_date' => null,
            ],
        ];
    }

    try {
        $lastDate = reiki_search_last_date($pdo);

        $stmt = $pdo->prepare('
            SELECT
                o.id,
                o.filename,
                o.title,
                o.document_type,
                o.enactment_date,
                o.responsible_department,
                o.combined_stance,
                o.source_url,
                o.content_text AS excerpt_source,
                ordinances_fts.rank AS score
            FROM ordinances_fts
            JOIN ordinances o ON o.id = ordinances_fts.rowid
            WHERE ordinances_fts MATCH :q
            ORDER BY score
            LIMIT :limit OFFSET :offset
        ');
        $stmt->bindValue(':q', (string)$preparedQuery['fts_query'], PDO::PARAM_STR);
        $stmt->bindValue(':limit', $perPage + 1, PDO::PARAM_INT);
        $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
        $stmt->execute();
        $rows = $stmt->fetchAll();
    } catch (Throwable) {
        return $summary + [
            'status' => 'search_error',
            'error' => 'この自治体の検索結果を読み込めませんでした。しばらくしてから再度お試しください。',
            'rows' => [],
            'total' => 0,
            'page' => $page,
            'per_page' => $perPage,
            'total_pages' => 0,
            'start' => 0,
            'end' => 0,
            'stats' => [
                'last_date' => $lastDate ?? null,
            ],
        ];
    }

    $hasMore = count($rows) > $perPage;
    $rows = array_slice($rows, 0, $perPage);
    $serializedRows = [];
    foreach ($rows as $row) {
        if (!is_array($row)) {
            continue;
        }
        $serializedRows[] = reiki_search_result_row($summary, $row, $preparedQuery);
    }

    $knownTotal = $offset + count($serializedRows) + ($hasMore ? 1 : 0);
    $totalExact = !$hasMore;

    return $summary + [
        'status' => 'ok',
        'error' => '',
        'rows' => $serializedRows,
        'total' => $knownTotal,
        'total_exact' => $totalExact,
        'has_more' => $hasMore,
        'page' => $page,
        'per_page' => $perPage,
        'total_pages' => $totalExact ? max(1, (int)ceil($knownTotal / $perPage)) : ($page + ($hasMore ? 1 : 0)),
        'start' => count($serializedRows) > 0 ? ($offset + 1) : 0,
        'end' => $offset + count($serializedRows),
        'stats' => [
            'last_date' => $lastDate,
        ],
    ];
}

function reiki_search_execute_preview(array $municipality, string $query, int $perPage = 3): array
{
    $preparedQuery = japanese_search_prepare_query($query);
    $summary = reiki_search_public_summary($municipality);
    $feature = is_array($municipality['reiki'] ?? null) ? $municipality['reiki'] : [];
    $dbPath = trim((string)($feature['db_path'] ?? ''));
    $perPage = max(1, min(6, $perPage));

    if (($preparedQuery['raw_query'] ?? '') === '' || ($preparedQuery['fts_query'] ?? '') === '') {
        return $summary + [
            'status' => 'query_error',
            'error' => '検索語を解釈できませんでした。キーワードを調整してください。',
            'rows' => [],
            'total' => 0,
            'total_exact' => true,
            'has_more' => false,
            'page' => 1,
            'per_page' => $perPage,
            'total_pages' => 1,
            'start' => 0,
            'end' => 0,
            'stats' => [
                'last_date' => null,
            ],
        ];
    }

    if ($dbPath === '' || !is_file($dbPath)) {
        return $summary + [
            'status' => 'missing_db',
            'error' => '全文検索対応の SQLite が見つかりません。',
            'rows' => [],
            'total' => 0,
            'total_exact' => true,
            'has_more' => false,
            'page' => 1,
            'per_page' => $perPage,
            'total_pages' => 1,
            'start' => 0,
            'end' => 0,
            'stats' => [
                'last_date' => null,
            ],
        ];
    }

    try {
        $pdo = reiki_search_open_pdo($dbPath);
    } catch (Throwable) {
        return $summary + [
            'status' => 'db_error',
            'error' => 'SQLiteの読み込みに失敗しました。',
            'rows' => [],
            'total' => 0,
            'total_exact' => true,
            'has_more' => false,
            'page' => 1,
            'per_page' => $perPage,
            'total_pages' => 1,
            'start' => 0,
            'end' => 0,
            'stats' => [
                'last_date' => null,
            ],
        ];
    }

    try {
        $lastDate = reiki_search_last_date($pdo);
        $stmt = $pdo->prepare('
            SELECT
                o.id,
                o.filename,
                o.title,
                o.document_type,
                o.enactment_date,
                o.responsible_department,
                o.combined_stance,
                o.source_url,
                o.content_text AS excerpt_source,
                ordinances_fts.rank AS score
            FROM ordinances_fts
            JOIN ordinances o ON o.id = ordinances_fts.rowid
            WHERE ordinances_fts MATCH :q
            ORDER BY score
            LIMIT :limit
        ');
        $stmt->bindValue(':q', (string)$preparedQuery['fts_query'], PDO::PARAM_STR);
        $stmt->bindValue(':limit', $perPage + 1, PDO::PARAM_INT);
        $stmt->execute();
        $rows = $stmt->fetchAll();
    } catch (Throwable) {
        return $summary + [
            'status' => 'search_error',
            'error' => 'この自治体の検索結果を読み込めませんでした。しばらくしてから再度お試しください。',
            'rows' => [],
            'total' => 0,
            'total_exact' => true,
            'has_more' => false,
            'page' => 1,
            'per_page' => $perPage,
            'total_pages' => 1,
            'start' => 0,
            'end' => 0,
            'stats' => [
                'last_date' => $lastDate ?? null,
            ],
        ];
    }

    $hasMore = count($rows) > $perPage;
    $rows = array_slice($rows, 0, $perPage);
    $serializedRows = [];
    foreach ($rows as $row) {
        if (!is_array($row)) {
            continue;
        }
        $serializedRows[] = reiki_search_result_row($summary, $row, $preparedQuery);
    }

    $knownTotal = count($serializedRows);
    return $summary + [
        'status' => 'ok',
        'error' => '',
        'rows' => $serializedRows,
        'total' => $knownTotal,
        'total_exact' => !$hasMore,
        'has_more' => $hasMore,
        'page' => 1,
        'per_page' => $perPage,
        'total_pages' => 1,
        'start' => $knownTotal > 0 ? 1 : 0,
        'end' => $knownTotal,
        'stats' => [
            'last_date' => $lastDate ?? null,
        ],
    ];
}

// 横断検索の結果から、自治体別画面へ飛ぶ URL もここでまとめて生成する。
function reiki_search_result_row(array $municipalitySummary, array $row, ?array $preparedQuery = null): array
{
    $preparedQuery = $preparedQuery ?? ['highlight_terms' => []];
    $detailUrl = '/reiki/?' . http_build_query([
        'slug' => (string)($municipalitySummary['slug'] ?? ''),
        'file' => (string)($row['filename'] ?? '') . '.html',
    ]);

    $browseUrl = '/reiki/?' . http_build_query([
        'slug' => (string)($municipalitySummary['slug'] ?? ''),
    ]);

    $excerpt = japanese_search_build_excerpt(
        (string)($row['excerpt_source'] ?? ''),
        is_array($preparedQuery['highlight_terms'] ?? null) ? $preparedQuery['highlight_terms'] : []
    );

    return [
        'id' => (int)($row['id'] ?? 0),
        'filename' => (string)($row['filename'] ?? ''),
        'title' => (string)($row['title'] ?? ''),
        'document_type' => (string)($row['document_type'] ?? ''),
        'enactment_date' => (string)($row['enactment_date'] ?? ''),
        'responsible_department' => (string)($row['responsible_department'] ?? ''),
        'combined_stance' => (string)($row['combined_stance'] ?? ''),
        'source_url' => (string)($row['source_url'] ?? ''),
        'excerpt' => $excerpt,
        'detail_url' => $detailUrl,
        'browse_url' => $browseUrl,
    ];
}
