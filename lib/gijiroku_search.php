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

function gijiroku_search_meta_db_path(): string
{
    return data_path('background_tasks/gijiroku_municipalities.sqlite');
}

function gijiroku_search_ready_cache_ttl_seconds(): int
{
    // 検索対象自治体の増減はスクレイパや deploy のタイミングでしか大きく変わらない。
    return 3600;
}

function gijiroku_search_public_summary(array $municipality): array
{
    $feature = is_array($municipality['gijiroku'] ?? null) ? $municipality['gijiroku'] : [];
    $code = (string)($municipality['code'] ?? '');
    $prefCode = (string)($municipality['pref_code'] ?? '');
    if ($prefCode === '') {
        $prefCode = municipality_prefecture_code_from_code($code);
    }
    $name = (string)($municipality['name'] ?? '');
    $assemblyName = (string)($municipality['assembly_name'] ?? '');
    if ($assemblyName === '') {
        $assemblyName = (string)($feature['assembly_name'] ?? ($name . '議会'));
    }
    $url = (string)($municipality['url'] ?? '');
    if ($url === '') {
        $url = (string)($feature['url'] ?? '');
    }

    return [
        'slug' => (string)($municipality['slug'] ?? ''),
        'code' => $code,
        'pref_code' => $prefCode,
        'pref_name' => municipality_prefecture_name_from_code($prefCode),
        'name' => $name,
        'assembly_name' => $assemblyName,
        'url' => $url,
    ];
}

function gijiroku_search_internal_ready_record(array $municipality): array
{
    $summary = gijiroku_search_public_summary($municipality);
    $feature = is_array($municipality['gijiroku'] ?? null) ? $municipality['gijiroku'] : [];
    $dbPath = trim((string)($feature['db_path'] ?? ($municipality['db_path'] ?? '')));
    if ($dbPath !== '') {
        $summary['db_path'] = $dbPath;
    }
    return $summary;
}

function gijiroku_search_build_ready_records_from_catalog(): array
{
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

        $items[] = gijiroku_search_internal_ready_record($municipality);
    }
    return $items;
}

function gijiroku_search_meta_query_text(array $record): array
{
    $searchText = trim(implode(' ', [
        (string)($record['slug'] ?? ''),
        (string)($record['code'] ?? ''),
        (string)($record['pref_code'] ?? ''),
        (string)($record['pref_name'] ?? ''),
        (string)($record['name'] ?? ''),
        (string)($record['assembly_name'] ?? ''),
        (string)($record['url'] ?? ''),
    ]));

    return [
        $searchText,
        str_replace([' ', '　'], '', $searchText),
    ];
}

function gijiroku_search_like_pattern(string $value): string
{
    return '%' . strtr($value, [
        '\\' => '\\\\',
        '%' => '\\%',
        '_' => '\\_',
    ]) . '%';
}

function gijiroku_search_text_contains(string $haystack, string $needle): bool
{
    if ($needle === '') {
        return true;
    }
    if (function_exists('mb_stripos')) {
        return mb_stripos($haystack, $needle, 0, 'UTF-8') !== false;
    }
    return stripos($haystack, $needle) !== false;
}

function gijiroku_search_meta_query_terms(string $query): array
{
    $normalized = trim(str_replace('　', ' ', $query));
    if ($normalized === '') {
        return [];
    }

    $terms = preg_split('/\s+/', $normalized, -1, PREG_SPLIT_NO_EMPTY);
    if ($terms === false || $terms === []) {
        return [$normalized];
    }
    return array_values(array_map('strval', $terms));
}

