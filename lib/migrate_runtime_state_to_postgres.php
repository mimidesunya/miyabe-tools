<?php
declare(strict_types=1);

require_once __DIR__ . DIRECTORY_SEPARATOR . 'homepage' . DIRECTORY_SEPARATOR . 'runtime.php';

// One-shot migration entrypoint for production containers.
// It seeds PostgreSQL from legacy runtime JSON and rebuilds the derived
// homepage rows. The JSON files remain as transition/audit input until the
// PostgreSQL path has survived normal scraper cycles.

if (PHP_SAPI !== 'cli') {
    http_response_code(404);
    exit;
}

$tasks = [
    'gijiroku',
    'gijiroku_snapshot',
    'gijiroku_reflect',
    'gijiroku_rebuild',
    'reiki',
    'reiki_snapshot',
    'reiki_reflect',
    'search_rebuild',
];

$stored = 0;
foreach ($tasks as $task) {
    $path = background_task_status_path($task);
    if (!is_file($path)) {
        fwrite(STDOUT, "[SKIP] {$task}: no legacy JSON\n");
        continue;
    }

    $decoded = json_decode((string)file_get_contents($path), true);
    if (!is_array($decoded)) {
        fwrite(STDOUT, "[WARN] {$task}: invalid legacy JSON\n");
        continue;
    }

    management_db_store_task_status($task, $decoded, (float)@filemtime($path));
    $count = is_array($decoded['items'] ?? null) ? count($decoded['items']) : 0;
    fwrite(STDOUT, "[DB] {$task}: {$count} task items\n");
    $stored += 1;
}

$payload = homepage_rebuild_api_payload_cache();
$cards = is_array($payload['municipalities'] ?? null) ? count($payload['municipalities']) : 0;
fwrite(STDOUT, "[DB] homepage cards: {$cards}\n");
fwrite(STDOUT, "[DONE] task_statuses={$stored}\n");
