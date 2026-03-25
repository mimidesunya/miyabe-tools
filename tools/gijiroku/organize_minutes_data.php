#!/usr/bin/env php
<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'municipalities.php';

main($argv);

function main(array $argv): void
{
    $options = getopt('', ['slug:', 'dry-run']);
    $slug = trim((string)($options['slug'] ?? get_default_slug()));
    $dryRun = array_key_exists('dry-run', $options);

    $feature = municipality_feature($slug, 'gijiroku') ?? [];
    $dataDir = (string)($feature['data_dir'] ?? '');
    $workDir = (string)($feature['work_dir'] ?? '');
    $downloadsDir = (string)($feature['downloads_dir'] ?? '');
    $dbPath = (string)($feature['db_path'] ?? '');

    if ($dataDir === '' || $downloadsDir === '') {
        throw new RuntimeException("gijiroku paths not found for slug: {$slug}");
    }
    if (!is_dir($dataDir) || !is_dir($downloadsDir)) {
        throw new RuntimeException("gijiroku data dir not found: {$dataDir}");
    }

    $pdo = null;
    if ($dbPath !== '' && is_file($dbPath)) {
        $pdo = new PDO('sqlite:' . $dbPath);
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    }

    $moved = 0;
    $deleted = 0;
    $dbUpdated = 0;
    $removedLogs = 0;
    $removedDirs = 0;

    $rootFiles = glob($downloadsDir . DIRECTORY_SEPARATOR . '*');
    if ($rootFiles === false) {
        $rootFiles = [];
    }

    foreach ($rootFiles as $path) {
        if (!is_file($path)) {
            continue;
        }

        $plan = plan_root_file($path, $downloadsDir);
        if ($plan === null) {
            continue;
        }

        if ($plan['action'] === 'delete') {
            if (!$dryRun && !@unlink($path)) {
                throw new RuntimeException("failed to delete file: {$path}");
            }
            if ($pdo && !$dryRun) {
                $stmt = $pdo->prepare('DELETE FROM minutes WHERE rel_path = :rel_path');
                $stmt->execute([':rel_path' => $plan['old_rel']]);
                $dbUpdated += $stmt->rowCount();
            }
            $deleted++;
            continue;
        }

        if ($plan['action'] !== 'move') {
            continue;
        }

        $destination = unique_destination((string)$plan['destination']);
        if (!$dryRun) {
            ensure_dir(dirname($destination));
            if (!@rename($path, $destination)) {
                throw new RuntimeException("failed to move file: {$path} -> {$destination}");
            }
        }

        if ($pdo && !$dryRun) {
            $stmt = $pdo->prepare('UPDATE minutes SET rel_path = :new_rel WHERE rel_path = :old_rel');
            $stmt->execute([
                ':new_rel' => relative_path($destination, $downloadsDir),
                ':old_rel' => $plan['old_rel'],
            ]);
            $dbUpdated += $stmt->rowCount();
        }

        $moved++;
    }

    $logFiles = glob($workDir . DIRECTORY_SEPARATOR . 'run_result_*.csv');
    if ($logFiles === false) {
        $logFiles = [];
    }
    foreach ($logFiles as $path) {
        if (!is_file($path)) {
            continue;
        }
        if (!$dryRun && !@unlink($path)) {
            throw new RuntimeException("failed to delete log file: {$path}");
        }
        $removedLogs++;
    }

    $pagesDir = $workDir . DIRECTORY_SEPARATOR . 'pages';
    if (is_dir($pagesDir)) {
        $removedDirs += remove_empty_directories($pagesDir, true, $dryRun);
    }

    echo "Organized gijiroku data for {$slug}\n";
    echo "  moved files: {$moved}\n";
    echo "  deleted junk files: {$deleted}\n";
    echo "  deleted run_result logs: {$removedLogs}\n";
    echo "  removed empty directories: {$removedDirs}\n";
    echo "  DB rows updated/deleted: {$dbUpdated}\n";
    if ($dryRun) {
        echo "  mode: dry-run\n";
    }
}

