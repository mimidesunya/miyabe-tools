<?php
declare(strict_types=1);

require_once __DIR__ . DIRECTORY_SEPARATOR . 'municipalities.php';

// Python 側の SudachiPy を呼び、PHP の検索リクエストでも同じ分かち書きを使う。

function japanese_search_python_script_path(): string
{
    return __DIR__ . DIRECTORY_SEPARATOR . 'python' . DIRECTORY_SEPARATOR . 'japanese_search_tokenizer.py';
}

function japanese_search_windows_python_candidates(): array
{
    if (DIRECTORY_SEPARATOR !== '\\') {
        return [];
    }

    $localAppData = trim((string)getenv('LOCALAPPDATA'));
    if ($localAppData === '') {
        return [];
    }

    $patterns = [
        $localAppData . DIRECTORY_SEPARATOR . 'Microsoft' . DIRECTORY_SEPARATOR . 'WindowsApps'
            . DIRECTORY_SEPARATOR . 'PythonSoftwareFoundation.Python.*',
        $localAppData . DIRECTORY_SEPARATOR . 'Programs' . DIRECTORY_SEPARATOR . 'Python'
            . DIRECTORY_SEPARATOR . 'Python*',
    ];

    $candidates = [];
    foreach ($patterns as $pattern) {
        $matched = glob(str_replace('\\', '/', $pattern));
        if ($matched === false) {
            continue;
        }
        rsort($matched, SORT_NATURAL);
        foreach ($matched as $path) {
            $path = rtrim(str_replace('/', DIRECTORY_SEPARATOR, $path), DIRECTORY_SEPARATOR)
                . DIRECTORY_SEPARATOR . 'python.exe';
            $candidates[] = $path;
        }
    }

    return $candidates;
}

function japanese_search_python_candidates(): array
{
    $env = trim((string)getenv('MIYABE_PYTHON_BIN'));
    $candidates = [];
    if ($env !== '') {
        $candidates[] = $env;
    }

    return array_values(array_unique(array_merge(
        $candidates,
        japanese_search_windows_python_candidates(),
        ['/usr/local/bin/python3', '/usr/bin/python3', 'python3', 'python']
    )));
}

function japanese_search_run_tokenizer(string $mode, string $text): ?array
{
    static $unavailable = false;
    if ($unavailable) {
        return null;
    }

    $script = japanese_search_python_script_path();
    if (!is_file($script)) {
        $unavailable = true;
        return null;
    }

    foreach (japanese_search_python_candidates() as $python) {
        $cmd = escapeshellarg($python)
            . ' '
            . escapeshellarg($script)
            . ' --mode '
            . escapeshellarg($mode)
            . ' --json';

        $descriptors = [
            0 => ['pipe', 'r'],
            1 => ['pipe', 'w'],
            2 => ['pipe', 'w'],
        ];
        $process = @proc_open($cmd, $descriptors, $pipes);
        if (!is_resource($process)) {
            continue;
        }

        fwrite($pipes[0], $text);
        fclose($pipes[0]);
        $stdout = stream_get_contents($pipes[1]);
        fclose($pipes[1]);
        $stderr = stream_get_contents($pipes[2]);
        fclose($pipes[2]);
        $exitCode = proc_close($process);

        if ($exitCode !== 0 || !is_string($stdout)) {
            continue;
        }

        $decoded = json_decode($stdout, true);
        if (is_array($decoded)) {
            return $decoded;
        }
    }

    $unavailable = true;
    return null;
}

function japanese_search_query_cache_path(string $query): string
{
    return data_path('background_tasks/japanese_search_query_cache/' . sha1('phrase-v5:' . $query) . '.json');
}

function japanese_search_query_cache_ttl_seconds(): int
{
    // 同じ検索語が短時間に集中しやすいので、Sudachi の前処理は跨リクエストでも再利用する。
    return 3600;
}

function japanese_search_document_terms_map(array $fields): array
{
    $normalized = [];
    foreach ($fields as $key => $value) {
        if (!is_scalar($key)) {
            continue;
        }
        $normalized[(string)$key] = is_scalar($value) ? (string)$value : '';
    }

    $payload = japanese_search_run_tokenizer(
        'fields',
        json_encode($normalized, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) ?: '{}'
    );
    if (!is_array($payload)) {
        return array_fill_keys(array_keys($normalized), '');
    }

    $terms = [];
    foreach ($normalized as $key => $_) {
        $terms[$key] = is_scalar($payload[$key] ?? null) ? trim((string)$payload[$key]) : '';
    }
    return $terms;
}