function gijiroku_search_read_ready_records_from_meta_db(string $query = ''): ?array
{
    $dbPath = gijiroku_search_meta_db_path();
    if (!is_file($dbPath) || !class_exists(PDO::class) || !in_array('sqlite', PDO::getAvailableDrivers(), true)) {
        return null;
    }

    try {
        $pdo = new PDO('sqlite:' . $dbPath);
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $pdo->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);
        $pdo->exec('PRAGMA query_only = ON');
        $pdo->exec('PRAGMA busy_timeout = 500');

        $where = [];
        $params = [];
        $index = 0;
        foreach (gijiroku_search_meta_query_terms($query) as $term) {
            $param = ':term' . $index;
            $compactParam = ':compact_term' . $index;
            $where[] = "(search_text LIKE {$param} ESCAPE '\\' OR compact_text LIKE {$compactParam} ESCAPE '\\')";
            $params[$param] = gijiroku_search_like_pattern($term);
            $params[$compactParam] = gijiroku_search_like_pattern(str_replace([' ', '　'], '', $term));
            $index++;
        }

        $sql = "SELECT slug, code, pref_code, pref_name, name, assembly_name, url, db_path
                  FROM municipalities";
        if ($where !== []) {
            $sql .= ' WHERE ' . implode(' AND ', $where);
        }
        $sql .= ' ORDER BY code, slug';

        $stmt = $pdo->prepare($sql);
        foreach ($params as $param => $value) {
            $stmt->bindValue($param, $value, PDO::PARAM_STR);
        }
        $stmt->execute();
        $rows = $stmt->fetchAll();
        return array_values(array_filter($rows, 'is_array'));
    } catch (Throwable) {
        return null;
    }
}

function gijiroku_search_rebuild_meta_db(?string $targetPath = null): array
{
    $targetPath = $targetPath !== null && trim($targetPath) !== '' ? $targetPath : gijiroku_search_meta_db_path();
    $records = gijiroku_search_build_ready_records_from_catalog();
    $dir = dirname($targetPath);
    if (!is_dir($dir)) {
        @mkdir($dir, 0775, true);
    }

    $tmpPath = $targetPath . '.tmp.' . getmypid();
    if (is_file($tmpPath)) {
        @unlink($tmpPath);
    }

    $pdo = new PDO('sqlite:' . $tmpPath);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $pdo->exec('PRAGMA journal_mode = OFF');
    $pdo->exec('PRAGMA synchronous = OFF');
    $pdo->exec(
        "CREATE TABLE municipalities (
            slug TEXT PRIMARY KEY,
            code TEXT NOT NULL,
            pref_code TEXT NOT NULL,
            pref_name TEXT NOT NULL,
            name TEXT NOT NULL,
            assembly_name TEXT NOT NULL,
            url TEXT NOT NULL,
            db_path TEXT NOT NULL,
            search_text TEXT NOT NULL,
            compact_text TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"
    );
    $pdo->exec('CREATE INDEX idx_gijiroku_municipalities_code ON municipalities(code)');
    $pdo->exec('CREATE INDEX idx_gijiroku_municipalities_pref_code ON municipalities(pref_code)');
    $pdo->exec('CREATE INDEX idx_gijiroku_municipalities_name ON municipalities(name)');
    $pdo->exec('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)');

    $stmt = $pdo->prepare(
        'INSERT INTO municipalities
            (slug, code, pref_code, pref_name, name, assembly_name, url, db_path, search_text, compact_text, updated_at)
         VALUES
            (:slug, :code, :pref_code, :pref_name, :name, :assembly_name, :url, :db_path, :search_text, :compact_text, :updated_at)'
    );
    $updatedAt = gmdate('c');
    $pdo->beginTransaction();
    foreach ($records as $record) {
        [$searchText, $compactText] = gijiroku_search_meta_query_text($record);
        $stmt->execute([
            ':slug' => (string)($record['slug'] ?? ''),
            ':code' => (string)($record['code'] ?? ''),
            ':pref_code' => (string)($record['pref_code'] ?? ''),
            ':pref_name' => (string)($record['pref_name'] ?? ''),
            ':name' => (string)($record['name'] ?? ''),
            ':assembly_name' => (string)($record['assembly_name'] ?? ''),
            ':url' => (string)($record['url'] ?? ''),
            ':db_path' => (string)($record['db_path'] ?? ''),
            ':search_text' => $searchText,
            ':compact_text' => $compactText,
            ':updated_at' => $updatedAt,
        ]);
    }
    $metaStmt = $pdo->prepare('INSERT INTO metadata(key, value) VALUES(:key, :value)');
    $metaStmt->execute([':key' => 'schema', ':value' => 'gijiroku-municipality-meta-v1']);
    $metaStmt->execute([':key' => 'updated_at', ':value' => $updatedAt]);
    $metaStmt->execute([':key' => 'count', ':value' => (string)count($records)]);
    $pdo->commit();
    $pdo->exec('PRAGMA optimize');
    $pdo = null;

    if (DIRECTORY_SEPARATOR === '\\' && is_file($targetPath)) {
        @unlink($targetPath);
    }
    if (!@rename($tmpPath, $targetPath)) {
        @unlink($tmpPath);
        throw new RuntimeException('Failed to replace gijiroku municipality meta DB: ' . $targetPath);
    }

    write_json_cache_file(gijiroku_search_ready_cache_path(), $records);
    return $records;
}