function plan_root_file(string $path, string $downloadsDir): ?array
{
    $ext = strtolower(pathinfo($path, PATHINFO_EXTENSION));
    if (!in_array($ext, ['txt', 'html', 'htm'], true)) {
        return null;
    }

    $decoded = read_text_auto($path);
    $text = ($ext === 'html' || $ext === 'htm') ? html_to_text($decoded) : $decoded;
    if ($text === '') {
        return null;
    }

    if (($ext === 'html' || $ext === 'htm') && looks_like_listing_html($text)) {
        return [
            'action' => 'delete',
            'old_rel' => basename($path),
        ];
    }

    $yearLabel = extract_year_label_from_text($text);
    $meetingName = extract_meeting_name_from_text($text);
    if ($yearLabel === null || $meetingName === null) {
        return null;
    }

    $destination = $downloadsDir
        . DIRECTORY_SEPARATOR . sanitize_path_component($yearLabel, 'unknown-year')
        . DIRECTORY_SEPARATOR . sanitize_path_component(trim_meeting_dir_label($meetingName), 'meeting')
        . DIRECTORY_SEPARATOR . basename($path);

    return [
        'action' => 'move',
        'destination' => $destination,
        'old_rel' => basename($path),
    ];
}

function unique_destination(string $destination): string
{
    if (!file_exists($destination)) {
        return $destination;
    }

    $dir = dirname($destination);
    $name = pathinfo($destination, PATHINFO_FILENAME);
    $ext = pathinfo($destination, PATHINFO_EXTENSION);
    $suffix = $ext !== '' ? '.' . $ext : '';
    $counter = 2;
    while (true) {
        $candidate = $dir . DIRECTORY_SEPARATOR . $name . '__' . $counter . $suffix;
        if (!file_exists($candidate)) {
            return $candidate;
        }
        $counter++;
    }
}

function relative_path(string $path, string $base): string
{
    $normalizedPath = str_replace('\\', '/', $path);
    $normalizedBase = rtrim(str_replace('\\', '/', $base), '/');
    if (str_starts_with($normalizedPath, $normalizedBase . '/')) {
        return substr($normalizedPath, strlen($normalizedBase) + 1);
    }
    return basename($path);
}

function ensure_dir(string $dir): void
{
    if ($dir === '' || is_dir($dir)) {
        return;
    }
    if (!mkdir($dir, 0777, true) && !is_dir($dir)) {
        throw new RuntimeException("failed to create directory: {$dir}");
    }
}

function remove_empty_directories(string $dir, bool $removeSelf, bool $dryRun): int
{
    if (!is_dir($dir)) {
        return 0;
    }

    $removed = 0;
    $children = scandir($dir);
    if ($children === false) {
        return 0;
    }

    foreach ($children as $child) {
        if ($child === '.' || $child === '..') {
            continue;
        }
        $childPath = $dir . DIRECTORY_SEPARATOR . $child;
        if (is_dir($childPath)) {
            $removed += remove_empty_directories($childPath, true, $dryRun);
        }
    }

    $remaining = scandir($dir);
    if ($remaining === false) {
        return $removed;
    }
    if ($removeSelf && count(array_diff($remaining, ['.', '..'])) === 0) {
        if (!$dryRun && !@rmdir($dir)) {
            throw new RuntimeException("failed to remove directory: {$dir}");
        }
        $removed++;
    }

    return $removed;
}

function read_text_auto(string $path): string
{
    $raw = (string)file_get_contents($path);
    if ($raw === '') {
        return '';
    }

    if (preg_match('//u', $raw) === 1) {
        return $raw;
    }

    foreach (['SJIS-win', 'SJIS', 'EUC-JP'] as $encoding) {
        $converted = @mb_convert_encoding($raw, 'UTF-8', $encoding);
        if (is_string($converted) && $converted !== '' && preg_match('//u', $converted) === 1) {
            return $converted;
        }
    }

    return @mb_convert_encoding($raw, 'UTF-8', 'SJIS-win') ?: $raw;
}

