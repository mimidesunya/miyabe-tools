<?php
declare(strict_types=1);

// 自治体マスタ、config.json、実データ配置を突き合わせて公開用の自治体カタログを組み立てる。

function app_utc_timezone(): DateTimeZone
{
    static $timezone = null;
    if ($timezone === null) {
        $timezone = new DateTimeZone('UTC');
    }
    return $timezone;
}

function app_tokyo_timezone(): DateTimeZone
{
    static $timezone = null;
    if ($timezone === null) {
        $timezone = new DateTimeZone('Asia/Tokyo');
    }
    return $timezone;
}

function app_parse_timestamp_utc(?string $value): ?DateTimeImmutable
{
    $value = trim((string)$value);
    if ($value === '') {
        return null;
    }

    if (preg_match('/(?:Z|[+-]\d{2}:\d{2}|[+-]\d{4})$/', $value) === 1) {
        try {
            return new DateTimeImmutable($value);
        } catch (Throwable) {
            return null;
        }
    }

    $normalized = str_replace('T', ' ', $value);
    foreach (['Y-m-d H:i:s', 'Y-m-d H:i', 'Y-m-d'] as $format) {
        $parsed = DateTimeImmutable::createFromFormat('!' . $format, $normalized, app_utc_timezone());
        if ($parsed instanceof DateTimeImmutable) {
            return $parsed;
        }
    }

    try {
        return new DateTimeImmutable($value, app_utc_timezone());
    } catch (Throwable) {
        return null;
    }
}

function app_parse_timestamp_utc_unix(?string $value): ?int
{
    $parsed = app_parse_timestamp_utc($value);
    return $parsed instanceof DateTimeImmutable ? $parsed->getTimestamp() : null;
}

function app_format_tokyo_datetime(?string $value, string $format = 'Y-m-d H:i:s'): string
{
    $parsed = app_parse_timestamp_utc($value);
    if (!$parsed instanceof DateTimeImmutable) {
        return trim((string)$value);
    }
    return $parsed->setTimezone(app_tokyo_timezone())->format($format);
}

function app_now_tokyo(string $format = 'Y-m-d H:i:s'): string
{
    return (new DateTimeImmutable('now', app_tokyo_timezone()))->format($format);
}

function project_root_path(): string
{
    return dirname(__DIR__);
}

function data_root_path(): string
{
    return project_root_path() . DIRECTORY_SEPARATOR . 'data';
}

function work_root_path(): string
{
    return project_root_path() . DIRECTORY_SEPARATOR . 'work';
}

// 本番では config.json、ローカル開発時は config.example.json も読めるようにする。
function load_config(): array
{
    static $config = null;
    if ($config !== null) {
        return $config;
    }

    $configFile = data_root_path() . DIRECTORY_SEPARATOR . 'config.json';
    $exampleFile = data_root_path() . DIRECTORY_SEPARATOR . 'config.example.json';
    $source = is_file($configFile) ? $configFile : (is_file($exampleFile) ? $exampleFile : null);
    if ($source === null) {
        $config = [];
        return $config;
    }

    $decoded = json_decode((string)file_get_contents($source), true);
    $config = is_array($decoded) ? $decoded : [];
    return $config;
}

function normalize_data_relative_path(string $relative): string
{
    return trim(str_replace(['/', '\\'], DIRECTORY_SEPARATOR, $relative), DIRECTORY_SEPARATOR);
}

function municipality_catalog_cache_path(): string
{
    return data_path('background_tasks/municipality_catalog_cache.json');
}

function municipality_catalog_cache_ttl_seconds(): int
{
    // 自治体マスタや保存先は deploy / バッチのタイミングでしか大きく変わらない。
    // 毎リクエストで全自治体を再判定しないよう、ここは長めに保持する。
    return 300;
}

function municipality_catalog_cache_is_compatible(array $cached): bool
{
    foreach ($cached as $entry) {
        if (!is_array($entry)) {
            return false;
        }
        $publicSlug = trim((string)($entry['public_slug'] ?? ''));
        if ($publicSlug === '') {
            return false;
        }
    }
    return true;
}

