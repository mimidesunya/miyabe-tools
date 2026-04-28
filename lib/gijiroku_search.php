<?php
declare(strict_types=1);

require_once __DIR__ . DIRECTORY_SEPARATOR . 'municipalities.php';
require_once __DIR__ . DIRECTORY_SEPARATOR . 'japanese_search.php';
require_once __DIR__ . DIRECTORY_SEPARATOR . 'gijiroku' . DIRECTORY_SEPARATOR . 'index_helpers.php';

// 会議録横断検索ページと API で共有する検索ロジック。

function gijiroku_search_ready_cache_path(): string
{
    return data_path('background_tasks/gijiroku_ready_municipalities.json');
}

function gijiroku_search_ready_cache_ttl_seconds(): int
{
    // 検索対象自治体の増減はスクレイパや deploy のタイミングでしか大きく変わらない。
    return 3600;
}

function gijiroku_search_public_summary(array $municipality): array
{
    $feature = is_array($municipality['gijiroku'] ?? null) ? $municipality['gijiroku'] : [];
    $prefCode = municipality_prefecture_code_from_code((string)($municipality['code'] ?? ''));
    return [
        'slug' => (string)($municipality['slug'] ?? ''),
        'code' => (string)($municipality['code'] ?? ''),
        'pref_code' => $prefCode,
        'pref_name' => municipality_prefecture_name_from_code($prefCode),
        'name' => (string)($municipality['name'] ?? ''),
        'assembly_name' => (string)($feature['assembly_name'] ?? (($municipality['name'] ?? '') . '議会')),
        'url' => (string)($feature['url'] ?? ''),
    ];
}

function gijiroku_search_ready_summaries(): array
{
    static $cache = null;
    if (is_array($cache)) {
        return $cache;
    }

    $cachePath = gijiroku_search_ready_cache_path();
    $catalogCachePath = municipality_catalog_cache_path();
    // 自治体 catalog が self-heal されたら、ready 一覧も古い cache を使わず追従させる。
    if (json_cache_file_is_fresh($cachePath, gijiroku_search_ready_cache_ttl_seconds(), [$catalogCachePath])) {
        $cached = read_json_cache_file($cachePath);
    } else {
        $cached = null;
    }
    if (is_array($cached)) {
        $cache = array_values(array_filter($cached, 'is_array'));
        return $cache;
    }

    $items = [];
    foreach (municipality_catalog() as $slug => $municipality) {
        if (!municipality_feature_enabled((string)$slug, 'gijiroku')) {
            continue;
        }

        $feature = $municipality['gijiroku'] ?? null;
        $dbPath = trim((string)($feature['db_path'] ?? ''));
        if ($dbPath === '' || !is_file($dbPath)) {
            continue;
        }

        $items[] = gijiroku_search_public_summary($municipality);
    }

    write_json_cache_file($cachePath, $items);
    $cache = $items;
    return $cache;
}

