<?php
declare(strict_types=1);

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

function choose_existing_data_path(array $relativeCandidates, string $defaultRelative): string
{
    foreach ($relativeCandidates as $relative) {
        $relative = trim((string)$relative);
        if ($relative === '') {
            continue;
        }

        $path = data_path($relative);
        if (file_exists($path)) {
            return $path;
        }
    }

    return data_path($defaultRelative);
}

function raw_municipality_entries(): array
{
    $entries = load_config()['MUNICIPALITIES'] ?? [];
    return is_array($entries) ? $entries : [];
}

function canonical_municipality_slug(string $slug): string
{
    $normalized = trim($slug);
    return match ($normalized) {
        'kawasaki' => 'kawasaki-shi',
        'higashikurume' => 'higashikurume-shi',
        default => $normalized,
    };
}

function municipality_default_name(string $slug): string
{
    $slug = canonical_municipality_slug($slug);
    return match ($slug) {
        'kawasaki-shi' => '川崎市',
        'higashikurume-shi' => '東久留米市',
        'hino-shi' => '日野市',
        default => $slug,
    };
}

function legacy_reiki_defaults(string $slug): array
{
    $slug = canonical_municipality_slug($slug);
    if ($slug === 'kawasaki-shi') {
        return [
            'title' => '川崎市例規集 AI評価ビューア',
            'source_dir' => 'reiki/kawasaki-shi/source',
            'clean_html_dir' => 'reiki/kawasaki-shi/html',
            'classification_dir' => 'reiki/kawasaki-shi/json',
            'image_dir' => 'reiki/kawasaki-shi/images',
            'markdown_dir' => 'reiki/kawasaki-shi/markdown',
            'db_path' => 'reiki/kawasaki-shi/ordinances.sqlite',
            'legacy_db_path' => 'reiki/ordinances.sqlite',
            'sortable_prefixes' => ['かわさきし', 'かわさき'],
        ];
    }

    return [
        'source_dir' => "reiki/{$slug}/source",
        'clean_html_dir' => "reiki/{$slug}/html",
        'classification_dir' => "reiki/{$slug}/json",
        'image_dir' => "reiki/{$slug}/images",
        'markdown_dir' => "reiki/{$slug}/markdown",
        'db_path' => "reiki/{$slug}/ordinances.sqlite",
        'legacy_db_path' => 'reiki/ordinances.sqlite',
    ];
}

function legacy_gijiroku_defaults(string $slug, string $name): array
{
    $slug = canonical_municipality_slug($slug);
    if ($slug === 'kawasaki-shi') {
        return [
            'title' => '川崎市議会 会議録 全文検索',
            'assembly_name' => '川崎市議会',
            'data_dir' => 'gijiroku/kawasaki-shi',
            'downloads_dir' => 'gijiroku/kawasaki-shi/downloads',
            'index_json_path' => 'gijiroku/kawasaki-shi/meetings_index.json',
            'db_path' => 'gijiroku/kawasaki-shi/minutes.sqlite',
        ];
    }

    $defaultDataDir = "gijiroku/{$slug}";
    return [
        'assembly_name' => "{$name}議会",
        'data_dir' => $defaultDataDir,
        'downloads_dir' => $defaultDataDir . '/downloads',
        'index_json_path' => $defaultDataDir . '/meetings_index.json',
        'db_path' => $defaultDataDir . '/minutes.sqlite',
    ];
}

