<?php
declare(strict_types=1);

require_once __DIR__ . DIRECTORY_SEPARATOR . 'municipalities.php';

// Python 側の SudachiPy を呼び、PHP の検索リクエストでも同じ分かち書きを使う。

function japanese_search_python_script_path(): string
{
    return __DIR__ . DIRECTORY_SEPARATOR . 'python' . DIRECTORY_SEPARATOR . 'japanese_search_tokenizer.py';
}

function japanese_search_python_candidates(): array
{
    $env = trim((string)getenv('MIYABE_PYTHON_BIN'));
    $candidates = [];
    if ($env !== '') {
        $candidates[] = $env;
    }

    return array_merge(
        $candidates,
        ['/usr/local/bin/python3', '/usr/bin/python3', 'python3', 'python']
    );
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
    return data_path('background_tasks/japanese_search_query_cache/' . sha1($query) . '.json');
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

function japanese_search_prepare_query(string $query): array
{
    static $cache = [];

    $normalized = trim($query);
    if (isset($cache[$normalized])) {
        return $cache[$normalized];
    }

    if ($normalized !== '') {
        $cachedPayload = read_json_cache_file(
            japanese_search_query_cache_path($normalized),
            japanese_search_query_cache_ttl_seconds()
        );
        if (is_array($cachedPayload)) {
            return $cache[$normalized] = $cachedPayload;
        }
    }

    $payload = $normalized !== '' ? japanese_search_run_tokenizer('query', $normalized) : null;
    $ftsQuery = trim((string)($payload['fts_query'] ?? ''));
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

    if ($ftsQuery === '' && $normalized !== '') {
        // Python が無い旧環境でも検索不能にはしない。image 更新後は SudachiPy 側へ寄る。
        $ftsQuery = $normalized;
    }

    $prepared = [
        'raw_query' => $normalized,
        'fts_query' => $ftsQuery,
        'highlight_terms' => array_values($surfaceTerms),
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