function data_path(string $relative): string
{
    $normalized = normalize_data_relative_path($relative);
    if ($normalized === '') {
        return data_root_path();
    }
    return data_root_path() . DIRECTORY_SEPARATOR . $normalized;
}

function work_path(string $relative): string
{
    $normalized = normalize_data_relative_path($relative);
    if ($normalized === '') {
        return work_root_path();
    }
    return work_root_path() . DIRECTORY_SEPARATOR . $normalized;
}

function data_public_url(string $relative): string
{
    $normalized = trim(str_replace('\\', '/', normalize_data_relative_path($relative)), '/');
    return '/data/' . $normalized;
}

function read_json_cache_file(string $path, int $ttlSeconds = 0): ?array
{
    if (!is_file($path)) {
        return null;
    }

    if ($ttlSeconds > 0) {
        $ageSeconds = time() - (int)@filemtime($path);
        if ($ageSeconds < 0 || $ageSeconds > $ttlSeconds) {
            return null;
        }
    }

    $decoded = json_decode((string)@file_get_contents($path), true);
    return is_array($decoded) ? $decoded : null;
}

function write_json_cache_file(string $path, array $payload): void
{
    $encoded = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    if (!is_string($encoded)) {
        return;
    }

    $dir = dirname($path);
    if (!is_dir($dir)) {
        @mkdir($dir, 0755, true);
    }

    $tmpPath = $path . '.' . bin2hex(random_bytes(4)) . '.tmp';
    if (@file_put_contents($tmpPath, $encoded, LOCK_EX) === false) {
        @unlink($tmpPath);
        return;
    }
    @rename($tmpPath, $path);
}

function feature_enabled_value(mixed $explicit, bool $detected): bool
{
    if (is_bool($explicit)) {
        return $explicit;
    }
    if (is_int($explicit) || is_float($explicit)) {
        return (bool)$explicit;
    }
    if (is_string($explicit)) {
        $value = strtolower(trim($explicit));
        if (in_array($value, ['1', 'true', 'yes', 'on'], true)) {
            return true;
        }
        if (in_array($value, ['0', 'false', 'no', 'off'], true)) {
            return false;
        }
    }
    return $detected;
}

// データ有無の判定には件数より速い存在確認で十分なので、最初の一致で打ち切る。
function directory_contains_matching_file(string $path, array $patterns = []): bool
{
    static $cache = [];
    $cacheKey = $path . "\n" . implode("\n", $patterns);
    if (array_key_exists($cacheKey, $cache)) {
        return $cache[$cacheKey];
    }

    if (!is_dir($path)) {
        $cache[$cacheKey] = false;
        return false;
    }

    try {
        $iterator = new RecursiveIteratorIterator(
            new RecursiveDirectoryIterator($path, FilesystemIterator::SKIP_DOTS)
        );
        foreach ($iterator as $entry) {
            if (!$entry instanceof SplFileInfo || !$entry->isFile()) {
                continue;
            }
            $filePath = $entry->getPathname();
            if ($patterns === []) {
                $cache[$cacheKey] = true;
                return true;
            }
            foreach ($patterns as $pattern) {
                if (@preg_match($pattern, $filePath) === 1) {
                    $cache[$cacheKey] = true;
                    return true;
                }
            }
        }
    } catch (Throwable) {
        $cache[$cacheKey] = false;
        return false;
    }

    $cache[$cacheKey] = false;
    return false;
}

function json_array_has_items_auto(string $path): bool
{
    static $cache = [];
    if (array_key_exists($path, $cache)) {
        return $cache[$path];
    }

    $candidates = [$path];
    if (str_ends_with(strtolower($path), '.gz')) {
        $candidates[] = substr($path, 0, -3);
    } else {
        $candidates[] = $path . '.gz';
    }

    foreach ($candidates as $candidate) {
        if (!is_file($candidate)) {
            continue;
        }

        $raw = @file_get_contents($candidate);
        if (!is_string($raw)) {
            continue;
        }
        if (str_ends_with(strtolower($candidate), '.gz')) {
            $decoded = @gzdecode($raw);
            if (!is_string($decoded)) {
                continue;
            }
            $raw = $decoded;
        }

        $decoded = json_decode($raw, true);
        $cache[$path] = is_array($decoded) && $decoded !== [];
        return $cache[$path];
    }

    $cache[$path] = false;
    return false;
}