// 横断検索では DB 未生成の自治体を除外し、検索可能なものだけを返す。
function gijiroku_search_ready_municipalities(): array
{
    $items = [];
    foreach (gijiroku_search_ready_summaries() as $summary) {
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
function gijiroku_search_validate_query(string $query, array $municipalities): ?string
{
    $preparedQuery = japanese_search_prepare_query($query);
    if (($preparedQuery['raw_query'] ?? '') === '') {
        return 'キーワードを入力してください。';
    }
    if (($preparedQuery['fts_query'] ?? '') === '') {
        return '検索語を解釈できませんでした。キーワードを調整してください。';
    }

    if ($municipalities === []) {
        return '検索可能な会議録データがまだありません。';
    }

    $firstMunicipality = reset($municipalities);
    if (!is_array($firstMunicipality)) {
        return '検索可能な会議録データがまだありません。';
    }

    $feature = is_array($firstMunicipality['gijiroku'] ?? null) ? $firstMunicipality['gijiroku'] : [];
    $dbPath = trim((string)($feature['db_path'] ?? ''));
    if ($dbPath === '' || !is_file($dbPath)) {
        return '検索可能な会議録データがまだありません。';
    }

    try {
        $pdo = gijiroku_search_open_pdo($dbPath);
        $stmt = $pdo->prepare('SELECT rowid FROM minutes_fts WHERE minutes_fts MATCH :q LIMIT 1');
        $stmt->bindValue(':q', (string)$preparedQuery['fts_query'], PDO::PARAM_STR);
        $stmt->execute();
    } catch (Throwable) {
        return '検索式の解釈に失敗しました。キーワードを調整してください。';
    }

    return null;
}

function gijiroku_search_open_pdo(string $dbPath): PDO
{
    $pdo = new PDO('sqlite:' . $dbPath);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $pdo->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);
    // 横断検索 API は読み取り専用なので、書込み待ちの影響を受けにくくする。
    $pdo->exec('PRAGMA query_only = ON');
    $pdo->exec('PRAGMA busy_timeout = 1500');
    return $pdo;
}

function gijiroku_search_browse_url(string $slug, string $query = ''): string
{
    $params = ['slug' => $slug];
    $normalizedQuery = trim($query);
    if ($normalizedQuery !== '') {
        $params['q'] = $normalizedQuery;
    }

    return '/gijiroku/?' . http_build_query($params);
}

function gijiroku_search_detail_url(string $slug, int $id, string $query = ''): string
{
    $params = [
        'slug' => $slug,
        'doc' => $id,
        'tab' => 'viewer',
        'viewer_tab' => 'matches',
    ];
    $normalizedQuery = trim($query);
    if ($normalizedQuery !== '') {
        $params['q'] = $normalizedQuery;
    }

    return '/gijiroku/?' . http_build_query($params);
}

function gijiroku_search_plain_excerpt(string $excerpt): string
{
    return str_replace(['[[[', ']]]'], '', $excerpt);
}

function gijiroku_search_text_length(string $text): int
{
    return function_exists('mb_strlen') ? mb_strlen($text, 'UTF-8') : strlen($text);
}

function gijiroku_search_validate_held_on(string $heldOn): ?string
{
    $normalized = trim($heldOn);
    if ($normalized === '') {
        return null;
    }
    if (preg_match('/^\d{4}-\d{2}-\d{2}$/', $normalized) !== 1) {
        return 'held_on は YYYY-MM-DD 形式で指定してください。';
    }

    $parsed = DateTimeImmutable::createFromFormat('!Y-m-d', $normalized);
    if (!$parsed instanceof DateTimeImmutable || $parsed->format('Y-m-d') !== $normalized) {
        return 'held_on は YYYY-MM-DD 形式で指定してください。';
    }

    return null;
}

function gijiroku_search_last_date(PDO $pdo): ?string
{
    // MAX() 集計より、doc_type と held_on の複合 index をそのまま使える形に寄せる。
    $stmt = $pdo->query(
        "SELECT held_on AS last_date
           FROM minutes
          WHERE doc_type = 'minutes'
            AND held_on IS NOT NULL
       ORDER BY held_on DESC, id DESC
          LIMIT 1"
    );
    $row = $stmt->fetch();
    $value = is_array($row) ? (string)($row['last_date'] ?? '') : '';
    return $value !== '' ? $value : null;
}

function gijiroku_search_latest_match_date(PDO $pdo, string $ftsQuery): ?string
{
    // 横断検索は「関連度」より「新しさ」で当たりを見たいので、MATCH 後の最新開催日を別で取る。
    $stmt = $pdo->prepare(
        "SELECT m.held_on AS latest_hit_date
           FROM minutes_fts
           JOIN minutes m ON m.id = minutes_fts.rowid
          WHERE minutes_fts MATCH :q
            AND m.held_on IS NOT NULL
       ORDER BY m.held_on DESC, m.id DESC
          LIMIT 1"
    );
    $stmt->bindValue(':q', $ftsQuery, PDO::PARAM_STR);
    $stmt->execute();
    $row = $stmt->fetch();
    $value = is_array($row) ? (string)($row['latest_hit_date'] ?? '') : '';
    return $value !== '' ? $value : null;
}

function gijiroku_search_fetch_exact_phrase_rows(
    PDO $pdo,
    string $ftsQuery,
    array $exactPhrases,
    int $perPage,
    int $offset = 0
): array {
    $batchSize = max($perPage + 1, 50);
    $rawOffset = 0;
    $exactSeen = 0;
    $rows = [];
    $latestHitDate = null;
    $exhausted = false;
    $phraseLike = japanese_search_exact_phrase_like_clause(
        $exactPhrases,
        ['m.title', 'm.meeting_name', 'm.content'],
        'exact_phrase'
    );
    $phraseSql = (string)($phraseLike['sql'] ?? '');
    $phraseWhereSql = $phraseSql !== '' ? ' AND ' . $phraseSql : '';
    $phraseParams = is_array($phraseLike['params'] ?? null) ? $phraseLike['params'] : [];

    while (count($rows) <= $perPage) {
        $stmt = $pdo->prepare("SELECT m.id, m.title, m.meeting_name, m.year_label, m.held_on, m.rel_path, m.source_url, m.content AS excerpt_source
                                 FROM minutes_fts
                                 JOIN minutes m ON m.id = minutes_fts.rowid
                                WHERE minutes_fts MATCH :q
                                  {$phraseWhereSql}
                             ORDER BY COALESCE(m.held_on, '') DESC, m.id DESC
                                LIMIT :limit OFFSET :offset");
        $stmt->bindValue(':q', $ftsQuery, PDO::PARAM_STR);
        foreach ($phraseParams as $param => $value) {
            $stmt->bindValue((string)$param, (string)$value, PDO::PARAM_STR);
        }
        $stmt->bindValue(':limit', $batchSize, PDO::PARAM_INT);
        $stmt->bindValue(':offset', $rawOffset, PDO::PARAM_INT);
        $stmt->execute();
        $candidates = $stmt->fetchAll();
        if ($candidates === []) {
            $exhausted = true;
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
            if ($latestHitDate === null) {
                $heldOn = trim((string)($candidate['held_on'] ?? ''));
                if ($heldOn !== '') {
                    $latestHitDate = $heldOn;
                }
            }
            if ($exactSeen++ < $offset) {
                continue;
            }
            $rows[] = $candidate;
            if (count($rows) > $perPage) {
                break 2;
            }
        }

        $rawOffset += count($candidates);
        if (count($candidates) < $batchSize) {
            $exhausted = true;
            break;
        }
    }

    $hasMore = count($rows) > $perPage;
    $rows = array_slice($rows, 0, $perPage);

    return [
        'rows' => $rows,
        'has_more' => $hasMore,
        'total' => $offset + count($rows) + ($hasMore ? 1 : 0),
        'total_exact' => !$hasMore && $exhausted,
        'latest_hit_date' => $latestHitDate,
    ];
}

function gijiroku_search_list_documents(
    array $municipality,
    string $heldOn = '',
    int $page = 1,
    int $perPage = 20
): array {
    $summary = gijiroku_search_public_summary($municipality);
    $feature = is_array($municipality['gijiroku'] ?? null) ? $municipality['gijiroku'] : [];
    $dbPath = trim((string)($feature['db_path'] ?? ''));
    $normalizedHeldOn = trim($heldOn);
    $page = max(1, $page);
    $perPage = max(1, min(100, $perPage));
    $offset = ($page - 1) * $perPage;

    $heldOnError = gijiroku_search_validate_held_on($normalizedHeldOn);
    if ($heldOnError !== null) {
        return $summary + [
            'status' => 'invalid_date',
            'error' => $heldOnError,
            'held_on' => $normalizedHeldOn,
            'rows' => [],
            'total' => 0,
            'total_exact' => true,
            'has_more' => false,
            'page' => $page,
            'per_page' => $perPage,
            'total_pages' => 0,
            'start' => 0,
            'end' => 0,
        ];
    }

    if ($dbPath === '' || !is_file($dbPath)) {
        return $summary + [
            'status' => 'missing_db',
            'error' => '検索DBが見つかりません。',
            'held_on' => $normalizedHeldOn,
            'rows' => [],
            'total' => 0,
            'total_exact' => true,
            'has_more' => false,
            'page' => $page,
            'per_page' => $perPage,
            'total_pages' => 0,
            'start' => 0,
            'end' => 0,
        ];
    }

    try {
        $pdo = gijiroku_search_open_pdo($dbPath);
    } catch (Throwable) {
        return $summary + [
            'status' => 'db_error',
            'error' => 'SQLiteの読み込みに失敗しました。',
            'held_on' => $normalizedHeldOn,
            'rows' => [],
            'total' => 0,
            'total_exact' => true,
            'has_more' => false,
            'page' => $page,
            'per_page' => $perPage,
            'total_pages' => 0,
            'start' => 0,
            'end' => 0,
        ];
    }

    try {
        if ($normalizedHeldOn !== '') {
            $countStmt = $pdo->prepare(
                "SELECT COUNT(*)
                   FROM minutes
                  WHERE doc_type = 'minutes'
                    AND held_on = :held_on"
            );
            $countStmt->bindValue(':held_on', $normalizedHeldOn, PDO::PARAM_STR);
            $countStmt->execute();
            $total = (int)$countStmt->fetchColumn();

            $stmt = $pdo->prepare(
                "SELECT id, title, meeting_name, year_label, held_on, rel_path, source_url, substr(content, 1, 240) AS excerpt_source
                   FROM minutes
                  WHERE doc_type = 'minutes'
                    AND held_on = :held_on
               ORDER BY id DESC
                  LIMIT :limit OFFSET :offset"
            );
            $stmt->bindValue(':held_on', $normalizedHeldOn, PDO::PARAM_STR);
        } else {
            $total = (int)$pdo->query("SELECT COUNT(*) FROM minutes WHERE doc_type = 'minutes'")->fetchColumn();
            $stmt = $pdo->prepare(
                "SELECT id, title, meeting_name, year_label, held_on, rel_path, source_url, substr(content, 1, 240) AS excerpt_source
                   FROM minutes
                  WHERE doc_type = 'minutes'
               ORDER BY COALESCE(held_on, '') DESC, id DESC
                  LIMIT :limit OFFSET :offset"
            );
        }
        $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
        $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
        $stmt->execute();
        $rows = $stmt->fetchAll();
    } catch (Throwable) {
        return $summary + [
            'status' => 'search_error',
            'error' => '会議一覧の読み込みに失敗しました。',
            'held_on' => $normalizedHeldOn,
            'rows' => [],
            'total' => 0,
            'total_exact' => true,
            'has_more' => false,
            'page' => $page,
            'per_page' => $perPage,
            'total_pages' => 0,
            'start' => 0,
            'end' => 0,
        ];
    }

    $serializedRows = [];
    foreach ($rows as $row) {
        if (!is_array($row)) {
            continue;
        }
        $serializedRows[] = gijiroku_search_result_row($summary, '', $row);
    }

    $totalPages = $total > 0 ? (int)ceil($total / $perPage) : 0;
    return $summary + [
        'status' => 'ok',
        'error' => '',
        'held_on' => $normalizedHeldOn,
        'rows' => $serializedRows,
        'total' => $total,
        'total_exact' => true,
        'has_more' => $page < $totalPages,
        'page' => $page,
        'per_page' => $perPage,
        'total_pages' => $totalPages,
        'start' => count($serializedRows) > 0 ? ($offset + 1) : 0,
        'end' => $offset + count($serializedRows),
    ];
}

function gijiroku_search_get_document(array $municipality, int $id, string $query = ''): array
{
    $summary = gijiroku_search_public_summary($municipality);
    $feature = is_array($municipality['gijiroku'] ?? null) ? $municipality['gijiroku'] : [];
    $dbPath = trim((string)($feature['db_path'] ?? ''));
    $normalizedQuery = trim($query);
    $documentId = max(0, $id);

    if ($documentId <= 0) {
        return $summary + [
            'status' => 'invalid_id',
            'error' => 'id は 1 以上で指定してください。',
            'query' => $normalizedQuery,
        ];
    }

    if ($dbPath === '' || !is_file($dbPath)) {
        return $summary + [
            'status' => 'missing_db',
            'error' => '検索DBが見つかりません。',
            'query' => $normalizedQuery,
            'id' => $documentId,
        ];
    }

    try {
        $pdo = gijiroku_search_open_pdo($dbPath);
    } catch (Throwable) {
        return $summary + [
            'status' => 'db_error',
            'error' => 'SQLiteの読み込みに失敗しました。',
            'query' => $normalizedQuery,
            'id' => $documentId,
        ];
    }

    try {
        $stmt = $pdo->prepare(
            "SELECT id, title, meeting_name, year_label, held_on, rel_path, source_url, source_fino, content
               FROM minutes
              WHERE id = :id
                AND doc_type = 'minutes'"
        );
        $stmt->bindValue(':id', $documentId, PDO::PARAM_INT);
        $stmt->execute();
        $detail = $stmt->fetch();
    } catch (Throwable) {
        return $summary + [
            'status' => 'search_error',
            'error' => '会議録全文の読み込みに失敗しました。',
            'query' => $normalizedQuery,
            'id' => $documentId,
        ];
    }

    if (!is_array($detail)) {
        return $summary + [
            'status' => 'not_found',
            'error' => '指定した会議録が見つかりません。',
            'query' => $normalizedQuery,
            'id' => $documentId,
        ];
    }

    $queryTerms = extract_query_terms($normalizedQuery);
    $document = annotate_document_matches(
        parse_minutes_document((string)($detail['content'] ?? '')),
        $queryTerms
    );
    $slug = (string)($summary['slug'] ?? '');
    $content = (string)($detail['content'] ?? '');

    return $summary + [
        'status' => 'ok',
        'error' => '',
        'query' => $normalizedQuery,
        'id' => (int)($detail['id'] ?? 0),
        'title' => (string)($detail['title'] ?? ''),
        'meeting_name' => (string)($detail['meeting_name'] ?? ''),
        'year_label' => (string)($detail['year_label'] ?? ''),
        'held_on' => (string)($detail['held_on'] ?? ''),
        'rel_path' => (string)($detail['rel_path'] ?? ''),
        'source_url' => (string)($detail['source_url'] ?? ''),
        'source_fino' => (string)($detail['source_fino'] ?? ''),
        'content' => $content,
        'content_length' => gijiroku_search_text_length($content),
        'document' => $document,
        'detail_url' => gijiroku_search_detail_url($slug, (int)($detail['id'] ?? 0), $normalizedQuery),
        'browse_url' => gijiroku_search_browse_url($slug, $normalizedQuery),
    ];
}

function gijiroku_search_execute(array $municipality, string $query, int $page = 1, int $perPage = 8): array
{
    $preparedQuery = japanese_search_prepare_query($query);
    $summary = gijiroku_search_public_summary($municipality);
    $feature = is_array($municipality['gijiroku'] ?? null) ? $municipality['gijiroku'] : [];
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
                'latest_hit_date' => null,
            ],
        ];
    }

    if ($dbPath === '' || !is_file($dbPath)) {
        return $summary + [
            'status' => 'missing_db',
            'error' => '検索DBが見つかりません。',
            'rows' => [],
            'total' => 0,
            'page' => $page,
            'per_page' => $perPage,
            'total_pages' => 0,
            'start' => 0,
            'end' => 0,
            'stats' => [
                'last_date' => null,
                'latest_hit_date' => null,
            ],
        ];
    }

    try {
        $pdo = gijiroku_search_open_pdo($dbPath);
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
                'latest_hit_date' => null,
            ],
        ];
    }

    try {
        $lastDate = gijiroku_search_last_date($pdo);
        $exactPhrases = japanese_search_exact_phrases_from_prepared($preparedQuery);
        if ($exactPhrases !== []) {
            $filtered = gijiroku_search_fetch_exact_phrase_rows(
                $pdo,
                (string)$preparedQuery['fts_query'],
                $exactPhrases,
                $perPage,
                $offset
            );
            $rows = $filtered['rows'];
            $latestHitDate = $filtered['latest_hit_date'];
        } else {
            $latestHitDate = gijiroku_search_latest_match_date($pdo, (string)$preparedQuery['fts_query']);

            // 横断検索は「まず最近の議論をざっと見る」用途なので、新しい開催日を優先する。
            $stmt = $pdo->prepare("SELECT m.id, m.title, m.meeting_name, m.year_label, m.held_on, m.rel_path, m.source_url, m.content AS excerpt_source
                                     FROM minutes_fts
                                     JOIN minutes m ON m.id = minutes_fts.rowid
                                    WHERE minutes_fts MATCH :q
                                 ORDER BY COALESCE(m.held_on, '') DESC, m.id DESC
                                    LIMIT :limit OFFSET :offset");
            $stmt->bindValue(':q', (string)$preparedQuery['fts_query'], PDO::PARAM_STR);
            $stmt->bindValue(':limit', $perPage + 1, PDO::PARAM_INT);
            $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
            $stmt->execute();
            $rows = $stmt->fetchAll();
            $filtered = null;
        }
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
                'last_date' => $lastDate,
                'latest_hit_date' => null,
            ],
        ];
    }

    $hasMore = isset($filtered) && is_array($filtered) ? (bool)$filtered['has_more'] : count($rows) > $perPage;
    $rows = isset($filtered) && is_array($filtered) ? $rows : array_slice($rows, 0, $perPage);
    $serializedRows = [];
    foreach ($rows as $row) {
        if (!is_array($row)) {
            continue;
        }
        $serializedRows[] = gijiroku_search_result_row($summary, $query, $row, $preparedQuery);
    }

    $knownTotal = isset($filtered) && is_array($filtered)
        ? (int)$filtered['total']
        : $offset + count($serializedRows) + ($hasMore ? 1 : 0);
    $totalExact = isset($filtered) && is_array($filtered) ? (bool)$filtered['total_exact'] : !$hasMore;

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
            'latest_hit_date' => $latestHitDate,
        ],
    ];
}