function japanese_search_fallback_terms(string $query): array
{
    $normalized = preg_replace('/["()]/u', ' ', $query);
    $parts = preg_split('/[\s　]+/u', (string)$normalized, -1, PREG_SPLIT_NO_EMPTY);
    if ($parts === false) {
        return [];
    }

    $terms = [];
    $skipNext = false;
    foreach ($parts as $part) {
        $token = trim($part);
        if ($token === '') {
            continue;
        }

        $upper = strtoupper($token);
        if ($skipNext) {
            $skipNext = false;
            continue;
        }
        if ($upper === 'NOT') {
            $skipNext = true;
            continue;
        }
        if (in_array($upper, ['AND', 'OR'], true) || str_starts_with($upper, 'NEAR')) {
            continue;
        }
        if (!preg_match('/[\p{Han}\p{Hiragana}\p{Katakana}]/u', $token) && strlen($token) < 2) {
            continue;
        }

        $terms[$token] = $token;
    }

    return array_values($terms);
}

function japanese_search_extract_quoted_phrases(string $query): array
{
    if (preg_match_all('/"[^"]*"|\(|\)|\bAND\b|\bOR\b|\bNOT\b|\bNEAR(?:\/\d+)?\b|[^\s()"]+/iu', $query, $matches) < 1) {
        return [];
    }

    $phrases = [];
    $hasDisjunction = false;
    $skipNextQuoted = false;
    foreach ($matches[0] ?? [] as $token) {
        $token = (string)$token;
        $upper = strtoupper($token);
        if ($upper === 'OR' || str_starts_with($upper, 'NEAR')) {
            $hasDisjunction = true;
        }
        if ($upper === 'NOT') {
            $skipNextQuoted = true;
            continue;
        }

        $isQuoted = str_starts_with($token, '"') && str_ends_with($token, '"') && strlen($token) >= 2;
        if ($isQuoted) {
            if (!$skipNextQuoted) {
                $phrase = substr($token, 1, -1);
                $text = trim(preg_replace('/\s+/u', ' ', $phrase) ?? $phrase);
                if ($text !== '') {
                    $phrases[$text] = $text;
                }
            }
            $skipNextQuoted = false;
            continue;
        }

        if ($token === '(' || $token === ')' || $upper === 'AND' || $upper === 'OR' || str_starts_with($upper, 'NEAR')) {
            continue;
        }
        $skipNextQuoted = false;
    }
    if ($hasDisjunction) {
        return [];
    }
    return array_values($phrases);
}

function japanese_search_exact_phrases_from_prepared(array $preparedQuery): array
{
    $phrases = [];
    foreach (($preparedQuery['exact_phrases'] ?? []) as $phrase) {
        if (!is_scalar($phrase)) {
            continue;
        }
        $text = trim(preg_replace('/\s+/u', ' ', (string)$phrase) ?? (string)$phrase);
        if ($text !== '') {
            $phrases[$text] = $text;
        }
    }
    return array_values($phrases);
}

function japanese_search_text_matches_exact_phrases(string $text, array $phrases): bool
{
    if ($phrases === []) {
        return true;
    }

    $normalized = preg_replace('/\s+/u', ' ', $text) ?? $text;
    foreach ($phrases as $phrase) {
        $needle = trim((string)$phrase);
        if ($needle === '') {
            continue;
        }
        if (mb_stripos($normalized, $needle, 0, 'UTF-8') === false) {
            return false;
        }
    }
    return true;
}

function japanese_search_sql_like_contains_pattern(string $text): string
{
    return '%' . strtr($text, [
        '\\' => '\\\\',
        '%' => '\\%',
        '_' => '\\_',
    ]) . '%';
}

function japanese_search_exact_phrase_like_clause(array $phrases, array $columns, string $paramPrefix = 'exact_phrase'): array
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
        $text = trim((string)$phrase);
        if ($text === '') {
            continue;
        }

        $param = ':' . $safePrefix . $index++;
        $columnConditions = [];
        foreach ($usableColumns as $column) {
            $columnConditions[] = $column . " LIKE {$param} ESCAPE '\\'";
        }
        $conditions[] = '(' . implode(' OR ', $columnConditions) . ')';
        $params[$param] = japanese_search_sql_like_contains_pattern($text);
    }

    return [
        'sql' => $conditions === [] ? '' : implode(' AND ', $conditions),
        'params' => $params,
    ];
}