function sqlite_table_max_id(string $dbPath, string $table): int
{
    static $cache = [];
    $cacheKey = $dbPath . '|' . $table;
    if (array_key_exists($cacheKey, $cache)) {
        return $cache[$cacheKey];
    }

    if (!is_file($dbPath) || !class_exists(PDO::class) || !in_array('sqlite', PDO::getAvailableDrivers(), true)) {
        $cache[$cacheKey] = 0;
        return 0;
    }
    if (!preg_match('/^[a-z_][a-z0-9_]*$/i', $table)) {
        $cache[$cacheKey] = 0;
        return 0;
    }

    try {
        // 検索用インデックス DB は再構築前提で、id は密な連番として保てる。
        // 公開判定や概算件数では COUNT(*) より軽い MAX(id) を優先して使う。
        $pdo = new PDO('sqlite:' . $dbPath);
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $value = $pdo->query('SELECT COALESCE(MAX(id), 0) FROM ' . $table)->fetchColumn();
        $cache[$cacheKey] = max(0, (int)$value);
    } catch (Throwable) {
        // 一時的な lock / open 失敗を 0 件として固定すると、
        // 同一リクエスト内の self-heal まで潰してしまうため失敗結果は cache しない。
        return 0;
    }

    return $cache[$cacheKey];
}

function sqlite_table_has_rows(string $dbPath, string $table): bool
{
    // 空の SQLite ファイルや schema だけの DB は「公開データあり」に含めない。
    return sqlite_table_max_id($dbPath, $table) > 0;
}

function municipality_feature_live_has_data(string $feature, array $featureConfig): bool
{
    switch ($feature) {
        case 'boards':
            $dbPath = trim((string)($featureConfig['db_path'] ?? ''));
            return $dbPath !== '' && is_file($dbPath);

        case 'reiki':
            $dbPath = trim((string)($featureConfig['db_path'] ?? ''));
            if ($dbPath !== '' && sqlite_table_has_rows($dbPath, 'ordinances')) {
                return true;
            }
            $htmlDir = trim((string)($featureConfig['clean_html_dir'] ?? ''));
            return $htmlDir !== '' && directory_contains_matching_file(
                $htmlDir,
                ['/\.(?:html|htm)(?:\.gz)?$/i']
            );

        case 'gijiroku':
            $dbPath = trim((string)($featureConfig['db_path'] ?? ''));
            if ($dbPath !== '' && sqlite_table_has_rows($dbPath, 'minutes')) {
                return true;
            }
            $indexJsonPath = trim((string)($featureConfig['index_json_path'] ?? ''));
            return $indexJsonPath !== '' && json_array_has_items_auto($indexJsonPath);
    }

    return !empty($featureConfig['has_data']);
}

function sanitize_slug_token(string $value): string
{
    $token = strtolower(trim($value));
    $token = preg_replace('/[^a-z0-9-]+/', '-', $token) ?? '';
    $token = trim($token, '-');
    return preg_replace('/-{2,}/', '-', $token) ?? '';
}

function normalize_slug_alias_value(string $value): string
{
    $value = trim($value);
    if ($value === '') {
        return '';
    }
    if (function_exists('mb_strtolower')) {
        return mb_strtolower($value, 'UTF-8');
    }
    return strtolower($value);
}