function gijiroku_search_execute_preview(array $municipality, string $query, int $perPage = 3): array
{
    $preparedQuery = japanese_search_prepare_query($query);
    $summary = gijiroku_search_public_summary($municipality);
    $feature = is_array($municipality['gijiroku'] ?? null) ? $municipality['gijiroku'] : [];
    $dbPath = trim((string)($feature['db_path'] ?? ''));
    $perPage = max(1, min(100, $perPage));

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
                'latest_hit_date' => null,
            ],
        ];
    }

    if ($dbPath === '' || !is_file($dbPath)) {
        return $summary + [
            'status' => 'missing_db',
            'error' => '検索DBが見つかりません。',
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
                'latest_hit_date' => null,
            ],
        ];
    }

    try {
        $pdo = gijiroku_search_open_pdo($dbPath);
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
                'latest_hit_date' => null,
            ],
        ];
    }

    try {
        $exactPhrases = japanese_search_exact_phrases_from_prepared($preparedQuery);
        if ($exactPhrases !== []) {
            $filtered = gijiroku_search_fetch_exact_phrase_rows(
                $pdo,
                (string)$preparedQuery['fts_query'],
                $exactPhrases,
                $perPage
            );
            $rows = $filtered['rows'];
        } else {
            // preview は自治体混合の並び替え用なので、本文全文はここでは引かない。
            $stmt = $pdo->prepare("SELECT m.id, m.title, m.meeting_name, m.year_label, m.held_on, m.rel_path, m.source_url
                                     FROM minutes_fts
                                     JOIN minutes m ON m.id = minutes_fts.rowid
                                    WHERE minutes_fts MATCH :q
                                 ORDER BY COALESCE(m.held_on, '') DESC, m.id DESC
                                    LIMIT :limit");
            $stmt->bindValue(':q', (string)$preparedQuery['fts_query'], PDO::PARAM_STR);
            $stmt->bindValue(':limit', $perPage + 1, PDO::PARAM_INT);
            $stmt->execute();
            $rows = $stmt->fetchAll();
            $filtered = null;
        }
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
                'last_date' => null,
                'latest_hit_date' => null,
            ],
        ];
    }

    $hasMore = isset($filtered) && is_array($filtered) ? (bool)$filtered['has_more'] : count($rows) > $perPage;
    $rows = isset($filtered) && is_array($filtered) ? $rows : array_slice($rows, 0, $perPage);
    $latestHitDate = null;
    if (isset($rows[0]) && is_array($rows[0])) {
        $heldOn = trim((string)($rows[0]['held_on'] ?? ''));
        if ($heldOn !== '') {
            $latestHitDate = $heldOn;
        }
    }
    $serializedRows = [];
    foreach ($rows as $row) {
        if (!is_array($row)) {
            continue;
        }
        $serializedRows[] = gijiroku_search_result_row($summary, $query, $row, $preparedQuery);
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
            'last_date' => null,
            'latest_hit_date' => $latestHitDate,
        ],
    ];
}