function normalize_municipality_entry(string $slug, array $entry, bool $isDefaultSlug, bool $singleMunicipality): array
{
    $name = trim((string)($entry['name'] ?? $entry['label'] ?? municipality_default_name($slug)));
    if ($name === '') {
        $name = $slug;
    }

    $boardsConfig = is_array($entry['boards'] ?? null) ? $entry['boards'] : [];
    $boardsEnabled = feature_enabled_value($boardsConfig['enabled'] ?? null, true);
    $boardsAllowOffset = (bool)($boardsConfig['allow_offset'] ?? $entry['allow_offset'] ?? false);

    $reikiConfig = is_array($entry['reiki'] ?? null) ? $entry['reiki'] : [];
    $reikiDefaults = legacy_reiki_defaults($slug);
    $reikiSourceRelative = trim((string)($reikiConfig['source_dir'] ?? $reikiConfig['data_dir'] ?? $reikiDefaults['source_dir']));
    $reikiCleanHtmlRelative = trim((string)($reikiConfig['clean_html_dir'] ?? $reikiDefaults['clean_html_dir']));
    $reikiClassificationRelative = trim((string)($reikiConfig['classification_dir'] ?? $reikiDefaults['classification_dir']));
    $reikiImageRelative = trim((string)($reikiConfig['image_dir'] ?? $reikiDefaults['image_dir']));
    $reikiMarkdownRelative = trim((string)($reikiConfig['markdown_dir'] ?? $reikiDefaults['markdown_dir']));
    $reikiDbRelative = trim((string)($reikiConfig['db_path'] ?? $reikiDefaults['db_path']));
    $reikiLegacyDbRelative = trim((string)($reikiConfig['legacy_db_path'] ?? $reikiDefaults['legacy_db_path']));
    $reikiDbPath = choose_existing_data_path(
        [$reikiDbRelative, ($isDefaultSlug || $singleMunicipality) ? $reikiLegacyDbRelative : ''],
        $reikiDbRelative
    );
    $reikiDetected = is_dir(data_path($reikiCleanHtmlRelative))
        || is_dir(data_path($reikiClassificationRelative))
        || is_file($reikiDbPath);
    $reikiEnabled = feature_enabled_value($reikiConfig['enabled'] ?? null, $reikiDetected);

    $gijirokuConfig = is_array($entry['gijiroku'] ?? null) ? $entry['gijiroku'] : [];
    $gijirokuDefaults = legacy_gijiroku_defaults($slug, $name);
    $gijirokuDataRelative = trim((string)($gijirokuConfig['data_dir'] ?? $gijirokuDefaults['data_dir']));
    $gijirokuDownloadsRelative = trim((string)($gijirokuConfig['downloads_dir'] ?? $gijirokuDefaults['downloads_dir']));
    $gijirokuIndexJsonRelative = trim((string)($gijirokuConfig['index_json_path'] ?? $gijirokuDefaults['index_json_path']));
    $gijirokuDbRelative = trim((string)($gijirokuConfig['db_path'] ?? $gijirokuDefaults['db_path']));
    $assemblyName = trim((string)($gijirokuConfig['assembly_name'] ?? $gijirokuDefaults['assembly_name']));
    $gijirokuDetected = ($gijirokuDataRelative !== '' && is_dir(data_path($gijirokuDataRelative)))
        || ($gijirokuIndexJsonRelative !== '' && is_file(data_path($gijirokuIndexJsonRelative)))
        || ($gijirokuDbRelative !== '' && is_file(data_path($gijirokuDbRelative)));
    $gijirokuEnabled = feature_enabled_value($gijirokuConfig['enabled'] ?? null, $gijirokuDetected);

    $code = trim((string)($entry['code'] ?? ''));

    return [
        'slug' => $slug,
        'code' => $code,
        'name' => $name,
        'boards' => [
            'enabled' => $boardsEnabled,
            'allow_offset' => $boardsAllowOffset,
            'title' => trim((string)($boardsConfig['title'] ?? "{$name} ポスター掲示場")),
            'description' => trim((string)($boardsConfig['description'] ?? '選挙ポスター掲示場の位置確認と作業状況共有')),
            'url' => "/boards/{$slug}/",
            'list_url' => '/boards/list.php?slug=' . rawurlencode($slug),
            'users_url' => '/boards/users.php?slug=' . rawurlencode($slug),
        ],
        'reiki' => [
            'enabled' => $reikiEnabled,
            'title' => trim((string)($reikiConfig['title'] ?? ($reikiDefaults['title'] ?? "{$name}例規集 AI評価ビューア"))),
            'description' => trim((string)($reikiConfig['description'] ?? '例規集の検索とAI評価結果の閲覧')),
            'url' => '/reiki/?slug=' . rawurlencode($slug),
            'source_dir_rel' => normalize_data_relative_path($reikiSourceRelative),
            'clean_html_dir_rel' => normalize_data_relative_path($reikiCleanHtmlRelative),
            'classification_dir_rel' => normalize_data_relative_path($reikiClassificationRelative),
            'image_dir_rel' => normalize_data_relative_path($reikiImageRelative),
            'markdown_dir_rel' => normalize_data_relative_path($reikiMarkdownRelative),
            'db_path_rel' => normalize_data_relative_path($reikiDbRelative),
            'source_dir' => work_path($reikiSourceRelative),
            'clean_html_dir' => data_path($reikiCleanHtmlRelative),
            'classification_dir' => data_path($reikiClassificationRelative),
            'image_dir' => data_path($reikiImageRelative),
            'image_url' => data_public_url($reikiImageRelative),
            'markdown_dir' => work_path($reikiMarkdownRelative),
            'db_path' => $reikiDbPath,
        ],
        'gijiroku' => [
            'enabled' => $gijirokuEnabled,
            'title' => trim((string)($gijirokuConfig['title'] ?? ($gijirokuDefaults['title'] ?? "{$assemblyName} 会議録 全文検索"))),
            'description' => trim((string)($gijirokuConfig['description'] ?? "{$assemblyName}の会議録を全文検索")),
            'assembly_name' => $assemblyName,
            'url' => '/gijiroku/?slug=' . rawurlencode($slug),
            'data_dir_rel' => normalize_data_relative_path($gijirokuDataRelative),
            'downloads_dir_rel' => normalize_data_relative_path($gijirokuDownloadsRelative),
            'index_json_path_rel' => normalize_data_relative_path($gijirokuIndexJsonRelative),
            'db_path_rel' => normalize_data_relative_path($gijirokuDbRelative),
            'data_dir' => data_path($gijirokuDataRelative),
            'work_dir' => work_path($gijirokuDataRelative),
            'downloads_dir' => work_path($gijirokuDownloadsRelative),
            'index_json_path' => work_path($gijirokuIndexJsonRelative),
            'db_path' => data_path($gijirokuDbRelative),
        ],
    ];
}