function normalize_municipality_entry(string $slug, array $entry): array
{
    $code = trim((string)($entry['code'] ?? ''));
    $masterEntry = $code !== '' ? (load_municipality_master_index()[$code] ?? []) : [];
    // 保存先も公開 URL も canonical な code-name_romaji slug をそのまま使う。
    $name = trim((string)($entry['name'] ?? $entry['label'] ?? $masterEntry['name'] ?? $slug));
    if ($name === '') {
        $name = $slug;
    }
    $fullName = trim((string)($entry['full_name'] ?? $masterEntry['full_name'] ?? ''));
    if ($fullName === '') {
        $fullName = $name;
    }
    $nameKana = trim((string)($entry['name_kana'] ?? $masterEntry['name_kana'] ?? ''));
    $nameRomaji = trim((string)($entry['name_romaji'] ?? $masterEntry['name_romaji'] ?? ''));
    $publicSlug = $slug;

    $boardsConfig = is_array($entry['boards'] ?? null) ? $entry['boards'] : [];
    $boardsDbRelative = normalize_data_relative_path(trim((string)($boardsConfig['db_path'] ?? "boards/{$slug}/boards.sqlite")));
    $boardsTasksDbRelative = normalize_data_relative_path(trim((string)($boardsConfig['tasks_db_path'] ?? "boards/{$slug}/tasks.sqlite")));
    $boardsDbPath = data_path($boardsDbRelative);
    $boardsTasksDbPath = data_path($boardsTasksDbRelative);
    $boardsDetected = is_file($boardsDbPath);
    $boardsEnabled = feature_enabled_value($boardsConfig['enabled'] ?? null, $boardsDetected);

    $reikiConfig = is_array($entry['reiki'] ?? null) ? $entry['reiki'] : [];
    $reikiSkipDetection = !empty($reikiConfig['skip_detection']);
    $reikiSourceRelative = normalize_data_relative_path(trim((string)($reikiConfig['source_dir'] ?? $reikiConfig['data_dir'] ?? "reiki/{$slug}/source")));
    $reikiCleanHtmlRelative = normalize_data_relative_path(trim((string)($reikiConfig['clean_html_dir'] ?? "reiki/{$slug}/html")));
    $reikiClassificationRelative = normalize_data_relative_path(trim((string)($reikiConfig['classification_dir'] ?? "reiki/{$slug}/json")));
    $reikiImageRelative = normalize_data_relative_path(trim((string)($reikiConfig['image_dir'] ?? "reiki/{$slug}/images")));
    $reikiMarkdownRelative = normalize_data_relative_path(trim((string)($reikiConfig['markdown_dir'] ?? "reiki/{$slug}/markdown")));
    $reikiDbRelative = normalize_data_relative_path(trim((string)($reikiConfig['db_path'] ?? "reiki/{$slug}/ordinances.sqlite")));
    $reikiDbPath = data_path($reikiDbRelative);
    $reikiSourcePath = work_path($reikiSourceRelative);
    $reikiCleanHtmlPath = data_path($reikiCleanHtmlRelative);
    $reikiClassificationPath = data_path($reikiClassificationRelative);
    $reikiImagePath = data_path($reikiImageRelative);
    $reikiMarkdownPath = work_path($reikiMarkdownRelative);
    if ($reikiSkipDetection) {
        $reikiDetected = false;
        $reikiEnabled = false;
    } else {
        $reikiDetected = sqlite_table_has_rows($reikiDbPath, 'ordinances')
            || directory_contains_matching_file(
                $reikiCleanHtmlPath,
                ['/\.(?:html|htm)(?:\.gz)?$/i']
            );
        $reikiEnabled = $reikiDetected || feature_enabled_value($reikiConfig['enabled'] ?? null, false);
    }

    $gijirokuConfig = is_array($entry['gijiroku'] ?? null) ? $entry['gijiroku'] : [];
    $gijirokuSkipDetection = !empty($gijirokuConfig['skip_detection']);
    $gijirokuDataRelative = normalize_data_relative_path(trim((string)($gijirokuConfig['data_dir'] ?? "gijiroku/{$slug}")));
    $gijirokuDownloadsRelative = normalize_data_relative_path(trim((string)($gijirokuConfig['downloads_dir'] ?? "gijiroku/{$slug}/downloads")));
    $gijirokuIndexJsonRelative = normalize_data_relative_path(trim((string)($gijirokuConfig['index_json_path'] ?? "gijiroku/{$slug}/meetings_index.json")));
    $gijirokuDbRelative = normalize_data_relative_path(trim((string)($gijirokuConfig['db_path'] ?? "gijiroku/{$slug}/minutes.sqlite")));
    $assemblyName = trim((string)($gijirokuConfig['assembly_name'] ?? "{$name}議会"));
    $gijirokuDataPath = data_path($gijirokuDataRelative);
    $gijirokuWorkPath = work_path($gijirokuDataRelative);
    $gijirokuDownloadsPath = work_path($gijirokuDownloadsRelative);
    $gijirokuIndexJsonPath = work_path($gijirokuIndexJsonRelative);
    $gijirokuDbPath = data_path($gijirokuDbRelative);
    if ($gijirokuSkipDetection) {
        $gijirokuDetected = false;
        $gijirokuEnabled = false;
    } else {
        $gijirokuDetected = sqlite_table_has_rows($gijirokuDbPath, 'minutes')
            || json_array_has_items_auto($gijirokuIndexJsonPath);
        $gijirokuEnabled = $gijirokuDetected || feature_enabled_value($gijirokuConfig['enabled'] ?? null, false);
    }

    return [
        'slug' => $slug,
        'public_slug' => $publicSlug,
        'code' => $code,
        'name' => $name,
        'name_kana' => $nameKana,
        'full_name' => $fullName,
        'name_romaji' => $nameRomaji,
        'boards' => [
            'enabled' => $boardsEnabled,
            'has_data' => $boardsDetected,
            'title' => trim((string)($boardsConfig['title'] ?? "{$name} ポスター掲示場")),
            'description' => trim((string)($boardsConfig['description'] ?? '選挙ポスター掲示場の位置確認と作業状況共有')),
            'url' => "/boards/{$publicSlug}/",
            'list_url' => '/boards/list.php?slug=' . rawurlencode($publicSlug),
            'users_url' => '/boards/users.php?slug=' . rawurlencode($publicSlug),
            'db_path_rel' => normalize_data_relative_path($boardsDbRelative),
            'tasks_db_path_rel' => normalize_data_relative_path($boardsTasksDbRelative),
            'db_path' => $boardsDbPath,
            'tasks_db_path' => $boardsTasksDbPath,
        ],
        'reiki' => [
            'enabled' => $reikiEnabled,
            'has_data' => $reikiDetected,
            'title' => trim((string)($reikiConfig['title'] ?? ($reikiDefaults['title'] ?? "{$name}例規集 AI評価ビューア"))),
            'description' => trim((string)($reikiConfig['description'] ?? '例規集の検索とAI評価結果の閲覧')),
            'url' => '/reiki/?slug=' . rawurlencode($publicSlug),
            'source_dir_rel' => normalize_data_relative_path($reikiSourceRelative),
            'clean_html_dir_rel' => normalize_data_relative_path($reikiCleanHtmlRelative),
            'classification_dir_rel' => normalize_data_relative_path($reikiClassificationRelative),
            'image_dir_rel' => normalize_data_relative_path($reikiImageRelative),
            'markdown_dir_rel' => normalize_data_relative_path($reikiMarkdownRelative),
            'db_path_rel' => normalize_data_relative_path($reikiDbRelative),
            'source_dir' => $reikiSourcePath,
            'clean_html_dir' => $reikiCleanHtmlPath,
            'classification_dir' => $reikiClassificationPath,
            'image_dir' => $reikiImagePath,
            'image_url' => data_public_url($reikiImageRelative),
            'markdown_dir' => $reikiMarkdownPath,
            'db_path' => $reikiDbPath,
        ],
        'gijiroku' => [
            'enabled' => $gijirokuEnabled,
            'has_data' => $gijirokuDetected,
            'title' => trim((string)($gijirokuConfig['title'] ?? ($gijirokuDefaults['title'] ?? "{$assemblyName} 会議録 全文検索"))),
            'description' => trim((string)($gijirokuConfig['description'] ?? "{$assemblyName}の会議録を全文検索")),
            'assembly_name' => $assemblyName,
            'url' => '/gijiroku/?slug=' . rawurlencode($publicSlug),
            'data_dir_rel' => normalize_data_relative_path($gijirokuDataRelative),
            'downloads_dir_rel' => normalize_data_relative_path($gijirokuDownloadsRelative),
            'index_json_path_rel' => normalize_data_relative_path($gijirokuIndexJsonRelative),
            'db_path_rel' => normalize_data_relative_path($gijirokuDbRelative),
            'data_dir' => $gijirokuDataPath,
            'work_dir' => $gijirokuWorkPath,
            'downloads_dir' => $gijirokuDownloadsPath,
            'index_json_path' => $gijirokuIndexJsonPath,
            'db_path' => $gijirokuDbPath,
        ],
    ];
}

