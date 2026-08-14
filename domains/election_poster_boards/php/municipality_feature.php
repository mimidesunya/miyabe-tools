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