function gijiroku_search_ready_records(): array
{
    static $cache = null;
    if (is_array($cache)) {
        return $cache;
    }

    $metaRecords = gijiroku_search_read_ready_records_from_meta_db();
    if (is_array($metaRecords)) {
        $cache = $metaRecords;
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
    if (!is_array($cached)) {
        // Public API/UI requests must not rebuild the municipality catalog synchronously when
        // the ready cache merely expired. Stale data is preferable to a gateway timeout.
        $cached = read_json_cache_file($cachePath);
    }
    if (is_array($cached)) {
        $cache = array_values(array_filter($cached, 'is_array'));
        return $cache;
    }

    // The catalog scan can be very expensive on the production data volume. Cache rebuilds
    // must be done by deploy/prewarm or scraper maintenance, not by public API requests.
    $cache = [];
    return $cache;
}

function gijiroku_search_ready_records_for_query(string $query): array
{
    $normalizedQuery = trim(str_replace('　', ' ', $query));
    if ($normalizedQuery === '') {
        return gijiroku_search_ready_records();
    }

    $metaRecords = gijiroku_search_read_ready_records_from_meta_db($normalizedQuery);
    if (is_array($metaRecords)) {
        return $metaRecords;
    }

    $items = [];
    foreach (gijiroku_search_ready_records() as $record) {
        [$searchText, $compactText] = gijiroku_search_meta_query_text($record);
        $matched = true;
        foreach (gijiroku_search_meta_query_terms($normalizedQuery) as $term) {
            $compactTerm = str_replace([' ', '　'], '', $term);
            if (
                !gijiroku_search_text_contains($searchText, $term)
                && !gijiroku_search_text_contains($compactText, $compactTerm)
            ) {
                $matched = false;
                break;
            }
        }
        if ($matched) {
            $items[] = $record;
        }
    }
    return $items;
}

function gijiroku_search_ready_summaries(): array
{
    static $cache = null;
    if (is_array($cache)) {
        return $cache;
    }

    $items = [];
    foreach (gijiroku_search_ready_records() as $record) {
        $items[] = gijiroku_search_public_summary($record);
    }
    return $cache = $items;
}

function gijiroku_search_ready_summaries_for_query(string $query): array
{
    $items = [];
    foreach (gijiroku_search_ready_records_for_query($query) as $record) {
        $items[] = gijiroku_search_public_summary($record);
    }
    return $items;
}

function gijiroku_search_ready_summary_index(): array
{
    static $index = null;
    if (is_array($index)) {
        return $index;
    }

    $index = [];
    foreach (gijiroku_search_ready_records() as $record) {
        $slug = trim((string)($record['slug'] ?? ''));
        if ($slug !== '') {
            $index[$slug] = $record;
        }
    }
    return $index;
}

function gijiroku_search_ready_slug_alias_index(): array
{
    static $index = null;
    if (is_array($index)) {
        return $index;
    }

    $index = [];
    foreach (gijiroku_search_ready_summary_index() as $slug => $record) {
        $aliases = [
            $slug,
            (string)($record['code'] ?? ''),
            (string)($record['name'] ?? ''),
            (string)($record['assembly_name'] ?? ''),
        ];
        foreach ($aliases as $alias) {
            $normalized = normalize_slug_alias_value((string)$alias);
            if ($normalized !== '' && !isset($index[$normalized])) {
                $index[$normalized] = $slug;
            }
        }
    }
    return $index;
}

function gijiroku_search_resolve_ready_slug(?string $input): string
{
    $candidate = trim((string)$input);
    if ($candidate === '') {
        return '';
    }

    $readyIndex = gijiroku_search_ready_summary_index();
    if (isset($readyIndex[$candidate])) {
        return $candidate;
    }

    $normalized = normalize_slug_alias_value($candidate);
    $aliasIndex = gijiroku_search_ready_slug_alias_index();
    $resolved = $normalized !== '' ? (string)($aliasIndex[$normalized] ?? '') : '';
    if ($resolved !== '') {
        return $resolved;
    }

    if (preg_match('/^[a-z0-9_-]+$/', $candidate) === 1 && is_file(gijiroku_search_default_db_path_for_slug($candidate))) {
        return $candidate;
    }

    return '';
}

function gijiroku_search_default_db_path_for_slug(string $slug): string
{
    if (preg_match('/^[a-z0-9_-]+$/', $slug) !== 1) {
        return '';
    }
    return data_path('gijiroku/' . $slug . '/minutes.sqlite');
}

function gijiroku_search_ready_record_to_municipality(array $record): ?array
{
    $slug = trim((string)($record['slug'] ?? ''));
    if ($slug === '') {
        return null;
    }

    $dbPath = trim((string)($record['db_path'] ?? ''));
    if ($dbPath === '' || !is_file($dbPath)) {
        $defaultDbPath = gijiroku_search_default_db_path_for_slug($slug);
        if ($defaultDbPath !== '' && is_file($defaultDbPath)) {
            $dbPath = $defaultDbPath;
        }
    }

    if ($dbPath === '' || !is_file($dbPath)) {
        // 古い cache か個別設定の自治体だけ、最後の手段として catalog を参照する。
        $municipality = municipality_entry($slug);
        return is_array($municipality) ? $municipality : null;
    }

    $name = (string)($record['name'] ?? $slug);
    return [
        'slug' => $slug,
        'code' => (string)($record['code'] ?? ''),
        'name' => $name,
        'gijiroku' => [
            'enabled' => true,
            'has_data' => true,
            'assembly_name' => (string)($record['assembly_name'] ?? ($name . '議会')),
            'url' => (string)($record['url'] ?? ''),
            'db_path' => $dbPath,
        ],
    ];
}

function gijiroku_search_ready_municipality(string $slug): ?array
{
    $resolved = gijiroku_search_resolve_ready_slug($slug);
    if ($resolved === '') {
        return null;
    }

    $record = gijiroku_search_ready_summary_index()[$resolved] ?? null;
    if (is_array($record)) {
        return gijiroku_search_ready_record_to_municipality($record);
    }

    $dbPath = gijiroku_search_default_db_path_for_slug($resolved);
    if ($dbPath !== '' && is_file($dbPath)) {
        return [
            'slug' => $resolved,
            'code' => '',
            'name' => $resolved,
            'gijiroku' => [
                'enabled' => true,
                'has_data' => true,
                'assembly_name' => $resolved,
                'url' => '',
                'db_path' => $dbPath,
            ],
        ];
    }

    return null;
}

// 横断検索では DB 未生成の自治体を除外し、検索可能なものだけを返す。
function gijiroku_search_ready_municipalities(): array
{
    $items = [];
    foreach (gijiroku_search_ready_records() as $summary) {
        $slug = trim((string)($summary['slug'] ?? ''));
        if ($slug === '') {
            continue;
        }
        $municipality = gijiroku_search_ready_record_to_municipality($summary);
        if (!is_array($municipality)) {
            continue;
        }
        $items[$slug] = $municipality;
    }

    return $items;
}

// クエリ前処理だけを検証する。実DBの MATCH は本検索側で一回だけ実行する。
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

function gijiroku_search_latest_match_date(PDO $pdo, string $ftsQuery, int $startYear = 0, int $endYear = 0): ?string
{
    // 横断検索は「関連度」より「新しさ」で当たりを見たいので、MATCH 後の最新開催日を別で取る。
    $yearFilter = gijiroku_search_year_filter_clause('m', $startYear, $endYear, 'latest_year');
    $stmt = $pdo->prepare(
        "SELECT m.held_on AS latest_hit_date
           FROM minutes_fts
           JOIN minutes m ON m.id = minutes_fts.rowid
          WHERE minutes_fts MATCH :q
            AND m.held_on IS NOT NULL
            {$yearFilter['sql']}
       ORDER BY m.held_on DESC, m.id DESC
          LIMIT 1"
    );
    $stmt->bindValue(':q', $ftsQuery, PDO::PARAM_STR);
    gijiroku_search_bind_string_params($stmt, $yearFilter['params']);
    $stmt->execute();
    $row = $stmt->fetch();
    $value = is_array($row) ? (string)($row['latest_hit_date'] ?? '') : '';
    return $value !== '' ? $value : null;
}

function gijiroku_search_candidate_limit(): int
{
    return 1000;
}

function gijiroku_search_exact_phrase_instr_clause(array $phrases, array $columns, string $paramPrefix = 'exact_phrase'): array
{
    $safePrefix = preg_replace('/[^A-Za-z0-9_]/', '_', $paramPrefix) ?: 'exact_phrase';
    $usableColumns = [];
    foreach ($columns as $column) {
        $column = trim((string)$column);
        if ($column !== '') {
            $usableColumns[] = $column;
        }
    }
    if ($usableColumns === []) {
        return ['sql' => '', 'params' => []];
    }

    $conditions = [];
    $params = [];
    $index = 0;
    foreach ($phrases as $phrase) {
        $text = trim(preg_replace('/[\s　]+/u', ' ', (string)$phrase) ?? (string)$phrase);
        if ($text === '') {
            continue;
        }

        $param = ':' . $safePrefix . $index++;
        $columnConditions = [];
        foreach ($usableColumns as $column) {
            $columnConditions[] = "instr({$column}, {$param}) > 0";
        }
        $conditions[] = '(' . implode(' OR ', $columnConditions) . ')';
        $params[$param] = $text;
    }

    return [
        'sql' => $conditions === [] ? '' : implode(' AND ', $conditions),
        'params' => $params,
    ];
}

function gijiroku_search_exact_excerpt_source_clause(array $exactPhrases): array
{
    $firstPhrase = '';
    foreach ($exactPhrases as $phrase) {
        $firstPhrase = trim(preg_replace('/[\s　]+/u', ' ', (string)$phrase) ?? (string)$phrase);
        if ($firstPhrase !== '') {
            break;
        }
    }

    if ($firstPhrase === '') {
        return [
            'sql' => 'substr(m.content, 1, 520) AS excerpt_source',
            'params' => [],
        ];
    }

    return [
        'sql' => "(CASE WHEN instr(m.content, :exact_excerpt_match) > 0 THEN substr(m.content, max(1, instr(m.content, :exact_excerpt_start) - 120), 520) ELSE substr(m.content, 1, 520) END) AS excerpt_source",
        'params' => [
            ':exact_excerpt_match' => $firstPhrase,
            ':exact_excerpt_start' => $firstPhrase,
        ],
    ];
}

function gijiroku_search_fetch_exact_phrase_rows(
    PDO $pdo,
    string $ftsQuery,
    array $exactPhrases,
    int $perPage,
    int $offset = 0,
    int $startYear = 0,
    int $endYear = 0,
    string $sort = 'date',
    int $candidateLimit = 0
): array {
    $sortMode = gijiroku_search_normalize_sort($sort);
    $candidateLimit = $candidateLimit > 0 ? max(1, $candidateLimit) : gijiroku_search_candidate_limit();
    $batchSize = min(50, max($perPage, 20));
    $rawOffset = 0;
    $rawScanned = 0;
    $targetRows = $offset + $perPage;
    $rows = [];
    $yearFilter = gijiroku_search_year_filter_clause('m0', $startYear, $endYear, 'exact_year');
    $candidateJoinSql = $yearFilter['sql'] !== '' ? 'JOIN minutes m0 ON m0.id = minutes_fts.rowid' : '';
    $phraseFilter = gijiroku_search_exact_phrase_instr_clause(
        $exactPhrases,
        ['m.title', 'm.meeting_name', 'm.content'],
        'exact_phrase'
    );
    $phraseFilterSql = trim((string)($phraseFilter['sql'] ?? ''));
    $whereSql = $phraseFilterSql !== '' ? 'WHERE ' . $phraseFilterSql : '';
    $excerptSource = gijiroku_search_exact_excerpt_source_clause($exactPhrases);
    $excerptSourceSql = (string)($excerptSource['sql'] ?? 'substr(m.content, 1, 520) AS excerpt_source');
    $orderSql = $sortMode === 'relevance' ? 'c.score' : 'm.held_on DESC, m.id DESC';

    while (count($rows) < $targetRows && $rawScanned < $candidateLimit) {
        $currentBatchSize = min($batchSize, $candidateLimit - $rawScanned);
        $rowLimit = max(1, $targetRows - count($rows));
        $stmt = $pdo->prepare(
            "SELECT m.id,
                    m.title,
                    m.meeting_name,
                    m.year_label,
                    m.held_on,
                    m.rel_path,
                    m.source_url,
                    {$excerptSourceSql},
                    c.score AS score
               FROM (
                     SELECT minutes_fts.rowid AS id,
                            minutes_fts.rank AS score
                       FROM minutes_fts
                       {$candidateJoinSql}
                      WHERE minutes_fts MATCH :q
                        {$yearFilter['sql']}
                   ORDER BY score
                      LIMIT :candidate_batch OFFSET :candidate_offset
                    ) c
               JOIN minutes m ON m.id = c.id
               {$whereSql}
           ORDER BY {$orderSql}
              LIMIT :row_limit"
        );
        $stmt->bindValue(':q', $ftsQuery, PDO::PARAM_STR);
        gijiroku_search_bind_string_params($stmt, $yearFilter['params']);
        gijiroku_search_bind_string_params($stmt, is_array($phraseFilter['params'] ?? null) ? $phraseFilter['params'] : []);
        gijiroku_search_bind_string_params($stmt, is_array($excerptSource['params'] ?? null) ? $excerptSource['params'] : []);
        $stmt->bindValue(':candidate_batch', $currentBatchSize, PDO::PARAM_INT);
        $stmt->bindValue(':candidate_offset', $rawOffset, PDO::PARAM_INT);
        $stmt->bindValue(':row_limit', $rowLimit, PDO::PARAM_INT);
        $stmt->execute();

        while (($row = $stmt->fetch()) !== false) {
            if (is_array($row)) {
                $rows[] = $row;
            }
        }
        $rawOffset += $currentBatchSize;
        $rawScanned += $currentBatchSize;
    }

    if ($sortMode === 'date') {
        usort($rows, static function (array $a, array $b): int {
            $dateCompare = strcmp((string)($b['held_on'] ?? ''), (string)($a['held_on'] ?? ''));
            if ($dateCompare !== 0) {
                return $dateCompare;
            }
            return ((int)($b['id'] ?? 0)) <=> ((int)($a['id'] ?? 0));
        });
    }

    $candidateLimitReached = $rawScanned >= $candidateLimit && count($rows) < $targetRows;
    $pageRows = array_slice($rows, $offset, $perPage);
    $hasMore = count($rows) >= $targetRows && ($rawScanned < $candidateLimit || $candidateLimitReached);
    $latestHitDate = null;
    if (isset($pageRows[0]) && is_array($pageRows[0])) {
        $heldOn = trim((string)($pageRows[0]['held_on'] ?? ''));
        if ($heldOn !== '') {
            $latestHitDate = $heldOn;
        }
    }

    return [
        'rows' => $pageRows,
        'has_more' => $hasMore,
        'total' => $offset + count($pageRows) + ($hasMore ? 1 : 0),
        'total_exact' => false,
        'latest_hit_date' => $latestHitDate,
        'candidate_limit' => $candidateLimit,
        'candidates_scanned' => min($rawScanned, $candidateLimit),
        'candidate_limit_reached' => $candidateLimitReached,
    ];
}

function gijiroku_search_preview_excerpt_source_clause(array $preparedQuery): array
{
    $terms = is_array($preparedQuery['highlight_terms'] ?? null)
        ? japanese_search_ordered_terms($preparedQuery['highlight_terms'])
        : [];
    $cases = [];
    $params = [];
    $index = 0;
    foreach ($terms as $term) {
        $term = trim((string)$term);
        if ($term === '') {
            continue;
        }

        $matchParam = ':excerpt_match_' . $index;
        $startParam = ':excerpt_start_' . $index;
        $cases[] = "WHEN instr(m.content, {$matchParam}) > 0 THEN substr(m.content, max(1, instr(m.content, {$startParam}) - 120), 520)";
        $params[$matchParam] = $term;
        $params[$startParam] = $term;
        $index++;
        if ($index >= 6) {
            break;
        }
    }

    $sql = $cases === []
        ? 'substr(m.content, 1, 520) AS excerpt_source'
        : '(CASE ' . implode(' ', $cases) . ' ELSE substr(m.content, 1, 520) END) AS excerpt_source';

    return [
        'sql' => $sql,
        'params' => $params,
    ];
}

function gijiroku_search_bind_string_params(PDOStatement $stmt, array $params): void
{
    foreach ($params as $name => $value) {
        $stmt->bindValue((string)$name, (string)$value, PDO::PARAM_STR);
    }
}

function gijiroku_search_normalize_year_range(int $startYear = 0, int $endYear = 0): array
{
    $startYear = $startYear > 0 ? max(1, min(9999, $startYear)) : 0;
    $endYear = $endYear > 0 ? max(1, min(9999, $endYear)) : 0;
    if ($startYear > 0 && $endYear > 0 && $startYear > $endYear) {
        [$startYear, $endYear] = [$endYear, $startYear];
    }

    return [
        'start_year' => $startYear,
        'end_year' => $endYear,
    ];
}

function gijiroku_search_normalize_sort(string $sort): string
{
    $normalized = strtolower(trim($sort));
    return in_array($normalized, ['date', 'relevance'], true) ? $normalized : 'date';
}

function gijiroku_search_year_filter_clause(
    string $tableAlias,
    int $startYear = 0,
    int $endYear = 0,
    string $paramPrefix = 'year'
): array {
    $range = gijiroku_search_normalize_year_range($startYear, $endYear);
    $alias = preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/', $tableAlias) === 1 ? $tableAlias : 'm';
    $prefix = preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/', $paramPrefix) === 1 ? $paramPrefix : 'year';
    $clauses = [];
    $params = [];

    if ($range['start_year'] > 0) {
        $name = ':' . $prefix . '_start_date';
        $clauses[] = "{$alias}.held_on >= {$name}";
        $params[$name] = sprintf('%04d-01-01', $range['start_year']);
    }
    if ($range['end_year'] > 0) {
        $name = ':' . $prefix . '_end_date';
        $clauses[] = "{$alias}.held_on <= {$name}";
        $params[$name] = sprintf('%04d-12-31', $range['end_year']);
    }

    return [
        'sql' => $clauses === [] ? '' : (' AND ' . implode(' AND ', $clauses)),
        'params' => $params,
        'start_year' => $range['start_year'],
        'end_year' => $range['end_year'],
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
               ORDER BY held_on DESC, id DESC
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

function gijiroku_search_execute(
    array $municipality,
    string $query,
    int $page = 1,
    int $perPage = 8,
    int $startYear = 0,
    int $endYear = 0,
    string $sort = 'date'
): array
{
    $preparedQuery = japanese_search_prepare_query($query);
    $summary = gijiroku_search_public_summary($municipality);
    $feature = is_array($municipality['gijiroku'] ?? null) ? $municipality['gijiroku'] : [];
    $dbPath = trim((string)($feature['db_path'] ?? ''));
    $page = max(1, $page);
    $perPage = max(1, min(20, $perPage));
    $offset = ($page - 1) * $perPage;
    $sortMode = gijiroku_search_normalize_sort($sort);

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
        $lastDate = null;
        $latestHitDate = null;
        $lastDate = gijiroku_search_last_date($pdo);
        $exactPhrases = japanese_search_exact_phrases_from_prepared($preparedQuery);
        if ($exactPhrases !== []) {
            $filtered = gijiroku_search_fetch_exact_phrase_rows(
                $pdo,
                (string)$preparedQuery['fts_query'],
                $exactPhrases,
                $perPage,
                $offset,
                $startYear,
                $endYear,
                $sortMode
            );
            $rows = $filtered['rows'];
            $latestHitDate = $filtered['latest_hit_date'];
        } else {
            $yearFilter = gijiroku_search_year_filter_clause('m', $startYear, $endYear, 'search_year');
            $excerptSource = gijiroku_search_preview_excerpt_source_clause($preparedQuery);
            $excerptSourceSql = (string)$excerptSource['sql'];
            // 横断検索は「まず最近の議論をざっと見る」用途なので、新しい開催日を優先する。
            if ($sortMode === 'relevance') {
                $stmt = $pdo->prepare("SELECT m.id, m.title, m.meeting_name, m.year_label, m.held_on, m.rel_path, m.source_url, {$excerptSourceSql}, minutes_fts.rank AS score
                                         FROM minutes_fts
                                         JOIN minutes m ON m.id = minutes_fts.rowid
                                        WHERE minutes_fts MATCH :q
                                          {$yearFilter['sql']}
                                     ORDER BY score
                                        LIMIT :limit OFFSET :offset");
            } else {
                $stmt = $pdo->prepare("SELECT m.id, m.title, m.meeting_name, m.year_label, m.held_on, m.rel_path, m.source_url, {$excerptSourceSql}
                                         FROM minutes_fts
                                         JOIN minutes m ON m.id = minutes_fts.rowid
                                        WHERE minutes_fts MATCH :q
                                          {$yearFilter['sql']}
                                     ORDER BY m.held_on DESC, m.id DESC
                                        LIMIT :limit OFFSET :offset");
            }
            $stmt->bindValue(':q', (string)$preparedQuery['fts_query'], PDO::PARAM_STR);
            gijiroku_search_bind_string_params($stmt, is_array($excerptSource['params'] ?? null) ? $excerptSource['params'] : []);
            gijiroku_search_bind_string_params($stmt, $yearFilter['params']);
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
    if ($latestHitDate === null && $sortMode === 'date' && isset($rows[0]) && is_array($rows[0])) {
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

    $knownTotal = isset($filtered) && is_array($filtered)
        ? (int)$filtered['total']
        : $offset + count($serializedRows) + ($hasMore ? 1 : 0);
    $totalExact = isset($filtered) && is_array($filtered) ? (bool)$filtered['total_exact'] : !$hasMore;
    $candidateLimitReached = isset($filtered) && is_array($filtered)
        ? (bool)($filtered['candidate_limit_reached'] ?? false)
        : false;

    return $summary + [
        'status' => 'ok',
        'error' => '',
        'sort' => $sortMode,
        'rows' => $serializedRows,
        'total' => $knownTotal,
        'total_exact' => $totalExact,
        'has_more' => $hasMore,
        'candidate_limit_reached' => $candidateLimitReached,
        'candidate_limit' => isset($filtered) && is_array($filtered) ? (int)($filtered['candidate_limit'] ?? 0) : 0,
        'candidates_scanned' => isset($filtered) && is_array($filtered) ? (int)($filtered['candidates_scanned'] ?? 0) : 0,
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

function gijiroku_search_execute_preview(
    array $municipality,
    string $query,
    int $perPage = 3,
    int $startYear = 0,
    int $endYear = 0
): array
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
                $perPage,
                0,
                $startYear,
                $endYear
            );
            $rows = $filtered['rows'];
        } else {
            $excerptSource = gijiroku_search_preview_excerpt_source_clause($preparedQuery);
            $excerptSourceSql = (string)$excerptSource['sql'];
            $yearFilter = gijiroku_search_year_filter_clause('m', $startYear, $endYear, 'preview_year');
            $stmt = $pdo->prepare("
                SELECT
                    m.id,
                    m.title,
                    m.meeting_name,
                    m.year_label,
                    m.held_on,
                    m.rel_path,
                    m.source_url,
                    {$excerptSourceSql}
                FROM minutes_fts
                JOIN minutes m ON m.id = minutes_fts.rowid
                WHERE minutes_fts MATCH :q
                {$yearFilter['sql']}
                ORDER BY m.held_on DESC, m.id DESC
                LIMIT :limit
            ");
            $stmt->bindValue(':q', (string)$preparedQuery['fts_query'], PDO::PARAM_STR);
            gijiroku_search_bind_string_params($stmt, is_array($excerptSource['params'] ?? null) ? $excerptSource['params'] : []);
            gijiroku_search_bind_string_params($stmt, $yearFilter['params']);
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