function load_delimited_rows(string $path, string $delimiter = "\t"): array
{
    if (!is_file($path)) {
        return [];
    }

    $handle = fopen($path, 'rb');
    if ($handle === false) {
        return [];
    }

    $header = fgetcsv($handle, 0, $delimiter);
    if (!is_array($header)) {
        fclose($handle);
        return [];
    }

    $header = array_map(static function ($value): string {
        return trim((string)$value);
    }, $header);
    if ($header !== []) {
        $header[0] = preg_replace('/^\xEF\xBB\xBF/u', '', $header[0]) ?? $header[0];
    }

    $rows = [];
    while (($row = fgetcsv($handle, 0, $delimiter)) !== false) {
        if (!is_array($row)) {
            continue;
        }
        $assoc = [];
        foreach ($header as $index => $column) {
            if ($column === '') {
                continue;
            }
            $assoc[$column] = trim((string)($row[$index] ?? ''));
        }
        if ($assoc !== []) {
            $rows[] = $assoc;
        }
    }

    fclose($handle);
    return $rows;
}

function load_municipality_master_index(): array
{
    static $index = null;
    if ($index !== null) {
        return $index;
    }

    $index = [];
    foreach (load_delimited_rows(data_path('municipalities/municipality_master.tsv')) as $row) {
        $code = trim((string)($row['jis_code'] ?? ''));
        if ($code === '') {
            continue;
        }
        $index[$code] = [
            'entity_type' => trim((string)($row['entity_type'] ?? '')),
            'name' => trim((string)($row['name'] ?? '')),
            'name_kana' => trim((string)($row['name_kana'] ?? '')),
            'full_name' => trim((string)($row['full_name'] ?? '')),
            'name_romaji' => trim((string)($row['name_romaji'] ?? '')),
        ];
    }
    return $index;
}

