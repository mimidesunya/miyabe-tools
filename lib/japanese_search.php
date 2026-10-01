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
    return data_path('background_tasks/japanese_search_query_cache/' . sha1('phrase-v7:' . $query) . '.json');
}

function japanese_search_query_cache_ttl_seconds(): int
{
    // 同じ検索語が短時間に集中しやすいので、Sudachi の前処理は跨リクエストでも再利用する。
    return 3600;
}

function japanese_search_query_cache_dir(): string
{
    return dirname(japanese_search_query_cache_path(''));
}

function japanese_search_query_cache_max_age_seconds(): int
{
    // TTL を過ぎた分は、同じ検索語がもう一度来ない限り読まれない。読まれないまま積み上がる
    // ので、1 日置いたものは捨てる。捨てても同じ語で検索されれば作り直すだけで、失うものはない。
    // 実際、本番では 3,880 件のうち有効なのは 18 件で、76% は 7 日以上前のものだった。
    return 24 * 60 * 60;
}

/**
 * 読まれなくなったクエリキャッシュを捨てる。消した件数を返す。
 *
 * ファイル名は検索語とスキーマの hash なので、スキーマを上げると前のものは二度と
 * 読まれない。TTL の判定は読むときにしか働かず、ファイル自体は残り続ける。
 */
function japanese_search_prune_query_cache(int $maxDeletions = 500): int
{
    $dir = japanese_search_query_cache_dir();
    $handle = @opendir($dir);
    if ($handle === false) {
        return 0;
    }

    $threshold = time() - japanese_search_query_cache_max_age_seconds();
    $deleted = 0;
    // 全部を配列に読み込むと、件数が増えたときに重くなる。1 件ずつ見て上限で打ち切る。
    while ($deleted < $maxDeletions && ($entry = readdir($handle)) !== false) {
        if (!str_ends_with($entry, '.json')) {
            continue;
        }
        $path = $dir . DIRECTORY_SEPARATOR . $entry;
        $mtime = @filemtime($path);
        if ($mtime === false || $mtime > $threshold) {
            continue;
        }
        if (@unlink($path)) {
            $deleted++;
        }
    }
    closedir($handle);
    return $deleted;
}

function japanese_search_maybe_prune_query_cache(): void
{
    // 検索のたびにディレクトリを走査するほどのものではないので、まれにだけ掃除する。
    try {
        if (random_int(1, 200) !== 1) {
            return;
        }
    } catch (Throwable) {
        return;
    }
    japanese_search_prune_query_cache();
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
    $query = preg_replace('/[\s　]+/u', ' ', $query) ?? $query;
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
                $text = trim(preg_replace('/[\s　]+/u', ' ', $phrase) ?? $phrase);
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
        $text = trim(preg_replace('/[\s　]+/u', ' ', (string)$phrase) ?? (string)$phrase);
        if ($text !== '') {
            $phrases[$text] = $text;
        }
    }
    return array_values($phrases);
}

/**
 * 演算子を含まない、素の AND 検索かどうか。
 *
 * OR や NOT が混ざったクエリでは「語が近い＝関連が強い」が成り立たないので、
 * 近接優先はこれが true のときだけ効かせる。simple_query_string の演算子のうち
 * +/- は語の先頭に置かれたときだけ意味を持つので、その位置だけを見る。
 */
function japanese_search_query_is_plain_and(string $query): bool
{
    $query = trim($query);
    if ($query === '') {
        return false;
    }
    if (preg_match('/["()|]/u', $query) === 1) {
        return false;
    }
    if (preg_match('/(?:^|[\s　])[+\-]/u', $query) === 1) {
        return false;
    }
    return preg_match('/\b(?:AND|OR|NOT|NEAR(?:\/\d+)?)\b/iu', $query) !== 1;
}

function japanese_search_prepare_query(string $query): array
{
    static $cache = [];

    $normalized = trim(preg_replace('/[\s　]+/u', ' ', $query) ?? $query);
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
            if ($cachedTokenizer !== 'fallback' && $cachedSchema === 'phrase-v7') {
                $payload = $cachedPayload;
                // 保存してあるのは下で組み立てた後の形なので、語は highlight_terms の名前で入って
                // いる。tokenizer の生の返り値と同じ名前に戻しておかないと、下の surface_terms が
                // 空になり、Sudachi で切った語を捨てて空白区切りへ落ちる。そうなると
                // 「ふるさと納税」のように Sudachi が割る複合語が body_terms と噛み合わなくなる。
                if (!array_key_exists('surface_terms', $payload)
                    && array_key_exists('highlight_terms', $payload)) {
                    $payload['surface_terms'] = $payload['highlight_terms'];
                }
            }
        }
    }

    if ($payload === null) {
        $payload = $normalized !== '' ? japanese_search_run_tokenizer('query', $normalized) : null;
    }

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

    $prepared = [
        'raw_query' => $normalized,
        'highlight_terms' => array_values($surfaceTerms),
        'exact_phrases' => array_values($exactPhrases),
        'query_cache_schema' => 'phrase-v7',
        'tokenizer' => $payload === null ? 'fallback' : 'sudachi',
    ];

    if ($normalized !== '') {
        write_json_cache_file(japanese_search_query_cache_path($normalized), $prepared);
        japanese_search_maybe_prune_query_cache();
    }

    return $cache[$normalized] = $prepared;
}