function municipality_registry(): array
{
    static $registry = null;
    if ($registry !== null) {
        return $registry;
    }

    $entries = raw_municipality_entries();
    $config = load_config();
    $defaultSlug = canonical_municipality_slug(trim((string)($config['DEFAULT_SLUG'] ?? '')));
    $validSlugs = [];
    foreach (array_keys($entries) as $slug) {
        if (is_string($slug)) {
            $slug = canonical_municipality_slug($slug);
        }
        if (is_string($slug) && preg_match('/^[a-z0-9_-]+$/', $slug) === 1) {
            $validSlugs[] = $slug;
        }
    }

    $singleMunicipality = count($validSlugs) <= 1;
    $registry = [];
    foreach ($entries as $slug => $entry) {
        if (is_string($slug)) {
            $slug = canonical_municipality_slug($slug);
        }
        if (!is_string($slug) || preg_match('/^[a-z0-9_-]+$/', $slug) !== 1) {
            continue;
        }

        $normalizedEntry = is_array($entry) ? $entry : [];
        $isDefaultSlug = $slug === $defaultSlug || ($defaultSlug === '' && $registry === []);
        $registry[$slug] = normalize_municipality_entry($slug, $normalizedEntry, $isDefaultSlug, $singleMunicipality);
    }

    uasort($registry, function (array $a, array $b): int {
        $ca = (string)($a['code'] ?? '');
        $cb = (string)($b['code'] ?? '');
        if ($ca === '' && $cb === '') return 0;
        if ($ca === '') return 1;
        if ($cb === '') return -1;
        return strcmp($ca, $cb);
    });
    return $registry;
}

function municipality_slugs(): array
{
    return array_keys(municipality_registry());
}

function get_default_slug(): string
{
    $registry = municipality_registry();
    $configured = canonical_municipality_slug(trim((string)(load_config()['DEFAULT_SLUG'] ?? '')));
    if ($configured !== '' && isset($registry[$configured])) {
        return $configured;
    }

    $first = array_key_first($registry);
    return is_string($first) ? $first : '';
}

function get_slug(?string $input = null): string
{
    $registry = municipality_registry();
    $candidate = $input ?? ($_GET['slug'] ?? get_default_slug());
    $candidate = canonical_municipality_slug(trim((string)$candidate));

    if ($candidate === '') {
        return get_default_slug();
    }
    if (preg_match('/^[a-z0-9_-]+$/', $candidate) !== 1) {
        return '';
    }
    return isset($registry[$candidate]) ? $candidate : '';
}

function municipality_entry(string $slug): ?array
{
    $registry = municipality_registry();
    $slug = canonical_municipality_slug($slug);
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
    return (bool)($featureConfig['enabled'] ?? false);
}

function municipality_switcher_items(string $feature): array
{
    $items = [];
    foreach (municipality_registry() as $slug => $entry) {
        $featureConfig = $entry[$feature] ?? [];
        $items[] = [
            'slug' => $slug,
            'name' => (string)($entry['name'] ?? $slug),
            'enabled' => (bool)($featureConfig['enabled'] ?? false),
            'url' => (string)($featureConfig['url'] ?? ''),
            'title' => (string)($featureConfig['title'] ?? ''),
        ];
    }
    return $items;
}