function load_system_url_index(string $relativePath): array
{
    static $cache = [];
    $key = normalize_data_relative_path($relativePath);
    if (array_key_exists($key, $cache)) {
        return $cache[$key];
    }

    $index = [];
    $runtimeRelativePath = preg_replace('/^municipalities\//', 'municipalities/', normalize_data_relative_path($relativePath)) ?? normalize_data_relative_path($relativePath);
    foreach (load_delimited_rows(data_path($runtimeRelativePath)) as $row) {
        $code = trim((string)($row['jis_code'] ?? ''));
        if ($code === '') {
            continue;
        }
        $index[$code] = [
            'url' => trim((string)($row['url'] ?? '')),
            'system_type' => trim((string)($row['system_type'] ?? '')),
        ];
    }
    $cache[$key] = $index;
    return $index;
}

function municipality_public_slug(string $slug): string
{
    // 公開 URL と保存先 slug は統一済みなので、ここは alias 解決だけ担う。
    $resolved = resolve_municipality_slug($slug);
    return $resolved !== '' ? $resolved : trim($slug);
}

function implicit_municipality_slug(string $code, array $masterEntry = []): string
{
    $normalizedCode = preg_replace('/[^0-9]/', '', $code) ?? '';
    if ($normalizedCode === '') {
        $normalizedCode = '00000';
    }

    $token = sanitize_slug_token((string)($masterEntry['name_romaji'] ?? ''));
    if ($token === '') {
        $token = 'municipality';
    }
    return $normalizedCode . '-' . $token;
}