function japanese_search_prepare_query(string $query): array
{
    static $cache = [];

    $normalized = trim($query);
    if (isset($cache[$normalized])) {
        return $cache[$normalized];
    }

    $payload = null;
    if ($normalized !== '') {
        $cachedPayload = read_json_cache_file(
            japanese_search_query_cache_path($normalized),
            japanese_search_query_cache_ttl_seconds()
        );
        if (is_array($cachedPayload)) {
            $cachedTokenizer = trim((string)($cachedPayload['tokenizer'] ?? ''));
            $cachedSchema = trim((string)($cachedPayload['query_cache_schema'] ?? ''));
            if ($cachedTokenizer !== 'fallback' && $cachedSchema === 'phrase-v5') {
                $payload = $cachedPayload;
            }
        }
    }

    if ($payload === null) {
        $payload = $normalized !== '' ? japanese_search_run_tokenizer('query', $normalized) : null;
    }
    $ftsQuery = trim((string)($payload['fts_query'] ?? ''));

    $quotedPhrases = japanese_search_extract_quoted_phrases($normalized);
    $exactPhrases = [];
    foreach (($payload['exact_phrases'] ?? []) as $phrase) {
        if (!is_scalar($phrase)) {
            continue;
        }
        $text = trim((string)$phrase);
        if ($text !== '') {
            $exactPhrases[$text] = $text;
        }
    }
    if ($exactPhrases === [] && ($payload === null || !array_key_exists('exact_phrases', $payload))) {
        foreach ($quotedPhrases as $phrase) {
            $exactPhrases[$phrase] = $phrase;
        }
    }

    $surfaceTerms = [];
    foreach (($payload['surface_terms'] ?? []) as $term) {
        if (!is_scalar($term)) {
            continue;
        }
        $text = trim((string)$term);
        if ($text !== '') {
            $surfaceTerms[$text] = $text;
        }
    }

    if ($surfaceTerms === []) {
        foreach (japanese_search_fallback_terms($normalized) as $term) {
            $surfaceTerms[$term] = $term;
        }
    }
    foreach ($quotedPhrases as $phrase) {
        $surfaceTerms[$phrase] = $phrase;
    }
    if ($exactPhrases !== []) {
        foreach (array_keys($surfaceTerms) as $term) {
            foreach ($exactPhrases as $phrase) {
                if ($term !== $phrase && mb_stripos($phrase, $term, 0, 'UTF-8') !== false) {
                    unset($surfaceTerms[$term]);
                    break;
                }
            }
        }
    }

    if ($ftsQuery === '' && $normalized !== '') {
        // Python が無い旧環境でも検索不能にはしない。image 更新後は SudachiPy 側へ寄る。
        $ftsQuery = $normalized;
    }

    $prepared = [
        'raw_query' => $normalized,
        'fts_query' => $ftsQuery,
        'highlight_terms' => array_values($surfaceTerms),
        'exact_phrases' => array_values($exactPhrases),
        'query_cache_schema' => 'phrase-v5',
        'tokenizer' => $payload === null ? 'fallback' : 'sudachi',
    ];

    if ($normalized !== '') {
        write_json_cache_file(japanese_search_query_cache_path($normalized), $prepared);
    }

    return $cache[$normalized] = $prepared;
}

function japanese_search_ordered_terms(array $terms): array
{
    $items = array_values(array_filter(array_map(
        static fn($value): string => trim((string)$value),
        $terms
    ), static fn(string $value): bool => $value !== ''));

    usort($items, static fn(string $a, string $b): int => mb_strlen($b, 'UTF-8') <=> mb_strlen($a, 'UTF-8'));
    return array_values(array_unique($items));
}

function japanese_search_highlight_excerpt(string $text, array $terms): string
{
    if ($text === '' || $terms === []) {
        return $text;
    }

    $pattern = '/(' . implode('|', array_map(
        static fn(string $term): string => preg_quote($term, '/'),
        japanese_search_ordered_terms($terms)
    )) . ')/iu';
    $parts = preg_split($pattern, $text, -1, PREG_SPLIT_DELIM_CAPTURE);
    if ($parts === false) {
        return $text;
    }

    $termMap = [];
    foreach ($terms as $term) {
        $termMap[mb_strtolower((string)$term, 'UTF-8')] = true;
    }

    $rendered = '';
    foreach ($parts as $part) {
        if ($part === '') {
            continue;
        }
        $matched = isset($termMap[mb_strtolower($part, 'UTF-8')]);
        $rendered .= $matched ? ('[[[' . $part . ']]]') : $part;
    }

    return $rendered;
}

function japanese_search_build_excerpt(string $text, array $terms, int $radius = 90, int $maxLength = 220): string
{
    $normalized = trim(preg_replace('/\s+/u', ' ', $text) ?? $text);
    if ($normalized === '') {
        return '';
    }

    $orderedTerms = japanese_search_ordered_terms($terms);
    $firstPos = null;
    $firstLength = 0;
    foreach ($orderedTerms as $term) {
        $pos = mb_stripos($normalized, $term, 0, 'UTF-8');
        if ($pos === false) {
            continue;
        }
        if ($firstPos === null || $pos < $firstPos) {
            $firstPos = $pos;
            $firstLength = mb_strlen($term, 'UTF-8');
        }
    }

    if ($firstPos === null) {
        $excerpt = mb_substr($normalized, 0, $maxLength, 'UTF-8');
        if (mb_strlen($normalized, 'UTF-8') > $maxLength) {
            $excerpt .= '…';
        }
        return $excerpt;
    }

    $start = max(0, $firstPos - $radius);
    $end = min(
        mb_strlen($normalized, 'UTF-8'),
        max($firstPos + $firstLength + $radius, $start + $maxLength)
    );
    $excerpt = mb_substr($normalized, $start, $end - $start, 'UTF-8');
    if ($start > 0) {
        $excerpt = '…' . ltrim($excerpt);
    }
    if ($end < mb_strlen($normalized, 'UTF-8')) {
        $excerpt = rtrim($excerpt) . '…';
    }

    return japanese_search_highlight_excerpt($excerpt, $orderedTerms);
}