// 横断検索の結果から、自治体別画面へ飛ぶ URL もここでまとめて生成する。
function gijiroku_search_result_row(array $municipalitySummary, string $query, array $row, ?array $preparedQuery = null): array
{
    $preparedQuery = $preparedQuery ?? japanese_search_prepare_query($query);
    $slug = (string)($municipalitySummary['slug'] ?? '');
    $detailUrl = gijiroku_search_detail_url($slug, (int)($row['id'] ?? 0), $query);
    $browseUrl = gijiroku_search_browse_url($slug, $query);

    $excerpt = japanese_search_build_excerpt(
        (string)($row['excerpt_source'] ?? ''),
        is_array($preparedQuery['highlight_terms'] ?? null) ? $preparedQuery['highlight_terms'] : []
    );
    $plainExcerpt = gijiroku_search_plain_excerpt($excerpt);

    return [
        'id' => (int)($row['id'] ?? 0),
        'municipality_slug' => (string)($municipalitySummary['slug'] ?? ''),
        'municipality_name' => (string)($municipalitySummary['name'] ?? ''),
        'assembly_name' => (string)($municipalitySummary['assembly_name'] ?? ''),
        'title' => (string)($row['title'] ?? ''),
        'meeting_name' => (string)($row['meeting_name'] ?? ''),
        'year_label' => (string)($row['year_label'] ?? ''),
        'held_on' => (string)($row['held_on'] ?? ''),
        'rel_path' => (string)($row['rel_path'] ?? ''),
        'source_url' => (string)($row['source_url'] ?? ''),
        'excerpt' => $excerpt,
        'excerpt_plain' => $plainExcerpt,
        'detail_url' => $detailUrl,
        'browse_url' => $browseUrl,
    ];
}