function municipality_catalog(): array
{
    static $catalog = null;
    if ($catalog !== null) {
        return $catalog;
    }

    $cachePath = municipality_catalog_cache_path();
    $ttlSeconds = municipality_catalog_cache_ttl_seconds();
    $cached = read_json_cache_file($cachePath, $ttlSeconds);
    if (is_array($cached) && municipality_catalog_cache_is_compatible($cached)) {
        $catalog = $cached;
        return $catalog;
    }

    // 公開カタログは全国マスタと system URL 一覧だけから復元する。
    // これで config に自治体一覧を持たなくても、公開対象候補を毎回組み立てられる。
    $catalog = [];
    $masterIndex = load_municipality_master_index();
    $minutesIndex = load_system_url_index('municipalities/assembly_minutes_system_urls.tsv');
    $reikiIndex = load_system_url_index('municipalities/reiki_system_urls.tsv');
    $allCodes = [];
    foreach ([$masterIndex, $minutesIndex, $reikiIndex] as $index) {
        foreach ($index as $code => $_entry) {
            $normalizedCode = trim((string)$code);
            if ($normalizedCode === '') {
                continue;
            }
            $allCodes[] = $normalizedCode;
        }
    }

    foreach (array_values(array_unique($allCodes)) as $code) {
        $masterEntry = $masterIndex[$code] ?? [];
        $slug = implicit_municipality_slug($code, $masterEntry);
        if (isset($catalog[$slug])) {
            continue;
        }

        $entry = [
            'code' => $code,
            'name' => trim((string)($masterEntry['name'] ?? '')) ?: $slug,
            'name_kana' => trim((string)($masterEntry['name_kana'] ?? '')),
            'full_name' => trim((string)($masterEntry['full_name'] ?? '')),
            'name_romaji' => trim((string)($masterEntry['name_romaji'] ?? '')),
        ];
        if (!isset($minutesIndex[$code])) {
            $entry['gijiroku'] = ['enabled' => false, 'skip_detection' => true];
        }
        if (!isset($reikiIndex[$code])) {
            $entry['reiki'] = ['enabled' => false, 'skip_detection' => true];
        }

        $catalog[$slug] = normalize_municipality_entry($slug, $entry);
    }

    uasort($catalog, function (array $a, array $b): int {
        $ca = (string)($a['code'] ?? '');
        $cb = (string)($b['code'] ?? '');
        if ($ca === '' && $cb === '') return 0;
        if ($ca === '') return 1;
        if ($cb === '') return -1;
        return strcmp($ca, $cb);
    });

    write_json_cache_file($cachePath, $catalog);

    return $catalog;
}

function municipality_slug_alias_index(): array
{
    static $aliasIndex = null;
    if ($aliasIndex !== null) {
        return $aliasIndex;
    }

    $aliasIndex = [];
    foreach (municipality_catalog() as $slug => $entry) {
        // 内部 slug / 公開 slug / code / romaji / 日本語名のどれから来ても同じ自治体へ寄せる。
        $aliases = [$slug];
        $code = trim((string)($entry['code'] ?? ''));
        $name = trim((string)($entry['name'] ?? ''));
        $fullName = trim((string)($entry['full_name'] ?? ''));
        $nameRomaji = sanitize_slug_token((string)($entry['name_romaji'] ?? ''));

        if ($code !== '') {
            $aliases[] = $code;
        }
        if (preg_match('/^\d{5}-(.+)$/', $slug, $matches) === 1) {
            $aliases[] = trim((string)($matches[1] ?? ''));
        }
        if ($code !== '' && $nameRomaji !== '') {
            $aliases[] = $code . '-' . $nameRomaji;
        }
        if ($nameRomaji !== '') {
            $aliases[] = $nameRomaji;
        }
        if ($name !== '') {
            $aliases[] = $name;
        }
        if ($fullName !== '') {
            $aliases[] = $fullName;
        }

        foreach ($aliases as $alias) {
            $normalizedAlias = normalize_slug_alias_value((string)$alias);
            if ($normalizedAlias === '' || isset($aliasIndex[$normalizedAlias])) {
                continue;
            }
            $aliasIndex[$normalizedAlias] = $slug;
        }
    }

    return $aliasIndex;
}

function resolve_municipality_slug(?string $input): string
{
    $candidate = trim((string)$input);
    if ($candidate === '') {
        return '';
    }

    $registry = municipality_catalog();
    if (isset($registry[$candidate])) {
        return $candidate;
    }

    $aliasIndex = municipality_slug_alias_index();
    $normalizedCandidate = normalize_slug_alias_value($candidate);
    if ($normalizedCandidate !== '' && isset($aliasIndex[$normalizedCandidate])) {
        return $aliasIndex[$normalizedCandidate];
    }

    return '';
}