function html_to_text(string $html): string
{
    $text = preg_replace('/<script[\s\S]*?<\/script>/i', '', $html);
    $text = preg_replace('/<style[\s\S]*?<\/style>/i', '', (string)$text);
    $text = preg_replace('/<br\s*\/?>/i', "\n", (string)$text);
    $text = preg_replace('/<\/(p|div|li|tr|table|h[1-6])>/i', "\n", (string)$text);
    $text = preg_replace('/<[^>]+>/', '', (string)$text);
    $text = html_entity_decode((string)$text, ENT_QUOTES | ENT_HTML5, 'UTF-8');
    $text = str_replace(["\r\n", "\r"], "\n", (string)$text);
    $text = preg_replace("/\n{3,}/", "\n\n", (string)$text);
    return trim((string)$text);
}

function normalize_space(string $value): string
{
    $normalized = preg_replace('/[ \t　]+/u', ' ', trim($value));
    return $normalized === null ? trim($value) : $normalized;
}

function first_nonempty_lines(string $text, int $limit = 8): array
{
    $lines = [];
    foreach (preg_split("/\r\n|\n|\r/", $text) ?: [] as $line) {
        $clean = normalize_space((string)$line);
        if ($clean === '') {
            continue;
        }
        $lines[] = $clean;
        if (count($lines) >= $limit) {
            break;
        }
    }
    return $lines;
}

function to_ascii_digits(string $value): string
{
    return strtr($value, [
        '０' => '0',
        '１' => '1',
        '２' => '2',
        '３' => '3',
        '４' => '4',
        '５' => '5',
        '６' => '6',
        '７' => '7',
        '８' => '8',
        '９' => '9',
    ]);
}

function extract_year_label_from_text(string $text): ?string
{
    $head = implode("\n", first_nonempty_lines($text, 6));
    if (!preg_match('/(昭和|平成|令和)\s*([元\d０-９]+)年(?:・(昭和|平成|令和)元年)?/u', $head, $matches)) {
        return null;
    }

    $label = $matches[1] . to_ascii_digits($matches[2]) . '年';
    if (!empty($matches[3])) {
        $label .= '・' . $matches[3] . '元年';
    }
    return $label;
}

function extract_meeting_name_from_text(string $text): ?string
{
    $lines = first_nonempty_lines($text, 5);
    if (count($lines) >= 2) {
        $second = $lines[1];
        if (!str_contains($second, '－') && mb_strlen($second, 'UTF-8') >= 4) {
            return $second;
        }
    }

    if ($lines !== []) {
        $first = preg_replace('/－[^－]+$/u', '', $lines[0]);
        $first = trim((string)$first);
        if (mb_strlen($first, 'UTF-8') >= 4) {
            return $first;
        }
    }

    return null;
}

function sanitize_path_component(string $value, string $fallback): string
{
    $cleaned = preg_replace('/[\\\\\\/:*?"<>|\t\r\n]+/u', '_', trim($value));
    $cleaned = trim((string)$cleaned, " .");
    return $cleaned !== '' ? $cleaned : $fallback;
}

function trim_meeting_dir_label(string $meetingName): string
{
    $trimmed = preg_replace('/^(昭和|平成|令和)\s*[元\d０-９]+年\s*/u', '', $meetingName);
    $trimmed = normalize_space((string)$trimmed);
    return $trimmed !== '' ? $trimmed : normalize_space($meetingName);
}

function looks_like_listing_html(string $text): bool
{
    return preg_match('/\d+件の日程がヒット/u', $text) === 1
        || str_contains($text, 'クリックすると発言者を表示');
}
