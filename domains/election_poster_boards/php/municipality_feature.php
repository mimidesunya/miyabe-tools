<?php
declare(strict_types=1);

/**
 * Build the poster-board portion of a normalized municipality entry.
 *
 * Municipality identity and canonical slug resolution stay in the shared
 * registry. Paths, availability detection and public board URLs belong here.
 */
function poster_boards_normalize_municipality_feature(
    string $slug,
    string $publicSlug,
    string $name,
    array $config
): array {
    $dbRelative = normalize_data_relative_path(
        trim((string)($config['db_path'] ?? "boards/{$slug}/boards.sqlite"))
    );
    $tasksDbRelative = normalize_data_relative_path(
        trim((string)($config['tasks_db_path'] ?? "boards/{$slug}/tasks.sqlite"))
    );
    $dbPath = data_path($dbRelative);
    $tasksDbPath = data_path($tasksDbRelative);
    $hasData = is_file($dbPath);

    return [
        'enabled' => feature_enabled_value($config['enabled'] ?? null, $hasData),
        'has_data' => $hasData,
        'title' => trim((string)($config['title'] ?? "{$name} 選挙ポスター掲示場")),
        'description' => trim((string)($config['description'] ?? '選挙ポスター掲示場の位置確認と作業状況共有')),
        'url' => "/boards/{$publicSlug}/",
        'list_url' => '/boards/list.php?slug=' . rawurlencode($publicSlug),
        'users_url' => '/boards/users.php?slug=' . rawurlencode($publicSlug),
        'db_path_rel' => $dbRelative,
        'tasks_db_path_rel' => $tasksDbRelative,
        'db_path' => $dbPath,
        'tasks_db_path' => $tasksDbPath,
    ];
}

function poster_boards_feature_has_live_data(array $featureConfig): bool
{
    $dbPath = trim((string)($featureConfig['db_path'] ?? ''));
    return $dbPath !== '' && is_file($dbPath);
}

function poster_boards_municipality_entry(string $slug): ?array
{
    $municipality = municipality_entry($slug);
    if (!is_array($municipality)) {
        return null;
    }

    $publicSlug = (string)($municipality['public_slug'] ?? $slug);
    $name = (string)($municipality['name'] ?? $slug);
    $municipality['boards'] = poster_boards_normalize_municipality_feature(
        $slug,
        $publicSlug,
        $name,
        []
    );
    return $municipality;
}

function poster_boards_municipality_feature(string $slug): ?array
{
    $municipality = poster_boards_municipality_entry($slug);
    $feature = is_array($municipality) ? ($municipality['boards'] ?? null) : null;
    return is_array($feature) ? $feature : null;
}

function poster_boards_municipality_switcher_items(): array
{
    static $items = null;
    if (is_array($items)) {
        return $items;
    }

    $items = [];
    foreach (municipality_catalog() as $slug => $baseMunicipality) {
        $municipality = poster_boards_municipality_entry((string)$slug);
        if (!is_array($municipality)) {
            continue;
        }
        $feature = is_array($municipality['boards'] ?? null) ? $municipality['boards'] : [];
        $items[] = [
            'slug' => (string)$slug,
            'name' => (string)($baseMunicipality['name'] ?? $slug),
            'enabled' => !empty($feature['enabled']) && !empty($feature['has_data']),
            'url' => (string)($feature['url'] ?? ''),
            'title' => (string)($feature['title'] ?? ''),
            'boards' => $feature,
        ];
    }
    return $items;
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