function requested_canonical_slug(?string $input = null): ?string
{
    $requested = trim((string)($input ?? ($_GET['slug'] ?? '')));
    if ($requested === '') {
        return null;
    }

    $resolved = resolve_municipality_slug($requested);
    if ($resolved === '') {
        return null;
    }

    // 画面の正規 URL は常に code-name_romaji 形式を返す。
    $canonical = municipality_public_slug($resolved);
    if ($canonical === '' || normalize_slug_alias_value($canonical) === normalize_slug_alias_value($requested)) {
        return null;
    }

    return $canonical;
}

function redirect_to_canonical_query_slug_if_needed(?string $input = null): void
{
    $canonical = requested_canonical_slug($input);
    if ($canonical === null) {
        return;
    }

    $path = parse_url((string)($_SERVER['REQUEST_URI'] ?? ''), PHP_URL_PATH);
    $path = is_string($path) && $path !== '' ? $path : '/';
    $query = $_GET;
    $query['slug'] = $canonical;
    $location = $path . '?' . http_build_query($query);
    header('Location: ' . $location, true, 302);
    exit;
}

function redirect_to_canonical_boards_slug_if_needed(?string $input = null, string $suffix = ''): void
{
    $canonical = requested_canonical_slug($input);
    if ($canonical === null) {
        return;
    }

    $suffix = ltrim($suffix, '/');
    $location = '/boards/' . rawurlencode($canonical) . '/';
    if ($suffix !== '') {
        $location .= $suffix;
    }
    if (!empty($_GET)) {
        $query = $_GET;
        unset($query['slug']);
        if ($query !== []) {
            $location .= '?' . http_build_query($query);
        }
    }
    header('Location: ' . $location, true, 302);
    exit;
}

function municipality_slugs(): array
{
    return array_keys(municipality_catalog());
}

function get_default_slug(): string
{
    $registry = municipality_catalog();
    $configured = trim((string)(load_config()['DEFAULT_SLUG'] ?? ''));
    if ($configured !== '') {
        $resolvedConfigured = resolve_municipality_slug($configured);
        if ($resolvedConfigured !== '' && isset($registry[$resolvedConfigured])) {
            return $resolvedConfigured;
        }
    }

    $first = array_key_first($registry);
    return is_string($first) ? $first : '';
}

function get_slug(?string $input = null): string
{
    $candidate = $input ?? ($_GET['slug'] ?? get_default_slug());
    $candidate = trim((string)$candidate);

    if ($candidate === '') {
        return get_default_slug();
    }
    return resolve_municipality_slug($candidate);
}

function municipality_entry(string $slug): ?array
{
    $registry = municipality_catalog();
    $slug = trim($slug);
    return $registry[$slug] ?? null;
}

function municipality_feature(string $slug, string $feature): ?array
{
    $entry = municipality_entry($slug);
    if ($entry === null) {
        return null;
    }

    $featureConfig = $entry[$feature] ?? null;
    return is_array($featureConfig) ? $featureConfig : null;
}

function municipality_feature_enabled(string $slug, string $feature): bool
{
    $featureConfig = municipality_feature($slug, $feature);
    if (!is_array($featureConfig)) {
        return false;
    }

    // runtime cache が古くても、実データが見えていれば公開状態へ自動回復させる。
    $hasData = !empty($featureConfig['has_data']) || municipality_feature_live_has_data($feature, $featureConfig);
    return $hasData;
}

function municipality_switcher_items(string $feature): array
{
    static $cache = [];
    if (isset($cache[$feature])) {
        return $cache[$feature];
    }

    $items = [];
    foreach (municipality_catalog() as $slug => $entry) {
        $featureConfig = $entry[$feature] ?? [];
        $items[] = [
            'slug' => $slug,
            'name' => (string)($entry['name'] ?? $slug),
            'enabled' => !empty($featureConfig['enabled']) && !empty($featureConfig['has_data']),
            'url' => (string)($featureConfig['url'] ?? ''),
            'title' => (string)($featureConfig['title'] ?? ''),
        ];
    }
    $cache[$feature] = $items;
    return $cache[$feature];
}
