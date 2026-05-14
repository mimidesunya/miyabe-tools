<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'municipalities.php';

// 既存の minutes.sqlite に、APIの一覧・日付絞り込みで使う追加 index を張る。
// スクレイピング済みの原本データや本文テーブルは削除しない。

function migrate_minutes_indexes_iter_databases(string $root): array
{
    if (is_file($root) && basename($root) === 'minutes.sqlite') {
        return [$root];
    }
    if (!is_dir($root)) {
        return [];
    }
    $direct = rtrim($root, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR . 'minutes.sqlite';
    if (is_file($direct)) {
        return [$direct];
    }

    $items = [];
    foreach (new DirectoryIterator($root) as $entry) {
        if ($entry->isDot() || !$entry->isDir()) {
            continue;
        }
        $path = $entry->getPathname() . DIRECTORY_SEPARATOR . 'minutes.sqlite';
        if (is_file($path)) {
            $items[] = $path;
        }
    }
    sort($items, SORT_STRING);
    return $items;
}

function migrate_minutes_indexes_apply(string $dbPath, bool $optimize): void
{
    $pdo = new PDO('sqlite:' . $dbPath);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $pdo->exec('PRAGMA busy_timeout = 30000');
    $pdo->exec('PRAGMA journal_mode = WAL');
    $pdo->exec(
        'CREATE INDEX IF NOT EXISTS idx_minutes_doc_type_held_on_id
           ON minutes(doc_type, held_on DESC, id DESC)'
    );
    $pdo->exec(
        'CREATE INDEX IF NOT EXISTS idx_minutes_doc_type_year_label_held_on_id
           ON minutes(doc_type, year_label, held_on DESC, id DESC)'
    );
    if ($optimize) {
        $pdo->exec('PRAGMA optimize');
    }
}

$root = $argv[1] ?? data_path('gijiroku');
$optimize = !in_array('--no-optimize', $argv, true);
$databases = migrate_minutes_indexes_iter_databases($root);
if ($databases === []) {
    fwrite(STDOUT, "[WARN] minutes.sqlite が見つかりません: {$root}\n");
    exit(0);
}

$ok = 0;
$failed = 0;
$startedAt = microtime(true);
foreach ($databases as $dbPath) {
    try {
        migrate_minutes_indexes_apply($dbPath, $optimize);
        $ok++;
        fwrite(STDOUT, "[OK] {$dbPath}\n");
    } catch (Throwable $e) {
        $failed++;
        fwrite(STDERR, "[ERROR] {$dbPath}: {$e->getMessage()}\n");
    }
}

$elapsed = microtime(true) - $startedAt;
fwrite(STDOUT, sprintf("[DONE] migrated=%d failed=%d time=%.3fs\n", $ok, $failed, $elapsed));
exit($failed > 0 ? 1 : 0);
