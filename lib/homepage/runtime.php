<?php
declare(strict_types=1);

require_once __DIR__ . DIRECTORY_SEPARATOR . '..' . DIRECTORY_SEPARATOR . 'municipalities.php';
require_once __DIR__ . DIRECTORY_SEPARATOR . '..' . DIRECTORY_SEPARATOR . 'background_tasks.php';
require_once __DIR__ . DIRECTORY_SEPARATOR . '..' . DIRECTORY_SEPARATOR . 'opensearch_search.php';
require_once __DIR__ . DIRECTORY_SEPARATOR . '..' . DIRECTORY_SEPARATOR . 'management_db.php';

// トップページで必要な件数集計やタスク表示の組み立てをここへ寄せる。
// app/index.php 側は、返ってきた配列を描画するだけに留める。

function homepage_json_array_count_auto(string $path): int
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

        $cache[$path] = homepage_json_top_level_array_count($candidate);
        return $cache[$path];
    }

    $cache[$path] = 0;
    return 0;
}

function homepage_json_top_level_array_count(string $path): int
{
    $isGzip = str_ends_with(strtolower($path), '.gz');
    $handle = $isGzip ? @gzopen($path, 'rb') : @fopen($path, 'rb');
    if (!$handle) {
        return 0;
    }

    $depth = 0;
    $inString = false;
    $escaped = false;
    $hasValue = false;
    $count = 0;
    while (!($isGzip ? gzeof($handle) : feof($handle))) {
        $chunk = $isGzip ? gzread($handle, 65536) : fread($handle, 65536);
        if (!is_string($chunk) || $chunk === '') {
            continue;
        }
        $length = strlen($chunk);
        for ($i = 0; $i < $length; $i++) {
            $char = $chunk[$i];
            if ($inString) {
                if ($escaped) {
                    $escaped = false;
                } elseif ($char === '\\') {
                    $escaped = true;
                } elseif ($char === '"') {
                    $inString = false;
                }
                continue;
            }
            if ($char === '"') {
                $inString = true;
                if ($depth === 1) {
                    $hasValue = true;
                }
                continue;
            }
            if ($char === '[' || $char === '{') {
                if ($depth === 1 && $char === '{') {
                    $hasValue = true;
                }
                $depth++;
                continue;
            }
            if ($char === ']' || $char === '}') {
                if ($depth > 0) {
                    $depth--;
                }
                continue;
            }
            if ($depth === 1 && $char === ',') {
                $count++;
                continue;
            }
            if ($depth === 1 && !ctype_space($char)) {
                $hasValue = true;
            }
        }
    }
    $isGzip ? gzclose($handle) : fclose($handle);
    return $hasValue ? $count + 1 : 0;
}

function homepage_json_array_auto(string $path): array
{
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
        if (is_array($decoded)) {
            return $decoded;
        }
    }

    return [];
}

function homepage_gijiroku_index_unique_count(string $path): int
{
    $rows = homepage_json_array_auto($path);
    $seen = [];
    foreach ($rows as $row) {
        if (!is_array($row)) {
            continue;
        }
        $url = trim((string)($row['url'] ?? ''));
        if ($url !== '') {
            $seen['url:' . $url] = true;
            continue;
        }
        $encoded = json_encode($row, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE);
        if (is_string($encoded) && $encoded !== '') {
            $seen['row:' . sha1($encoded)] = true;
        }
    }
    return count($seen);
}

function homepage_gijiroku_sanitize_filename(string $text, string $fallback): string
{
    $cleaned = preg_replace('/[\\\\\/:*?"<>|\t\r\n]+/u', '_', $text) ?? '';
    $cleaned = trim($cleaned, " .");
    if ($cleaned === '') {
        return $fallback;
    }
    return function_exists('mb_substr') ? mb_substr($cleaned, 0, 180) : substr($cleaned, 0, 180);
}

function homepage_gijiroku_normalize_year_dir(string $yearLabel): string
{
    return homepage_gijiroku_sanitize_filename(trim($yearLabel) !== '' ? $yearLabel : 'unknown', 'unknown');
}

function homepage_gijiroku_normalize_group_dir(string $meetingGroup): string
{
    $meetingGroup = trim($meetingGroup);
    return $meetingGroup !== '' ? homepage_gijiroku_sanitize_filename($meetingGroup, 'meeting') : '';
}

function homepage_gijiroku_signature(array $row): string
{
    $normalize = static function (mixed $value) use (&$normalize): mixed {
        if (!is_array($value)) {
            return $value;
        }
        $isList = array_is_list($value);
        if (!$isList) {
            ksort($value);
        }
        foreach ($value as $key => $child) {
            $value[$key] = $normalize($child);
        }
        return $value;
    };
    $encoded = json_encode(
        $normalize($row),
        JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE
    );
    return sha1(is_string($encoded) ? $encoded : '');
}

function homepage_gijiroku_disambiguated_stem(string $stem, string $discriminator, int $occurrenceIndex): string
{
    $stem = trim($stem) !== '' ? trim($stem) : 'meeting';
    if ($occurrenceIndex <= 0) {
        return $stem;
    }
    return $stem . '-' . substr(sha1($discriminator !== '' ? $discriminator : $stem), 0, 8);
}

function homepage_gijiroku_existing_named_output(string $directory, string $stem): bool
{
    if (!is_dir($directory)) {
        return false;
    }
    try {
        $iterator = new DirectoryIterator($directory);
        $pattern = '/^' . preg_quote($stem, '/') . '\.[^.\/]+(?:\.gz)?$/iu';
        foreach ($iterator as $fileInfo) {
            if (!$fileInfo->isFile()) {
                continue;
            }
            if (preg_match($pattern, $fileInfo->getFilename()) === 1) {
                return true;
            }
        }
    } catch (Throwable) {
        return false;
    }
    return false;
}

function homepage_gijiroku_indexed_download_count(string $indexPath, string $downloadsDir): int
{
    if (!is_dir($downloadsDir)) {
        return 0;
    }
    $rows = homepage_json_array_auto($indexPath);
    $seenItems = [];
    $seenOutputStems = [];
    $downloaded = 0;
    foreach ($rows as $row) {
        if (!is_array($row)) {
            continue;
        }
        $url = trim((string)($row['url'] ?? ''));
        $itemKey = $url !== '' ? ('url:' . $url) : ('row:' . homepage_gijiroku_signature($row));
        if (isset($seenItems[$itemKey])) {
            continue;
        }
        $seenItems[$itemKey] = true;

        $yearDir = homepage_gijiroku_normalize_year_dir((string)($row['year_label'] ?? ''));
        $groupDir = homepage_gijiroku_normalize_group_dir((string)($row['meeting_group'] ?? ''));
        $relativeDirParts = [$yearDir];
        if ($groupDir !== '') {
            $relativeDirParts[] = $groupDir;
        }
        $relativeDir = implode('/', $relativeDirParts);
        $stem = homepage_gijiroku_sanitize_filename((string)($row['title'] ?? ''), 'meeting');
        $stemScope = $relativeDir . "\n" . $stem;
        $occurrenceIndex = (int)($seenOutputStems[$stemScope] ?? 0);
        $seenOutputStems[$stemScope] = $occurrenceIndex + 1;
        $stem = homepage_gijiroku_disambiguated_stem($stem, homepage_gijiroku_signature($row), $occurrenceIndex);

        $directory = rtrim($downloadsDir, DIRECTORY_SEPARATOR . '/\\') . DIRECTORY_SEPARATOR
            . str_replace('/', DIRECTORY_SEPARATOR, $relativeDir);
        if (homepage_gijiroku_existing_named_output($directory, $stem)) {
            $downloaded++;
        }
    }
    return $downloaded;
}

function homepage_progress_count_is_complete(int $currentCount, int $totalCount): bool
{
    return $totalCount > 0 && $currentCount >= $totalCount;
}

function homepage_progress_count_detail(int $currentCount, int $totalCount): string
{
    $currentCount = max(0, $currentCount);
    $totalCount = max(0, $totalCount);
    if ($currentCount <= 0 && $totalCount <= 0) {
        return '';
    }
    if ($totalCount > 0) {
        $currentCount = min($currentCount, $totalCount);
        if (homepage_progress_count_is_complete($currentCount, $totalCount)) {
            return sprintf('%d件', $currentCount);
        }
        return sprintf('%d/%d件', $currentCount, $totalCount);
    }
    return sprintf('%d件', $currentCount);
}

function homepage_progress_count_labeled_detail(string $label, int $currentCount, int $totalCount): string
{
    $detail = homepage_progress_count_detail($currentCount, $totalCount);
    if ($detail === '') {
        return '';
    }
    $label = trim($label);
    return $label !== '' ? ($label . ' ' . $detail) : $detail;
}

function homepage_unique_logical_file_count(string $path, array $allowedSuffixes): int
{
    static $cache = [];
    $normalizedAllowed = array_values(array_unique(array_map(static function ($suffix): string {
        $suffix = strtolower(trim((string)$suffix));
        return str_starts_with($suffix, '.') ? $suffix : ('.' . $suffix);
    }, $allowedSuffixes)));
    sort($normalizedAllowed);
    $cacheKey = $path . "\n" . implode("\n", $normalizedAllowed);
    if (array_key_exists($cacheKey, $cache)) {
        return $cache[$cacheKey];
    }
    if (!is_dir($path) || $normalizedAllowed === []) {
        $cache[$cacheKey] = 0;
        return 0;
    }

    $allowedLookup = array_fill_keys($normalizedAllowed, true);
    $logicalKeys = [];
    $rootPrefix = rtrim(str_replace('\\', '/', $path), '/') . '/';

    try {
        $iterator = new RecursiveIteratorIterator(
            new RecursiveDirectoryIterator($path, FilesystemIterator::SKIP_DOTS)
        );
        foreach ($iterator as $fileInfo) {
            if (!$fileInfo instanceof SplFileInfo || !$fileInfo->isFile()) {
                continue;
            }
            $pathname = str_replace('\\', '/', $fileInfo->getPathname());
            if (!str_starts_with($pathname, $rootPrefix)) {
                continue;
            }
            $relative = substr($pathname, strlen($rootPrefix));
            if (!is_string($relative) || $relative === '') {
                continue;
            }
            $logical = preg_replace('/\.gz$/i', '', $relative) ?? $relative;
            $extension = strtolower(pathinfo($logical, PATHINFO_EXTENSION));
            if ($extension === '') {
                continue;
            }
            $logicalSuffix = '.' . $extension;
            if (!isset($allowedLookup[$logicalSuffix])) {
                continue;
            }
            $logicalKey = preg_replace('/\.[^.\/]+$/', '', $logical) ?? $logical;
            if ($logicalKey !== '') {
                $logicalKeys[$logicalKey] = true;
            }
        }
    } catch (Throwable) {
        $logicalKeys = [];
    }

    $cache[$cacheKey] = count($logicalKeys);
    return $cache[$cacheKey];
}

function homepage_directory_matching_file_count(string $path, array $patterns = []): int
{
    static $cache = [];
    $cacheKey = $path . "\n" . implode("\n", $patterns);
    if (array_key_exists($cacheKey, $cache)) {
        return $cache[$cacheKey];
    }
    if (!is_dir($path)) {
        $cache[$cacheKey] = 0;
        return 0;
    }

    $count = 0;
    try {
        $iterator = new RecursiveIteratorIterator(
            new RecursiveDirectoryIterator($path, FilesystemIterator::SKIP_DOTS)
        );
        foreach ($iterator as $fileInfo) {
            if (!$fileInfo instanceof SplFileInfo || !$fileInfo->isFile()) {
                continue;
            }
            $pathname = $fileInfo->getPathname();
            if ($patterns === []) {
                $count += 1;
                continue;
            }
            // ダウンロード済み HTML や gzipped JSON など、拡張子規則が機能ごとに違うため正規表現で数える。
            foreach ($patterns as $pattern) {
                if (@preg_match($pattern, $pathname) === 1) {
                    $count += 1;
                    break;
                }
            }
        }
    } catch (Throwable) {
        $count = 0;
    }

    $cache[$cacheKey] = $count;
    return $count;
}

function homepage_feature_fallback_display(string $featureKey, array $feature, ?array $snapshotDisplay = null): ?array
{
    if ($featureKey === 'reiki') {
        $manifestPath = dirname((string)($feature['source_dir'] ?? '')) . DIRECTORY_SEPARATOR . 'source_manifest.json';
        $manifestCount = homepage_json_array_count_auto($manifestPath);
        $cleanHtmlCount = homepage_unique_logical_file_count((string)($feature['clean_html_dir'] ?? ''), ['.html', '.htm']);
        $downloadedCount = max($manifestCount, $cleanHtmlCount);
        $totalCount = max($manifestCount, $downloadedCount, $cleanHtmlCount);
        $detailLines = array_values(array_filter([
            homepage_progress_count_labeled_detail('DL済', $downloadedCount, $totalCount),
            homepage_progress_count_labeled_detail('HTML', $cleanHtmlCount, $totalCount),
        ]));
        if ($detailLines === []) {
            return null;
        }
        $isComplete = homepage_progress_count_is_complete($cleanHtmlCount, $totalCount);
        return [
            'label' => $isComplete ? '完了' : '反映状況',
            'class' => $isComplete ? 'task-done' : 'task-info',
            'detail' => implode("\n", $detailLines),
            'progress_current' => $cleanHtmlCount,
            'progress_total' => $totalCount > 0 ? $totalCount : null,
        ];
    }

    if ($featureKey === 'gijiroku') {
        $indexPath = (string)($feature['index_json_path'] ?? '');
        $downloadsDir = (string)($feature['downloads_dir'] ?? '');
        $totalCount = homepage_gijiroku_index_unique_count($indexPath);
        $downloadedCount = homepage_gijiroku_indexed_download_count($indexPath, $downloadsDir);
        $totalCount = max($totalCount, $downloadedCount);
        $detailLines = array_values(array_filter([
            homepage_progress_count_labeled_detail('DL済', $downloadedCount, $totalCount),
        ]));
        if ($detailLines === []) {
            return null;
        }
        $isComplete = homepage_progress_count_is_complete($downloadedCount, $totalCount);
        return [
            'label' => $isComplete ? '完了' : '取得状況',
            'class' => $isComplete ? 'task-done' : 'task-info',
            'detail' => implode("\n", $detailLines),
            'progress_current' => $downloadedCount,
            'progress_total' => $totalCount > 0 ? $totalCount : null,
        ];
    }

    return null;
}

function homepage_task_display_has_count_detail(?array $display): bool
{
    if (!is_array($display)) {
        return false;
    }
    $detail = trim((string)($display['detail'] ?? ''));
    if ($detail === '') {
        return false;
    }
    return preg_match('/\d+(?:\/\d+)?\s*件/u', $detail) === 1;
}

function homepage_task_display_attach_count_from_progress(array $display, array $sourceDisplay): array
{
    $current = $sourceDisplay['count_current'] ?? ($sourceDisplay['progress_current'] ?? null);
    $total = $sourceDisplay['count_total'] ?? ($sourceDisplay['progress_total'] ?? null);
    if (is_numeric($current) && is_numeric($total) && (int)$total > 0) {
        $display['count_current'] = max(0, min((int)$current, (int)$total));
        $display['count_total'] = max(0, (int)$total);
    }
    return $display;
}

function homepage_task_display_is_complete(?array $display): bool
{
    if (!is_array($display)) {
        return false;
    }

    if (trim((string)($display['label'] ?? '')) === '完了') {
        return true;
    }

    $current = $display['progress_current'] ?? null;
    $total = $display['progress_total'] ?? null;
    if ($current === null || $total === null) {
        return false;
    }

    return (int)$total > 0 && (int)$current >= (int)$total;
}

function homepage_task_display_is_index_waiting(?array $display): bool
{
    if (!is_array($display)) {
        return false;
    }
    if (trim((string)($display['label'] ?? '')) === 'インデックス待機中') {
        return true;
    }
    return str_contains((string)($display['detail'] ?? ''), 'インデックス待機中');
}

function homepage_task_display_should_hide(?array $display): bool
{
    if (!is_array($display)) {
        return false;
    }

    return trim((string)($display['class'] ?? '')) === 'task-failed'
        && !homepage_task_display_has_count_detail($display);
}

function homepage_unpublished_display_should_hide(?array $display): bool
{
    if (!is_array($display)) {
        return false;
    }

    // 未公開カードは件数が見えて初めて意味がある。
    // 実行中かどうかは上段の running board でも追えるので、ここでは件数の無いものを一律で隠す。
    return !homepage_task_display_has_count_detail($display);
}

function homepage_prefecture_label(array $municipality): string
{
    $fullName = trim((string)($municipality['full_name'] ?? ''));
    if ($fullName !== '' && preg_match('/^(.+?[都道府県])(?:\s|$)/u', $fullName, $matches) === 1) {
        return trim((string)($matches[1] ?? ''));
    }

    $name = trim((string)($municipality['name'] ?? ''));
    if ($name !== '' && preg_match('/.+?[都道府県]$/u', $name) === 1) {
        return $name;
    }

    return 'その他';
}

function homepage_prefecture_code(array $municipality): string
{
    $prefCode = trim((string)($municipality['pref_code'] ?? ''));
    if ($prefCode !== '') {
        return $prefCode;
    }
    return municipality_prefecture_code_from_code((string)($municipality['code'] ?? ''));
}

function homepage_prefecture_options_from_cards(array $municipalityCards): array
{
    $counts = [];
    foreach ($municipalityCards as $card) {
        if (!is_array($card)) {
            continue;
        }
        $prefCode = trim((string)($card['prefecture_code'] ?? ''));
        $prefectureLabel = trim((string)($card['prefecture_label'] ?? ''));
        if ($prefCode === '' || $prefectureLabel === '') {
            continue;
        }
        if (!isset($counts[$prefCode])) {
            $counts[$prefCode] = [
                'code' => $prefCode,
                'name' => $prefectureLabel,
                'count' => 0,
            ];
        }
        $counts[$prefCode]['count'] += 1;
    }

    ksort($counts, SORT_STRING);
    return array_values($counts);
}

function homepage_normalize_prefecture_filter(?string $value, array $prefectureOptions): string
{
    $requested = trim((string)$value);
    if ($requested === '' || $requested === 'all') {
        return '';
    }
    if (preg_match('/^\d{1,2}$/', $requested) === 1) {
        $requested = str_pad($requested, 2, '0', STR_PAD_LEFT);
    }
    foreach ($prefectureOptions as $option) {
        if (!is_array($option)) {
            continue;
        }
        $code = trim((string)($option['code'] ?? ''));
        $name = trim((string)($option['name'] ?? ''));
        if ($requested === $code || $requested === $name) {
            return $code;
        }
    }
    return '';
}

function homepage_filter_api_payload_by_prefecture(array $payload, ?string $prefecture): array
{
    $prefectureOptions = is_array($payload['prefectures'] ?? null) ? $payload['prefectures'] : [];
    $selectedCode = homepage_normalize_prefecture_filter($prefecture, $prefectureOptions);
    $selectedName = '';
    foreach ($prefectureOptions as $option) {
        if (is_array($option) && (string)($option['code'] ?? '') === $selectedCode) {
            $selectedName = (string)($option['name'] ?? '');
            break;
        }
    }

    if ($selectedCode === '') {
        $payload['selected_prefecture_code'] = '';
        $payload['selected_prefecture_name'] = '';
        $payload['display_municipality_count'] = is_array($payload['municipalities'] ?? null)
            ? count($payload['municipalities'])
            : 0;
        return $payload;
    }

    $municipalities = is_array($payload['municipalities'] ?? null) ? $payload['municipalities'] : [];
    $payload['municipalities'] = array_values(array_filter(
        $municipalities,
        static fn($card): bool => is_array($card) && (string)($card['prefecture_code'] ?? '') === $selectedCode
    ));
    $payload['selected_prefecture_code'] = $selectedCode;
    $payload['selected_prefecture_name'] = $selectedName;
    $payload['display_municipality_count'] = count($payload['municipalities']);
    return $payload;
}

function homepage_merge_task_display(?array $taskDisplay, ?array $fallbackDisplay): ?array
{
    if (!is_array($taskDisplay)) {
        return is_array($fallbackDisplay) ? $fallbackDisplay : null;
    }
    if (!is_array($fallbackDisplay)) {
        return $taskDisplay;
    }

    $taskHasCount = homepage_task_display_has_count_detail($taskDisplay);
    $taskCurrent = (int)($taskDisplay['progress_current'] ?? 0);
    $fallbackCurrent = (int)($fallbackDisplay['progress_current'] ?? 0);
    $preferFallbackCount = !$taskHasCount
        || $fallbackCurrent > $taskCurrent;
    $taskClass = trim((string)($taskDisplay['class'] ?? ''));
    $taskIsTransient = in_array($taskClass, ['task-running', 'task-stale'], true);
    if (!$preferFallbackCount && !$taskIsTransient) {
        $activityLines = homepage_task_display_metadata_lines($taskDisplay);
        if ($activityLines !== []) {
            $taskDisplay['detail'] = implode("\n", $activityLines);
        }
        return $taskDisplay;
    }

    // running 中の progress は「一覧収集」「更新確認」などの作業進捗にも使う。
    // DL済件数は DB/ファイル走査で復元した count_* として分けて合成する。
    $merged = $taskDisplay;
    $fallbackDetail = trim((string)($fallbackDisplay['detail'] ?? ''));
    $taskDetail = trim((string)($taskDisplay['detail'] ?? ''));
    $taskDetail = preg_replace('/(^| \/ )件数未集計(?= \/ |$)/u', '$1', $taskDetail) ?? $taskDetail;
    $taskDetail = trim(preg_replace('/\s*\/\s*\/\s*/u', ' / ', $taskDetail) ?? $taskDetail, ' /');
    $fallbackLines = homepage_task_display_count_lines($fallbackDisplay);
    if ($fallbackDetail !== '') {
        $taskLines = preg_split('/\R/u', $taskDetail, -1, PREG_SPLIT_NO_EMPTY) ?: [];
        if ($taskLines !== [] && preg_match('/\d+(?:\/\d+)?\s*件/u', trim((string)$taskLines[0])) === 1) {
            array_shift($taskLines);
            $taskDetail = implode("\n", $taskLines);
        }
    }
    if (($fallbackDisplay['label'] ?? '') === '完了' && !$taskIsTransient) {
        $merged['label'] = '完了';
        $merged['class'] = 'task-done';
        unset($merged['log_lines']);
    }
    if ($fallbackDetail !== '') {
        $mergedLines = $fallbackLines;
        foreach (homepage_task_display_metadata_lines($taskDisplay) as $line) {
            if (($fallbackDisplay['label'] ?? '') === '完了'
                && !$taskIsTransient
                && preg_match('/^(理由|失敗理由|終了コード|詳細ログ)/u', $line) === 1
            ) {
                continue;
            }
            if ($line !== '' && !in_array($line, $mergedLines, true)) {
                $mergedLines[] = $line;
            }
        }
        $merged['detail'] = implode("\n", $mergedLines);
    }
    $merged = homepage_task_display_attach_count_from_progress($merged, $fallbackDisplay);
    if ($taskIsTransient
        && is_numeric($taskDisplay['progress_current'] ?? null)
        && is_numeric($taskDisplay['progress_total'] ?? null)
        && (int)$taskDisplay['progress_total'] > 0
    ) {
        $merged['progress_current'] = $taskDisplay['progress_current'];
        $merged['progress_total'] = $taskDisplay['progress_total'];
    } else {
        $merged['progress_current'] = $fallbackDisplay['progress_current'] ?? null;
        $merged['progress_total'] = $fallbackDisplay['progress_total'] ?? null;
    }
    return $merged;
}

function homepage_task_display_detail_lines(?array $display): array
{
    if (!is_array($display)) {
        return [];
    }

    $detail = trim((string)($display['detail'] ?? ''));
    if ($detail === '') {
        return [];
    }

    $lines = preg_split('/\R/u', $detail, -1, PREG_SPLIT_NO_EMPTY) ?: [];
    $normalized = [];
    foreach ($lines as $line) {
        $line = trim((string)$line);
        if ($line === '' || in_array($line, $normalized, true)) {
            continue;
        }
        $normalized[] = $line;
    }
    return $normalized;
}

function homepage_task_display_metadata_lines(?array $display): array
{
    $lines = homepage_task_display_detail_lines($display);
    $metadata = [];
    foreach ($lines as $line) {
        $line = homepage_task_display_public_activity_line($line);
        if ($line === '') {
            continue;
        }
        if (in_array($line, $metadata, true)) {
            continue;
        }
        $metadata[] = $line;
    }
    if ($metadata === []) {
        return [];
    }
    return [end($metadata)];
}

function homepage_task_display_count_lines(?array $display): array
{
    $lines = homepage_task_display_detail_lines($display);
    $counts = [];
    $progressCurrent = $display['progress_current'] ?? null;
    $progressTotal = $display['progress_total'] ?? null;
    $hasProgress = is_numeric($progressCurrent) && is_numeric($progressTotal) && (int)$progressTotal > 0;
    foreach ($lines as $line) {
        $line = trim((string)$line);
        if ($line === '') {
            continue;
        }
        if (preg_match('/^(DL済|HTML|反映|投入済|追加済)\s+\d+(?:\/\d+)?\s*件$/u', $line, $matches) === 1) {
            if ($hasProgress) {
                $line = homepage_progress_count_labeled_detail(
                    (string)$matches[1],
                    (int)$progressCurrent,
                    (int)$progressTotal
                );
            }
        } elseif (preg_match('/^\d+(?:\/\d+)?\s*件$/u', $line) === 1) {
            if ($hasProgress) {
                $line = homepage_progress_count_detail((int)$progressCurrent, (int)$progressTotal);
            }
        } else {
            continue;
        }
        if (!in_array($line, $counts, true)) {
            $counts[] = $line;
        }
    }
    return $counts;
}

function homepage_task_display_public_activity_line(string $line): string
{
    $line = trim($line);
    if ($line === '' || $line === '件数未集計') {
        return '';
    }
    if (preg_match('/^\d+(?:\/\d+)?\s*件$/u', $line) === 1) {
        return '';
    }
    if (preg_match('/^(DL済|HTML|反映|投入済|追加済)\s+\d+(?:\/\d+)?\s*件$/u', $line) === 1) {
        return '';
    }
    if (preg_match('/^(更新|応答)\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}$/u', $line) === 1) {
        return '';
    }
    if (preg_match('/^警告あり\s+\d+件$/u', $line) === 1) {
        return '';
    }

    $prefix = '';
    $body = $line;
    if (preg_match('/^(作業|理由)\s+(.+)$/u', $line, $matches) === 1) {
        $prefix = (string)$matches[1];
        $body = trim((string)$matches[2]);
    }

    if (preg_match('/^\[\d+\/\d+\]\s*(.*)$/u', $body, $matches) === 1) {
        $body = trim((string)$matches[1]);
    }
    if ($body === '') {
        $body = '処理中';
    }
    if (preg_match('/\b(downloaded|checked|skipped|parsed|reused)=\d+\b/u', $body) === 1) {
        $body = '既存データを確認中';
    }
    if (preg_match('/^Found\s+\d+\s+(?:unique regulation IDs|ordinance pages)\b/i', $body) === 1) {
        $body = '例規一覧を確認中';
    }
    if ($prefix === '') {
        $prefix = '作業';
    }
    return $prefix . ' ' . $body;
}

function homepage_sanitize_home_card_display(?array $display): ?array
{
    if (!is_array($display)) {
        return null;
    }

    $detailLines = homepage_task_display_count_lines($display);
    $class = trim((string)($display['class'] ?? ''));
    if (in_array($class, ['task-running', 'task-stale', 'task-failed'], true)) {
        foreach (homepage_task_display_metadata_lines($display) as $line) {
            if ($line !== '' && !in_array($line, $detailLines, true)) {
                $detailLines[] = $line;
            }
        }
    }
    $display['detail'] = implode("\n", $detailLines);
    foreach ($detailLines as $line) {
        if (preg_match('/(?:^|\s)(?:DL済|HTML|反映|投入済|追加済)?\s*(\d+)\/(\d+)\s*件/u', $line, $matches) === 1) {
            $countCurrent = max(0, (int)$matches[1]);
            $countTotal = max(0, (int)$matches[2]);
            $display['count_current'] = min($countCurrent, $countTotal);
            $display['count_total'] = $countTotal;
            if (!in_array($class, ['task-running', 'task-stale'], true)
                || !is_numeric($display['progress_current'] ?? null)
                || !is_numeric($display['progress_total'] ?? null)
            ) {
                $display['progress_current'] = $display['count_current'];
                $display['progress_total'] = $display['count_total'];
            }
            break;
        }
    }
    return $display;
}

function homepage_task_display_warning_lines(?array $display): array
{
    if (!is_array($display) || !is_array($display['warning_lines'] ?? null)) {
        return [];
    }
    $lines = [];
    foreach ($display['warning_lines'] as $line) {
        $line = trim((string)$line);
        if ($line === '' || in_array($line, $lines, true)) {
            continue;
        }
        $lines[] = $line;
    }
    return $lines;
}

function homepage_search_index_cache_path(): string
{
    return data_path('background_tasks/search_indexed_slug_cache.json');
}

function homepage_feature_search_doc_type(string $featureKey): ?string
{
    return match ($featureKey) {
        'gijiroku' => 'minutes',
        'reiki' => 'reiki',
        default => null,
    };
}

function homepage_search_index_cache_ttl_seconds(): int
{
    // current alias の切替直後に長く古い表示を残さない範囲で、ホームAPIの連打からOpenSearchを守る。
    return 60;
}

function homepage_fetch_indexed_slugs_for_doc_type(string $docType): array
{
    $alias = miyabe_search_alias_for_type($docType);
    if ($alias === '') {
        return [];
    }

    $response = miyabe_search_http_request(
        'POST',
        '/' . rawurlencode($alias) . '/_search',
        [
            'size' => 0,
            'aggs' => [
                'slugs' => [
                    'terms' => [
                        'field' => 'slug',
                        'size' => 10000,
                    ],
                ],
            ],
        ]
    );

    $buckets = $response['aggregations']['slugs']['buckets'] ?? [];
    if (!is_array($buckets)) {
        return [];
    }

    $slugs = [];
    foreach ($buckets as $bucket) {
        if (!is_array($bucket)) {
            continue;
        }
        $slug = trim((string)($bucket['key'] ?? ''));
        $count = max(0, (int)($bucket['doc_count'] ?? 0));
        if ($slug !== '' && $count > 0) {
            $slugs[$slug] = $count;
        }
    }
    return $slugs;
}

function homepage_search_indexed_slug_sets(): array
{
    static $cachedSets = null;
    if (is_array($cachedSets)) {
        return $cachedSets;
    }

    $cachePath = homepage_search_index_cache_path();
    $cached = read_json_cache_file($cachePath, homepage_search_index_cache_ttl_seconds());
    if (is_array($cached) && is_array($cached['features'] ?? null)) {
        $cachedSets = $cached['features'];
        return $cachedSets;
    }

    $features = [];
    try {
        foreach (['gijiroku', 'reiki'] as $featureKey) {
            $docType = homepage_feature_search_doc_type($featureKey);
            $features[$featureKey] = $docType !== null
                ? homepage_fetch_indexed_slugs_for_doc_type($docType)
                : [];
        }
        write_json_cache_file($cachePath, [
            'generated_at' => app_now_tokyo(),
            'features' => $features,
        ]);
        $cachedSets = $features;
        return $cachedSets;
    } catch (Throwable $error) {
        error_log('[home_api] search index availability check failed: ' . $error->getMessage());
        $staleCached = read_json_cache_file($cachePath, 0);
        $cachedSets = is_array($staleCached) && is_array($staleCached['features'] ?? null)
            ? $staleCached['features']
            : [];
        return $cachedSets;
    }
}

function homepage_feature_search_indexed(string $featureKey, string $slug): bool
{
    $docType = homepage_feature_search_doc_type($featureKey);
    if ($docType === null) {
        return true;
    }

    $slug = trim($slug);
    if ($slug === '') {
        return false;
    }

    $sets = homepage_search_indexed_slug_sets();
    $featureSet = is_array($sets[$featureKey] ?? null) ? $sets[$featureKey] : [];
    return isset($featureSet[$slug]) && (int)$featureSet[$slug] > 0;
}

function homepage_feature_card_display(
    string $featureKey,
    array $feature,
    ?array $primaryDisplay,
    ?array $publishDisplay,
    ?array $snapshotDisplay,
    bool $hasData
): ?array {
    $fallbackDisplay = homepage_feature_fallback_display($featureKey, $feature, $snapshotDisplay);
    $statusDisplay = null;
    if (!$hasData && is_array($publishDisplay)) {
        $statusDisplay = $publishDisplay;
    } elseif (is_array($primaryDisplay)) {
        $statusDisplay = $primaryDisplay;
    } elseif (is_array($publishDisplay)) {
        $statusDisplay = $publishDisplay;
    } else {
        $statusDisplay = $fallbackDisplay;
    }

    if (!is_array($fallbackDisplay)) {
        return is_array($statusDisplay) ? $statusDisplay : null;
    }
    if (!is_array($statusDisplay)) {
        return $fallbackDisplay;
    }

    $statusClass = trim((string)($statusDisplay['class'] ?? ''));
    $statusIsTransient = in_array($statusClass, ['task-running', 'task-failed', 'task-stale'], true);

    $merged = $statusDisplay;
    if (!$statusIsTransient) {
        $merged['label'] = $fallbackDisplay['label'] ?? ($merged['label'] ?? '');
        $merged['class'] = $fallbackDisplay['class'] ?? ($merged['class'] ?? '');
    }

    $detailLines = homepage_task_display_count_lines($fallbackDisplay);
    foreach (homepage_task_display_metadata_lines($statusDisplay) as $line) {
        if (in_array($line, $detailLines, true)) {
            continue;
        }
        $detailLines[] = $line;
    }

    $warningLines = [];
    foreach ([$statusDisplay, $publishDisplay, $fallbackDisplay] as $warningDisplay) {
        foreach (homepage_task_display_warning_lines(is_array($warningDisplay) ? $warningDisplay : null) as $line) {
            if (!in_array($line, $warningLines, true)) {
                $warningLines[] = $line;
            }
        }
    }
    if ($warningLines !== []) {
        $hasWarningDetail = false;
        foreach ($detailLines as $line) {
            if (str_starts_with($line, '警告あり')) {
                $hasWarningDetail = true;
                break;
            }
        }
        if (!$hasWarningDetail) {
            $detailLines[] = '警告あり ' . (string)count($warningLines) . '件';
        }
        $merged['warning_lines'] = $warningLines;
    }
    if ($detailLines !== []) {
        $merged['detail'] = implode("\n", $detailLines);
    }
    $merged = homepage_task_display_attach_count_from_progress($merged, $fallbackDisplay);

    if ($statusIsTransient) {
        $progressCurrent = $statusDisplay['progress_current'] ?? null;
        $progressTotal = $statusDisplay['progress_total'] ?? null;
        if ($progressCurrent !== null && $progressTotal !== null) {
            $merged['progress_current'] = $progressCurrent;
            $merged['progress_total'] = $progressTotal;
            return $merged;
        }
    }

    $merged['progress_current'] = $fallbackDisplay['progress_current'] ?? null;
    $merged['progress_total'] = $fallbackDisplay['progress_total'] ?? null;
    return $merged;
}

function homepage_normalize_task_status_items(array $taskStatus): array
{
    $items = $taskStatus['items'] ?? null;
    if (!is_array($items)) {
        $taskStatus['items'] = [];
        return $taskStatus;
    }

    $normalizedItems = [];
    foreach ($items as $rawSlug => $item) {
        if (!is_array($item)) {
            continue;
        }

        // slug を配列キーと値の両方で揃え、描画側が片方だけ見ても壊れないようにする。
        $slug = trim((string)($item['slug'] ?? (string)$rawSlug));
        if ($slug === '') {
            continue;
        }

        $item['slug'] = $slug;
        $normalizedItems[$slug] = $item;
    }

    $taskStatus['items'] = $normalizedItems;
    return $taskStatus;
}

function homepage_feature_publish_task_key(string $featureKey): ?string
{
    return match ($featureKey) {
        'gijiroku' => 'gijiroku_reflect',
        'reiki' => 'reiki_reflect',
        default => null,
    };
}

function homepage_task_display_is_done_success(?array $display): bool
{
    if (!is_array($display)) {
        return false;
    }

    return trim((string)($display['class'] ?? '')) === 'task-done'
        && homepage_task_display_is_complete($display);
}

function homepage_feature_runtime_displays(
    string $featureKey,
    string $slug,
    array $feature,
    array $backgroundTaskStatuses,
    array $backgroundTaskSnapshots
): array {
    $taskDisplay = null;
    if (isset($backgroundTaskStatuses[$featureKey]) && is_array($backgroundTaskStatuses[$featureKey])) {
        $taskDisplay = background_task_item_display($backgroundTaskStatuses[$featureKey], $slug);
        if (homepage_task_display_is_index_waiting($taskDisplay)) {
            $taskDisplay = null;
        }
    }

    $snapshotDisplay = null;
    $fallbackDisplay = null;
    if (isset($backgroundTaskSnapshots[$featureKey]) && is_array($backgroundTaskSnapshots[$featureKey])) {
        $snapshotDisplay = background_task_item_display($backgroundTaskSnapshots[$featureKey], $slug);
        $fallbackDisplay = background_task_item_fallback_display($backgroundTaskSnapshots[$featureKey], $slug);
    }
    if ($fallbackDisplay === null) {
        $fallbackDisplay = homepage_feature_fallback_display($featureKey, $feature);
    }

    $primaryDisplay = $taskDisplay !== null
        ? homepage_merge_task_display($taskDisplay, $fallbackDisplay)
        : ($snapshotDisplay ?? $fallbackDisplay);
    if (homepage_task_display_should_hide($primaryDisplay)) {
        $primaryDisplay = null;
    }

    $publishDisplay = null;
    $publishTaskKey = homepage_feature_publish_task_key($featureKey);
    if ($publishTaskKey !== null && isset($backgroundTaskStatuses[$publishTaskKey])) {
        $publishDisplay = background_task_item_display($backgroundTaskStatuses[$publishTaskKey], $slug);
        if (homepage_task_display_should_hide($publishDisplay)) {
            $publishDisplay = null;
        }
    }

    return [
        'task' => $taskDisplay,
        'snapshot' => $snapshotDisplay,
        'fallback' => $fallbackDisplay,
        'primary' => $primaryDisplay,
        'publish' => $publishDisplay,
    ];
}

function homepage_feature_has_available_data(
    string $slug,
    string $featureKey,
    array $feature,
    ?array $primaryDisplay,
    ?array $publishDisplay
): bool {
    $hasData = (bool)($feature['has_data'] ?? false);
    if (!$hasData && homepage_task_display_is_done_success($publishDisplay)) {
        // 反映タスク自身が完了成功を返しているなら、その結果を公開可否の最優先根拠にする。
        $hasData = true;
    }
    if (!$hasData && $primaryDisplay !== null && homepage_task_display_is_complete($primaryDisplay)) {
        // 反映直後に municipality_catalog cache だけ古いときは、実ファイルを見て self-heal する。
        $hasData = municipality_feature_live_has_data_with_cache_heal($slug, $featureKey, $feature);
    }
    return $hasData;
}

function homepage_feature_target_codes(string $featureKey): array
{
    static $cache = [];
    if (array_key_exists($featureKey, $cache)) {
        return $cache[$featureKey];
    }

    $index = match ($featureKey) {
        'gijiroku' => load_system_url_index('municipalities/assembly_minutes_system_urls.tsv'),
        'reiki' => load_system_url_index('municipalities/reiki_system_urls.tsv'),
        default => [],
    };

    $codes = [];
    foreach ($index as $code => $row) {
        if (!is_array($row)) {
            continue;
        }
        if (trim((string)($row['url'] ?? '')) === '') {
            continue;
        }
        $codes[] = trim((string)$code);
    }
    $cache[$featureKey] = $codes;
    return $cache[$featureKey];
}

function homepage_build_feature_runtime_states(
    array $municipalities,
    array $featureLabels,
    array $backgroundTaskStatuses,
    array $backgroundTaskSnapshots
): array {
    $states = [];
    foreach ($municipalities as $slug => $municipality) {
        if (!is_array($municipality)) {
            continue;
        }

        $normalizedSlug = (string)$slug;
        foreach (array_keys($featureLabels) as $featureKey) {
            $feature = is_array($municipality[$featureKey] ?? null) ? $municipality[$featureKey] : [];
            $displays = homepage_feature_runtime_displays(
                $featureKey,
                $normalizedSlug,
                $feature,
                $backgroundTaskStatuses,
                $backgroundTaskSnapshots
            );
            $states[$normalizedSlug][$featureKey] = [
                'feature' => $feature,
                'displays' => $displays,
                'has_data' => homepage_feature_has_available_data(
                    $normalizedSlug,
                    $featureKey,
                    $feature,
                    $displays['primary'],
                    $displays['publish']
                ),
                'search_indexed' => homepage_feature_search_indexed($featureKey, $normalizedSlug),
            ];
        }
    }

    return $states;
}

function homepage_feature_summaries(
    array $municipalities,
    array $featureLabels,
    array $featureIcons,
    array $featureRuntimeStates
): array {
    $summaries = [];
    foreach (['gijiroku', 'reiki'] as $featureKey) {
        $targetCodes = array_values(array_filter(
            homepage_feature_target_codes($featureKey),
            static fn(mixed $code): bool => trim((string)$code) !== ''
        ));
        $targetLookup = array_fill_keys($targetCodes, true);
        $availableCount = 0;

        foreach ($municipalities as $slug => $municipality) {
            if (!is_array($municipality)) {
                continue;
            }
            $code = trim((string)($municipality['code'] ?? ''));
            if ($code === '' || !isset($targetLookup[$code])) {
                continue;
            }

            $runtimeState = $featureRuntimeStates[(string)$slug][$featureKey] ?? null;
            if (
                is_array($runtimeState)
                && !empty($runtimeState['has_data'])
                && !empty($runtimeState['search_indexed'])
            ) {
                $availableCount += 1;
            }
        }

        $label = (string)($featureLabels[$featureKey] ?? $featureKey);
        $icon = (string)($featureIcons[$featureKey] ?? '');
        $targetCount = count($targetLookup);
        $summaries[] = [
            'feature_key' => $featureKey,
            'label' => $label,
            'icon' => $icon,
            'target_count' => $targetCount,
            'available_count' => $availableCount,
            'text' => sprintf('%s %s: 対象 %d / 検索可能 %d', $icon, $label, $targetCount, $availableCount),
        ];
    }

    return $summaries;
}

function homepage_task_summary_append_stat(array &$stats, string $label, string $value): void
{
    $label = trim($label);
    $value = trim($value);
    if ($label === '' || $value === '') {
        return;
    }
    $stats[] = ['label' => $label, 'value' => $value];
}

function homepage_task_summary_int(array $taskStatus, string $key): ?int
{
    $value = $taskStatus[$key] ?? null;
    if ($value === null || $value === '') {
        return null;
    }
    return max(0, (int)$value);
}

function homepage_task_summary_start_text(array $taskStatus): string
{
    foreach (['last_started_at', 'started_at'] as $key) {
        $value = trim((string)($taskStatus[$key] ?? ''));
        if ($value !== '') {
            return $value;
        }
    }
    return '';
}

function homepage_task_summary_finish_text(array $taskStatus, ?array $fallbackStatus = null): string
{
    foreach ([$taskStatus, is_array($fallbackStatus) ? $fallbackStatus : []] as $status) {
        foreach (['last_finished_at', 'finished_at'] as $key) {
            $value = trim((string)($status[$key] ?? ''));
            if ($value !== '') {
                return $value;
            }
        }
    }
    return '';
}

function homepage_task_summary_append_run_time_stats(
    array &$stats,
    array $taskStatus,
    ?array $fallbackFinishedStatus = null,
    string $startKey = 'started_at',
    string $finishKey = 'finished_at',
    ?bool $isRunning = null
): void {
    $isRunning = $isRunning ?? (bool)($taskStatus['running'] ?? false);
    $start = '';
    foreach ([$taskStatus, is_array($fallbackFinishedStatus) ? $fallbackFinishedStatus : []] as $statusIndex => $status) {
        $keys = $startKey === 'started_at' || $statusIndex > 0
            ? ['last_started_at', 'started_at']
            : [$startKey, 'last_started_at', 'started_at'];
        foreach ($keys as $key) {
            $value = trim((string)($status[$key] ?? ''));
            if ($value !== '') {
                $start = $value;
                break 2;
            }
        }
    }
    if ($isRunning) {
        homepage_task_summary_append_stat($stats, '開始', $start);
        return;
    }

    $finish = '';
    foreach ([$taskStatus, is_array($fallbackFinishedStatus) ? $fallbackFinishedStatus : []] as $status) {
        foreach (['last_finished_at', $finishKey, 'finished_at'] as $key) {
            $value = trim((string)($status[$key] ?? ''));
            if ($value !== '') {
                $finish = $value;
                break 2;
            }
        }
    }
    homepage_task_summary_append_stat($stats, '完了', $finish);
}

function homepage_search_rebuild_total_count_cache_path(): string
{
    return data_path('background_tasks/search_rebuild_total_count.json');
}

function homepage_search_rebuild_total_count_fallback(): int
{
    $cached = read_json_cache_file(homepage_search_rebuild_total_count_cache_path(), 0);
    return is_array($cached) ? max(0, (int)($cached['total_count'] ?? 0)) : 0;
}

function homepage_search_rebuild_current_slug_count_cache_path(): string
{
    return data_path('background_tasks/search_rebuild_current_slug_count.json');
}

function homepage_search_rebuild_current_slug_count_fallback(array $taskStatus): array
{
    $cached = read_json_cache_file(homepage_search_rebuild_current_slug_count_cache_path(), 0);
    if (!is_array($cached)) {
        return ['processed_count' => 0, 'total_count' => 0];
    }
    $slug = trim((string)($taskStatus['current_slug'] ?? ''));
    $stage = trim((string)($taskStatus['current_stage'] ?? ''));
    if ($slug !== '' && trim((string)($cached['slug'] ?? '')) !== $slug) {
        return ['processed_count' => 0, 'total_count' => 0];
    }
    if ($stage !== '' && trim((string)($cached['stage'] ?? '')) !== $stage) {
        return ['processed_count' => 0, 'total_count' => 0];
    }
    $runId = trim((string)($taskStatus['run_id'] ?? ''));
    if ($runId !== '' && trim((string)($cached['run_id'] ?? '')) !== $runId) {
        return ['processed_count' => 0, 'total_count' => 0];
    }
    return [
        'processed_count' => max(0, (int)($cached['processed_count'] ?? 0)),
        'total_count' => max(0, (int)($cached['total_count'] ?? 0)),
    ];
}

function homepage_opensearch_count(string $indexOrAlias, array $query): int
{
    $indexOrAlias = trim($indexOrAlias);
    if ($indexOrAlias === '') {
        return 0;
    }
    try {
        $response = miyabe_search_http_request(
            'POST',
            '/' . rawurlencode($indexOrAlias) . '/_count',
            ['query' => $query]
        );
    } catch (Throwable) {
        return 0;
    }
    return max(0, (int)($response['count'] ?? 0));
}

function homepage_search_rebuild_visible_index_count(array $taskStatus): int
{
    $indexName = trim((string)($taskStatus['current_index'] ?? ''));
    if ($indexName === '') {
        return 0;
    }
    return homepage_opensearch_count($indexName, ['match_all' => new stdClass()]);
}

function homepage_search_rebuild_current_slug_total(array $taskStatus): int
{
    $slug = trim((string)($taskStatus['current_slug'] ?? ''));
    $stage = trim((string)($taskStatus['current_stage'] ?? ''));
    if ($slug === '') {
        return 0;
    }
    $feature = municipality_feature($slug, $stage === 'reiki' ? 'reiki' : 'gijiroku') ?? [];
    if ($stage === 'reiki') {
        $count = homepage_unique_logical_file_count((string)($feature['clean_html_dir'] ?? ''), ['.html', '.htm']);
        return $count > 0
            ? $count
            : homepage_unique_logical_file_count(data_path('reiki/' . $slug . '/html'), ['.html', '.htm']);
    }
    $count = homepage_unique_logical_file_count((string)($feature['downloads_dir'] ?? ''), ['.txt', '.html', '.htm']);
    return $count > 0
        ? $count
        : homepage_unique_logical_file_count(work_path('gijiroku/' . $slug . '/downloads'), ['.txt', '.html', '.htm']);
}

function homepage_search_rebuild_activity_detail(array $searchRebuildStatus): string
{
    $title = background_task_compact_detail_text((string)($searchRebuildStatus['current_document_title'] ?? ''));
    if ($title === '') {
        return '';
    }
    $stage = trim((string)($searchRebuildStatus['current_stage'] ?? ''));
    $prefix = match ($stage) {
        'minutes' => '会議録投入',
        'reiki' => '例規集投入',
        default => '投入',
    };
    return $prefix . ' ' . $title;
}

function homepage_task_summary_feature_counts(
    array $municipalities,
    string $featureKey,
    array $featureRuntimeStates,
    string $mode
): ?array {
    $featureKey = trim($featureKey);
    $mode = trim($mode);
    if ($featureKey === '' || $mode === '') {
        return null;
    }

    $targetCodes = array_values(array_filter(
        homepage_feature_target_codes($featureKey),
        static fn(mixed $code): bool => trim((string)$code) !== ''
    ));
    if ($targetCodes === []) {
        return null;
    }
    $targetLookup = array_fill_keys($targetCodes, true);

    $targetCount = 0;
    $completeCount = 0;
    foreach ($municipalities as $slug => $municipality) {
        if (!is_array($municipality)) {
            continue;
        }
        $code = trim((string)($municipality['code'] ?? ''));
        if ($code === '' || !isset($targetLookup[$code])) {
            continue;
        }
        $slug = trim((string)$slug);
        if ($slug === '') {
            continue;
        }

        $targetCount += 1;
        $runtimeState = $featureRuntimeStates[$slug][$featureKey] ?? null;
        if (!is_array($runtimeState)) {
            continue;
        }

        $displays = is_array($runtimeState['displays'] ?? null) ? $runtimeState['displays'] : [];
        $primaryDisplay = is_array($displays['primary'] ?? null) ? $displays['primary'] : null;
        $publishDisplay = is_array($displays['publish'] ?? null) ? $displays['publish'] : null;
        $isComplete = match ($mode) {
            'primary_complete' => homepage_task_display_is_complete($primaryDisplay),
            'feature_available' => (bool)($runtimeState['has_data'] ?? false),
            'publish_complete' => homepage_task_display_is_complete($publishDisplay),
            'runtime_complete' => (
                (bool)($runtimeState['has_data'] ?? false)
                || homepage_task_display_is_complete($primaryDisplay)
                || homepage_task_display_is_complete($publishDisplay)
            ),
            default => false,
        };
        if ($isComplete) {
            $completeCount += 1;
        }
    }

    if ($targetCount <= 0) {
        return null;
    }

    return [
        'total' => $targetCount,
        'complete' => $completeCount,
        'incomplete' => max(0, $targetCount - $completeCount),
    ];
}

function homepage_feature_search_index_counts(
    array $municipalities,
    string $featureKey,
    array $featureRuntimeStates
): array {
    $targetCodes = array_values(array_filter(
        homepage_feature_target_codes($featureKey),
        static fn(mixed $code): bool => trim((string)$code) !== ''
    ));
    $targetLookup = array_fill_keys($targetCodes, true);
    $addableCount = 0;
    $indexedCount = 0;
    foreach ($municipalities as $slug => $municipality) {
        if (!is_array($municipality)) {
            continue;
        }
        $code = trim((string)($municipality['code'] ?? ''));
        if ($code === '' || !isset($targetLookup[$code])) {
            continue;
        }
        $runtimeState = $featureRuntimeStates[(string)$slug][$featureKey] ?? null;
        if (!is_array($runtimeState) || empty($runtimeState['has_data'])) {
            continue;
        }
        // インデックス更新の母数は、全対象自治体ではなく、
        // 部分取得を含めて検索インデックスに追加できるデータを持つ自治体にする。
        $addableCount += 1;
        if (!empty($runtimeState['search_indexed'])) {
            $indexedCount += 1;
        }
    }
    return ['complete' => $indexedCount, 'total' => $addableCount];
}

function homepage_task_status_index_state(array $taskStatus): array
{
    if (background_task_is_stale($taskStatus)) {
        return ['停止の可能性', 'task-summary-stale'];
    }
    $active = max(0, (int)($taskStatus['index_active_count'] ?? 0));
    $queue = max(0, (int)($taskStatus['index_queue_count'] ?? 0));
    if ($active > 0 || $queue > 0) {
        return ['実行中', 'task-summary-running'];
    }
    return ['待機中', 'task-summary-idle'];
}

function homepage_scraper_index_summary(
    array $taskStatus,
    string $featureKey,
    array $featureIcons,
    array $featureRuntimeStates,
    array $municipalities
): array {
    $featureLabel = $featureKey === 'reiki' ? '例規集' : '会議録';
    [$stateLabel, $stateClass] = homepage_task_status_index_state($taskStatus);
    $capacity = homepage_task_summary_int($taskStatus, 'index_capacity') ?? 1;
    $active = homepage_task_summary_int($taskStatus, 'index_active_count') ?? 0;
    $counts = homepage_feature_search_index_counts($municipalities, $featureKey, $featureRuntimeStates);

    $stats = [];
    homepage_task_summary_append_stat($stats, '稼働', max(0, $active) . '/' . max(1, $capacity));
    if ((int)$counts['total'] > 0) {
        $completeCount = (int)$counts['complete'];
        $totalCount = (int)$counts['total'];
        homepage_task_summary_append_stat($stats, '検索可', $completeCount . '/' . $totalCount);
    }
    homepage_task_summary_append_run_time_stats(
        $stats,
        $taskStatus,
        null,
        'index_started_at',
        'index_finished_at',
        $stateClass === 'task-summary-running'
    );

    return [
        'label' => $featureLabel . ' インデックス更新',
        'icon' => (string)($featureIcons[$featureKey] ?? ''),
        'state_label' => $stateLabel,
        'state_class' => $stateClass,
        'stats' => $stats,
        'tasks' => [],
    ];
}

function homepage_background_task_summary(
    array $taskStatus,
    array $taskDefinition,
    array $featureIcons,
    array $featureRuntimeStates,
    array $municipalities
): ?array
{
    if ($taskStatus === []) {
        return null;
    }

    $running = (bool)($taskStatus['running'] ?? false);
    $stale = background_task_is_stale($taskStatus);
    $showWhenIdle = (bool)($taskDefinition['show_when_idle'] ?? true);
    if (!$running && !$stale && !$showWhenIdle) {
        return null;
    }

    $taskKey = trim((string)($taskDefinition['task_key'] ?? ''));
    $featureKey = (string)($taskDefinition['feature_key'] ?? '');
    $label = trim((string)($taskDefinition['summary_label'] ?? ($taskDefinition['running_label'] ?? $featureKey)));
    if ($label === '') {
        return null;
    }

    $workerCapacity = homepage_task_summary_int($taskStatus, 'worker_capacity');
    if ($workerCapacity === null) {
        $fallbackCapacity = $taskDefinition['default_worker_capacity'] ?? null;
        if ($fallbackCapacity !== null && $fallbackCapacity !== '') {
            $workerCapacity = max(0, (int)$fallbackCapacity);
        }
    }
    $workerActive = homepage_task_summary_int($taskStatus, 'worker_active_count');
    if ($workerActive === null) {
        $workerActive = $running ? homepage_task_summary_int($taskStatus, 'active_count') : 0;
    }
    $workerIdle = homepage_task_summary_int($taskStatus, 'worker_idle_count');
    if ($workerIdle === null && $workerCapacity !== null && $workerActive !== null) {
        $workerIdle = max(0, $workerCapacity - $workerActive);
    }

    $indexCapacity = homepage_task_summary_int($taskStatus, 'index_capacity');
    $indexActive = homepage_task_summary_int($taskStatus, 'index_active_count');
    $indexIdle = homepage_task_summary_int($taskStatus, 'index_idle_count');
    if ($indexIdle === null && $indexCapacity !== null && $indexActive !== null) {
        $indexIdle = max(0, $indexCapacity - $indexActive);
    }
    $indexQueue = homepage_task_summary_int($taskStatus, 'index_queue_count');
    $processedCount = homepage_task_summary_int($taskStatus, 'processed_count');
    $publishedSlugCount = homepage_task_summary_int($taskStatus, 'published_slug_count');
    $currentMunicipalityName = trim((string)($taskStatus['current_municipality_name'] ?? ''));
    $currentSlug = trim((string)($taskStatus['current_slug'] ?? ''));
    $pendingCount = homepage_task_summary_int($taskStatus, 'pending_count') ?? 0;
    $completedCount = homepage_task_summary_int($taskStatus, 'completed_count') ?? 0;
    $totalCount = homepage_task_summary_int($taskStatus, 'total_count') ?? 0;
    if ($taskKey === 'search_rebuild' && $totalCount <= 0) {
        $totalCount = homepage_search_rebuild_total_count_fallback();
    }
    $completedLabel = trim((string)($taskDefinition['completed_stat_label'] ?? '完了'));
    if ($completedLabel === '') {
        $completedLabel = '完了';
    }
    $completionMode = trim((string)($taskDefinition['completion_stat_mode'] ?? ''));
    if ($completionMode !== '') {
        $featureCounts = homepage_task_summary_feature_counts(
            $municipalities,
            $featureKey,
            $featureRuntimeStates,
            $completionMode
        );
        if (is_array($featureCounts)) {
            $completedCount = (int)($featureCounts['complete'] ?? 0);
            $totalCount = (int)($featureCounts['total'] ?? 0);
        }
    }
    $pendingLabel = '未着手';
    $pendingDisplayCount = $pendingCount;
    $pendingMode = trim((string)($taskDefinition['pending_stat_mode'] ?? ''));
    if ($pendingMode !== '') {
        $pendingLabel = trim((string)($taskDefinition['pending_stat_label'] ?? '未反映'));
        if ($pendingLabel === '') {
            $pendingLabel = '未反映';
        }
        $featureCounts = homepage_task_summary_feature_counts(
            $municipalities,
            $featureKey,
            $featureRuntimeStates,
            $pendingMode
        );
        if (is_array($featureCounts)) {
            $pendingDisplayCount = (int)($featureCounts['incomplete'] ?? 0);
        }
    }

    $stats = [];
    $compactWorkerStats = (bool)($taskDefinition['compact_worker_stats'] ?? false);
    if ($compactWorkerStats) {
        if ($workerActive !== null && $workerCapacity !== null) {
            homepage_task_summary_append_stat($stats, '稼働', $workerActive . '/' . $workerCapacity);
        } elseif ($workerActive !== null) {
            homepage_task_summary_append_stat($stats, '稼働', (string)$workerActive);
        } elseif ($workerCapacity !== null) {
            homepage_task_summary_append_stat($stats, '最大', (string)$workerCapacity);
        }
    } else {
        if ($workerActive !== null) {
            homepage_task_summary_append_stat($stats, '稼働', (string)$workerActive);
        }
        if ($workerIdle !== null) {
            homepage_task_summary_append_stat($stats, '空き', (string)$workerIdle);
        }
        if ($workerCapacity !== null) {
            homepage_task_summary_append_stat($stats, '最大', (string)$workerCapacity);
        }
    }
    $showCurrentStat = (bool)($taskDefinition['show_current_stat'] ?? true);
    $showIndexStats = (bool)($taskDefinition['show_index_stats'] ?? true);
    $showProcessedStat = (bool)($taskDefinition['show_processed_stat'] ?? true);
    $showPublishedStat = (bool)($taskDefinition['show_published_stat'] ?? true);
    $showPendingStat = (bool)($taskDefinition['show_pending_stat'] ?? true);
    if ($currentMunicipalityName !== '' && $showCurrentStat) {
        homepage_task_summary_append_stat($stats, '処理中', $currentMunicipalityName);
    } elseif ($currentSlug !== '' && $showCurrentStat) {
        homepage_task_summary_append_stat($stats, '処理中', $currentSlug);
    }
    if ($indexCapacity !== null && $indexActive !== null && $showIndexStats) {
        homepage_task_summary_append_stat($stats, '反映', $indexActive . '/' . $indexCapacity);
    }
    if ((($indexQueue ?? 0) > 0 || ($indexCapacity !== null && $running)) && $showIndexStats) {
        homepage_task_summary_append_stat($stats, '反映待ち', (string)($indexQueue ?? 0));
    }
    if ($processedCount !== null && ($processedCount > 0 || $running) && $showProcessedStat) {
        homepage_task_summary_append_stat($stats, '投入', (string)$processedCount);
    }
    if ($publishedSlugCount !== null && ($publishedSlugCount > 0 || $running) && $showPublishedStat) {
        homepage_task_summary_append_stat($stats, '検索可', (string)$publishedSlugCount);
    }
    if (($pendingDisplayCount > 0 || $running) && $showPendingStat) {
        homepage_task_summary_append_stat($stats, $pendingLabel, (string)$pendingDisplayCount);
    }
    if ($taskKey === 'search_rebuild') {
        $completedCount = min(
            count($municipalities),
            max(0, (int)($taskStatus['published_municipality_count'] ?? $publishedSlugCount ?? 0))
        );
        $totalCount = count($municipalities);
    }
    if ($totalCount > 0) {
        homepage_task_summary_append_stat($stats, $completedLabel, $completedCount . '/' . $totalCount);
    }
    homepage_task_summary_append_run_time_stats($stats, $taskStatus, null, 'started_at', 'finished_at', $running && !$stale);
    if ($stats === []) {
        return null;
    }

    if ($stale) {
        $stateLabel = '停止の可能性';
        $stateClass = 'task-summary-stale';
    } elseif ($running) {
        $stateLabel = '実行中';
        $stateClass = 'task-summary-running';
    } else {
        $stateLabel = '待機中';
        $stateClass = 'task-summary-idle';
    }

    return [
        'task_key' => $taskKey,
        'feature_key' => $featureKey,
        'label' => $label,
        'icon' => (string)($featureIcons[$featureKey] ?? ''),
        'state_label' => $stateLabel,
        'state_class' => $stateClass,
        'stats' => $stats,
        'index_summary' => in_array($taskKey, ['gijiroku', 'reiki'], true)
            ? homepage_scraper_index_summary($taskStatus, $featureKey, $featureIcons, $featureRuntimeStates, $municipalities)
            : null,
    ];
}

function homepage_background_task_summaries(
    array $runningTaskDefinitions,
    array $backgroundTaskStatuses,
    array $featureIcons,
    array $featureRuntimeStates,
    array $municipalities
): array {
    $summaries = [];
    foreach ($runningTaskDefinitions as $taskDefinition) {
        if (!is_array($taskDefinition)) {
            continue;
        }
        $taskKey = trim((string)($taskDefinition['task_key'] ?? ''));
        if ($taskKey === '') {
            continue;
        }
        $taskStatus = $backgroundTaskStatuses[$taskKey] ?? null;
        if (!is_array($taskStatus)) {
            continue;
        }
        $summary = homepage_background_task_summary(
            $taskStatus,
            $taskDefinition,
            $featureIcons,
            $featureRuntimeStates,
            $municipalities
        );
        if ($summary !== null) {
            $summaries[] = $summary;
        }
    }
    return $summaries;
}

function homepage_task_item_is_index_activity(array $item): bool
{
    $message = trim((string)($item['message'] ?? ''));
    $indexStatus = trim((string)($item['index_status'] ?? ''));
    return str_contains($message, 'インデックス')
        || ($indexStatus !== '' && $indexStatus !== 'pending');
}

function homepage_collect_visible_features(
    array $municipality,
    string $slug,
    array $featureLabels,
    array $featureIcons,
    array $backgroundTaskStatuses,
    array $backgroundTaskSnapshots,
    array $featureRuntimeStates = []
): array {
    $visibleFeatures = [];
    $readyVisibleCount = 0;

    foreach ($featureLabels as $featureKey => $label) {
        $runtimeState = $featureRuntimeStates[$featureKey] ?? null;
        $feature = is_array($runtimeState['feature'] ?? null)
            ? $runtimeState['feature']
            : (is_array($municipality[$featureKey] ?? null) ? $municipality[$featureKey] : []);
        $featureTitle = (string)($feature['title'] ?? (($municipality['name'] ?? $slug) . $label));
        $displays = is_array($runtimeState['displays'] ?? null)
            ? $runtimeState['displays']
            : homepage_feature_runtime_displays(
                $featureKey,
                $slug,
                $feature,
                $backgroundTaskStatuses,
                $backgroundTaskSnapshots
            );
        $primaryDisplay = $displays['primary'];
        $publishDisplay = $displays['publish'];

        $hasData = is_array($runtimeState) && array_key_exists('has_data', $runtimeState)
            ? (bool)$runtimeState['has_data']
            : homepage_feature_has_available_data($slug, $featureKey, $feature, $primaryDisplay, $publishDisplay);
        $searchIndexed = is_array($runtimeState) && array_key_exists('search_indexed', $runtimeState)
            ? (bool)$runtimeState['search_indexed']
            : homepage_feature_search_indexed($featureKey, $slug);
        $isSearchBacked = homepage_feature_search_doc_type($featureKey) !== null;
        $isEnabled = $hasData && (!$isSearchBacked || $searchIndexed);
        $display = homepage_feature_card_display(
            $featureKey,
            $feature,
            $primaryDisplay,
            $publishDisplay,
            is_array($displays['snapshot'] ?? null) ? $displays['snapshot'] : null,
            $hasData
        );

        $needsPublish = !$hasData && (
            homepage_task_display_is_complete($primaryDisplay)
            || $publishDisplay !== null
        );
        if (!$hasData && homepage_unpublished_display_should_hide($display)) {
            continue;
        }
        if (!$hasData && $display === null) {
            continue;
        }

        // 公開中のデータがあるものと、まだ未公開でも進捗を見せたいものだけを残す。
        if ($isEnabled) {
            $statusLabel = '利用可能';
            $statusClass = 'status-ready';
            $mode = 'link';
            $readyVisibleCount += 1;
        } elseif ($hasData && $isSearchBacked && !$searchIndexed) {
            $statusLabel = '検索準備中';
            $statusClass = 'status-needs-build';
            $mode = 'disabled';
        } elseif ($hasData) {
            $statusLabel = '休止中';
            $statusClass = 'status-suspended';
            $mode = 'disabled';
        } elseif ($needsPublish) {
            $statusLabel = '要反映';
            $statusClass = 'status-needs-build';
            $mode = 'disabled';
        } else {
            $statusLabel = '未公開';
            $statusClass = 'status-unpublished';
            $mode = 'disabled';
        }

        $visibleFeatures[] = [
            'feature_key' => $featureKey,
            'label' => $label,
            'icon' => (string)($featureIcons[$featureKey] ?? ''),
            'feature' => $feature,
            'title' => $featureTitle,
            'display' => $display,
            'status_label' => $statusLabel,
            'status_class' => $statusClass,
            'mode' => $mode,
        ];
    }

    $availableSummary = implode(' / ', array_map(
        static fn(array $item): string => (string)$item['label'],
        $visibleFeatures
    ));

    return [
        'visible_features' => $visibleFeatures,
        'ready_visible_count' => $readyVisibleCount,
        'available_summary' => $availableSummary,
    ];
}

function homepage_build_context(): array
{
    $municipalities = municipality_catalog();
    $featureLabels = [
        'boards' => '掲示板',
        'gijiroku' => '会議録',
        'reiki' => '例規集',
    ];
    $featureIcons = [
        'boards' => '🗳️',
        'gijiroku' => '🏛️',
        'reiki' => '⚖️',
    ];
    $backgroundTaskStatuses = [
        'gijiroku' => homepage_normalize_task_status_items(load_background_task_status('gijiroku')),
        'reiki' => homepage_normalize_task_status_items(load_background_task_status('reiki')),
        'search_rebuild' => homepage_normalize_task_status_items(load_background_task_status('search_rebuild')),
        'gijiroku_reflect' => homepage_normalize_task_status_items(load_background_task_status('gijiroku_reflect')),
        'reiki_reflect' => homepage_normalize_task_status_items(load_background_task_status('reiki_reflect')),
        'gijiroku_rebuild' => homepage_normalize_task_status_items(load_background_task_status('gijiroku_rebuild')),
    ];
    $backgroundTaskSnapshots = [
        'gijiroku' => homepage_normalize_task_status_items(load_background_task_status('gijiroku_snapshot')),
        'reiki' => homepage_normalize_task_status_items(load_background_task_status('reiki_snapshot')),
    ];
    $featureRuntimeStates = homepage_build_feature_runtime_states(
        $municipalities,
        $featureLabels,
        $backgroundTaskStatuses,
        $backgroundTaskSnapshots
    );
    $runningTaskDefinitions = [
        [
            'task_key' => 'gijiroku',
            'feature_key' => 'gijiroku',
            'running_label' => '会議録 スクレイピング',
            'summary_label' => '会議録 スクレイピング',
            'default_worker_capacity' => 3,
            'compact_worker_stats' => true,
            'show_index_stats' => false,
            'show_pending_stat' => false,
            'pending_stat_mode' => 'primary_complete',
            'pending_stat_label' => '未取得',
            'completion_stat_mode' => 'primary_complete',
            'completed_stat_label' => '取得完了',
        ],
        [
            'task_key' => 'reiki',
            'feature_key' => 'reiki',
            'running_label' => '例規集 スクレイピング',
            'summary_label' => '例規集 スクレイピング',
            'default_worker_capacity' => 3,
            'compact_worker_stats' => true,
            'show_index_stats' => false,
            'show_pending_stat' => false,
            'pending_stat_mode' => 'primary_complete',
            'pending_stat_label' => '未取得',
            'completion_stat_mode' => 'primary_complete',
            'completed_stat_label' => '取得完了',
        ],
        [
            'task_key' => 'gijiroku_rebuild',
            'feature_key' => 'gijiroku',
            'running_label' => '会議録 再構築',
            'summary_label' => '会議録 再構築',
            'show_when_idle' => false,
            'default_worker_capacity' => 4,
            'pending_stat_mode' => 'runtime_complete',
            'pending_stat_label' => '未反映',
        ],
    ];
    $taskStateSummaries = homepage_background_task_summaries(
        $runningTaskDefinitions,
        $backgroundTaskStatuses,
        $featureIcons,
        $featureRuntimeStates,
        $municipalities
    );

    uasort($municipalities, static function (array $a, array $b): int {
        $ca = (string)($a['code'] ?? '');
        $cb = (string)($b['code'] ?? '');
        if ($ca === '' && $cb === '') {
            return 0;
        }
        if ($ca === '') {
            return 1;
        }
        if ($cb === '') {
            return -1;
        }
        return strcmp($ca, $cb);
    });

    $displayMunicipalities = [];
    foreach ($municipalities as $slug => $municipality) {
        // カード表示可否は visible_features を作った結果だけで決める。
        // ポスター掲示場・会議録・例規集の三つがすべて非表示なら、自治体カード自体も出さない。
        $summary = homepage_collect_visible_features(
            $municipality,
            (string)$slug,
            $featureLabels,
            $featureIcons,
            $backgroundTaskStatuses,
            $backgroundTaskSnapshots,
            is_array($featureRuntimeStates[(string)$slug] ?? null) ? $featureRuntimeStates[(string)$slug] : []
        );
        if (($summary['visible_features'] ?? []) === []) {
            continue;
        }
        $displayMunicipalities[] = [
            'slug' => (string)$slug,
            'municipality' => $municipality,
            'visible_features' => $summary['visible_features'],
            'ready_visible_count' => $summary['ready_visible_count'],
            'available_summary' => $summary['available_summary'],
        ];
    }

    $runningTaskEntries = [];
    $runningTaskOrder = [];
    foreach ($runningTaskDefinitions as $definitionIndex => $taskDefinition) {
        if (!is_array($taskDefinition)) {
            continue;
        }
        $taskKey = trim((string)($taskDefinition['task_key'] ?? ''));
        if ($taskKey === '') {
            continue;
        }
        $runningTaskOrder[$taskKey] = $definitionIndex;
    }
    foreach ($runningTaskDefinitions as $taskDefinition) {
        $taskKey = (string)($taskDefinition['task_key'] ?? '');
        $featureKey = (string)($taskDefinition['feature_key'] ?? '');
        $featureLabel = (string)($taskDefinition['running_label'] ?? ($featureLabels[$featureKey] ?? $featureKey));
        $taskStatus = $backgroundTaskStatuses[$taskKey] ?? [];
        $items = $taskStatus['items'] ?? null;
        if (!is_array($items)) {
            continue;
        }

        foreach ($items as $slug => $item) {
            if (!is_array($item) || trim((string)($item['status'] ?? '')) !== 'running') {
                continue;
            }

            $normalizedSlug = (string)$slug;
            $municipality = is_array($municipalities[$normalizedSlug] ?? null) ? $municipalities[$normalizedSlug] : [];
            $runtimeState = is_array($featureRuntimeStates[$normalizedSlug][$featureKey] ?? null)
                ? $featureRuntimeStates[$normalizedSlug][$featureKey]
                : null;
            $feature = is_array($runtimeState['feature'] ?? null)
                ? $runtimeState['feature']
                : (is_array($municipality[$featureKey] ?? null) ? $municipality[$featureKey] : []);
            $taskDisplay = background_task_item_display($taskStatus, $normalizedSlug);
            if (homepage_task_display_is_index_waiting($taskDisplay)) {
                continue;
            }
            $isIndexActivity = homepage_task_item_is_index_activity($item);
            $snapshotDisplay = is_array($runtimeState['displays']['snapshot'] ?? null)
                ? $runtimeState['displays']['snapshot']
                : null;
            $display = $isIndexActivity
                ? $taskDisplay
                : homepage_feature_card_display(
                    $featureKey,
                    $feature,
                    $taskDisplay,
                    null,
                    $snapshotDisplay,
                    (bool)($runtimeState['has_data'] ?? false)
                );
            if (!is_array($display) || ($display['class'] ?? '') !== 'task-running') {
                continue;
            }

            $runningTaskEntries[] = [
                'slug' => $normalizedSlug,
                'municipality_name' => (string)($municipality['name'] ?? ($item['name'] ?? $slug)),
                'feature_key' => $featureKey,
                'feature_label' => $featureLabel,
                'task_key' => $taskKey,
                'task_area' => $isIndexActivity ? 'index' : 'scrape',
                'task_order' => (int)($runningTaskOrder[$taskKey] ?? PHP_INT_MAX),
                'feature_icon' => (string)($featureIcons[$featureKey] ?? ''),
                'display' => $display,
            ];
        }
    }

    usort($runningTaskEntries, static function (array $a, array $b): int {
        $taskOrderCompare = ((int)($a['task_order'] ?? PHP_INT_MAX)) <=> ((int)($b['task_order'] ?? PHP_INT_MAX));
        if ($taskOrderCompare !== 0) {
            return $taskOrderCompare;
        }
        return strcmp((string)($a['municipality_name'] ?? ''), (string)($b['municipality_name'] ?? ''));
    });

    return [
        'municipalities' => $municipalities,
        'displayMunicipalities' => $displayMunicipalities,
        'runningTaskEntries' => $runningTaskEntries,
        'featureLabels' => $featureLabels,
        'featureIcons' => $featureIcons,
        'backgroundTaskStatuses' => $backgroundTaskStatuses,
        'backgroundTaskSnapshots' => $backgroundTaskSnapshots,
        'featureRuntimeStates' => $featureRuntimeStates,
        'runningTaskDefinitions' => $runningTaskDefinitions,
        'taskStateSummaries' => $taskStateSummaries,
    ];
}

function homepage_build_api_payload(): array
{
    $context = homepage_build_context();
    $municipalities = is_array($context['municipalities'] ?? null) ? $context['municipalities'] : [];
    $displayMunicipalities = is_array($context['displayMunicipalities'] ?? null) ? $context['displayMunicipalities'] : [];
    $backgroundTaskStatuses = is_array($context['backgroundTaskStatuses'] ?? null) ? $context['backgroundTaskStatuses'] : [];
    $runningTaskEntries = is_array($context['runningTaskEntries'] ?? null) ? $context['runningTaskEntries'] : [];
    $featureLabels = is_array($context['featureLabels'] ?? null) ? $context['featureLabels'] : [];
    $featureIcons = is_array($context['featureIcons'] ?? null) ? $context['featureIcons'] : [];
    $featureRuntimeStates = is_array($context['featureRuntimeStates'] ?? null) ? $context['featureRuntimeStates'] : [];
    $taskStateSummaries = is_array($context['taskStateSummaries'] ?? null) ? $context['taskStateSummaries'] : [];
    $featureSummaries = homepage_feature_summaries(
        $municipalities,
        $featureLabels,
        $featureIcons,
        $featureRuntimeStates
    );

    $municipalityCards = [];
    foreach ($displayMunicipalities as $card) {
        if (!is_array($card)) {
            continue;
        }
        $municipality = is_array($card['municipality'] ?? null) ? $card['municipality'] : [];
        $features = [];
        foreach (($card['visible_features'] ?? []) as $item) {
            if (!is_array($item)) {
                continue;
            }
            $feature = is_array($item['feature'] ?? null) ? $item['feature'] : [];
            $display = homepage_sanitize_home_card_display(
                is_array($item['display'] ?? null) ? $item['display'] : null
            );
            $features[] = [
                'feature_key' => (string)($item['feature_key'] ?? ''),
                'label' => (string)($item['label'] ?? ''),
                'icon' => (string)($item['icon'] ?? ''),
                'title' => (string)($item['title'] ?? ''),
                'status_label' => (string)($item['status_label'] ?? ''),
                'status_class' => (string)($item['status_class'] ?? ''),
                'mode' => (string)($item['mode'] ?? 'disabled'),
                'url' => (string)($feature['url'] ?? ''),
                'display' => $display,
            ];
        }

        if ($features === []) {
            continue;
        }

        $municipalityCards[] = [
            'slug' => (string)($municipality['public_slug'] ?? ($card['slug'] ?? '')),
            'name' => (string)($municipality['name'] ?? ''),
            'prefecture_code' => homepage_prefecture_code($municipality),
            'prefecture_label' => homepage_prefecture_label($municipality),
            'ready_visible_count' => (int)($card['ready_visible_count'] ?? 0),
            'feature_count' => count($features),
            'available_summary' => (string)($card['available_summary'] ?? ''),
            'features' => $features,
        ];
    }

    $runningTasks = [];
    foreach ($runningTaskEntries as $entry) {
        if (!is_array($entry)) {
            continue;
        }
        $runningTasks[] = [
            'slug' => municipality_public_slug((string)($entry['slug'] ?? '')),
            'municipality_name' => (string)($entry['municipality_name'] ?? ''),
            'feature_key' => (string)($entry['feature_key'] ?? ''),
            'task_key' => (string)($entry['task_key'] ?? ''),
            'task_area' => (string)($entry['task_area'] ?? 'scrape'),
            'feature_label' => (string)($entry['feature_label'] ?? ''),
            'feature_icon' => (string)($entry['feature_icon'] ?? ''),
            'display' => is_array($entry['display'] ?? null) ? $entry['display'] : null,
        ];
    }
    $searchRebuildStatus = is_array($backgroundTaskStatuses['search_rebuild'] ?? null)
        ? $backgroundTaskStatuses['search_rebuild']
        : [];
    if ((bool)($searchRebuildStatus['running'] ?? false)) {
        $currentName = trim((string)($searchRebuildStatus['current_municipality_name'] ?? ''));
        $currentSlug = trim((string)($searchRebuildStatus['current_slug'] ?? ''));
        $processedCount = max(0, (int)($searchRebuildStatus['processed_count'] ?? 0));
        $totalCount = max(0, (int)($searchRebuildStatus['total_count'] ?? 0));
        if ($totalCount <= 0) {
            $totalCount = homepage_search_rebuild_total_count_fallback();
        }
        $currentSlugProcessed = max(0, (int)($searchRebuildStatus['current_slug_processed_count'] ?? 0));
        $currentSlugTotal = max(0, (int)($searchRebuildStatus['current_slug_total_count'] ?? 0));
        $currentSlugCountCache = homepage_search_rebuild_current_slug_count_fallback($searchRebuildStatus);
        if ((int)$currentSlugCountCache['total_count'] > 0) {
            $currentSlugTotal = (int)$currentSlugCountCache['total_count'];
        }
        if ((int)$currentSlugCountCache['processed_count'] > 0) {
            $currentSlugProcessed = (int)$currentSlugCountCache['processed_count'];
        }
        if ($currentSlugTotal <= 0) {
            $currentSlugTotal = homepage_search_rebuild_current_slug_total($searchRebuildStatus);
        }
        if ($currentSlugProcessed <= 0 && $processedCount > 0) {
            $currentSlugProcessed = max(0, $processedCount - homepage_search_rebuild_visible_index_count($searchRebuildStatus));
        }
        if ($currentSlugTotal > 0) {
            $currentSlugProcessed = min($currentSlugProcessed, $currentSlugTotal);
        }
        $detailLines = [];
        $activityDetail = homepage_search_rebuild_activity_detail($searchRebuildStatus);
        if ($activityDetail !== '') {
            $detailLines[] = $activityDetail;
        }
        $runningTasks[] = [
            'slug' => $currentSlug,
            'municipality_name' => $currentName !== '' ? $currentName : $currentSlug,
            'feature_key' => trim((string)($searchRebuildStatus['current_stage'] ?? '')) === 'reiki' ? 'reiki' : 'gijiroku',
            'task_key' => trim((string)($searchRebuildStatus['current_stage'] ?? '')) === 'reiki' ? 'reiki' : 'gijiroku',
            'task_area' => 'index',
            'feature_label' => '検索インデックス更新',
            'feature_icon' => '',
            'display' => [
                'label' => 'インデックス作成中',
                'class' => 'task-running',
                'detail' => implode("\n", $detailLines),
                'progress_current' => $currentSlugProcessed,
                'progress_total' => $currentSlugTotal > 0 ? $currentSlugTotal : null,
                'batch_running' => true,
            ],
        ];
    }

    return [
        // トップページはこの payload だけを見て描画する。
        // 空の自治体や空の機能はサーバー側で落としてから返す。
        'generated_at' => app_now_tokyo(),
        'municipality_count' => count($municipalities),
        'display_municipality_count' => count($municipalityCards),
        'prefectures' => homepage_prefecture_options_from_cards($municipalityCards),
        'selected_prefecture_code' => '',
        'selected_prefecture_name' => '',
        'feature_summaries' => $featureSummaries,
        'task_state_summaries' => $taskStateSummaries,
        'running_tasks' => $runningTasks,
        'municipalities' => $municipalityCards,
    ];
}

function homepage_overlay_live_status(array $payload): array
{
    $statuses = [
        'gijiroku' => homepage_normalize_task_status_items(load_background_task_status_fast('gijiroku')),
        'reiki' => homepage_normalize_task_status_items(load_background_task_status_fast('reiki')),
    ];

    if (is_array($payload['municipalities'] ?? null)) {
        foreach ($payload['municipalities'] as $cardIndex => $card) {
            if (!is_array($card)) {
                continue;
            }
            $slug = resolve_municipality_slug((string)($card['slug'] ?? ''));
            if ($slug === '' || !is_array($card['features'] ?? null)) {
                continue;
            }
            foreach ($card['features'] as $featureIndex => $featureCard) {
                if (!is_array($featureCard)) {
                    continue;
                }
                $featureKey = trim((string)($featureCard['feature_key'] ?? ''));
                if (!isset($statuses[$featureKey]) || !is_array($statuses[$featureKey])) {
                    continue;
                }
                $statusDisplay = background_task_item_display($statuses[$featureKey], $slug);
                if (!is_array($statusDisplay) || homepage_task_display_is_index_waiting($statusDisplay)) {
                    continue;
                }
                $existingDisplay = is_array($featureCard['display'] ?? null) ? $featureCard['display'] : null;
                $mergedDisplay = is_array($existingDisplay)
                    ? homepage_merge_task_display($statusDisplay, $existingDisplay)
                    : $statusDisplay;
                if (is_array($mergedDisplay)) {
                    $payload['municipalities'][$cardIndex]['features'][$featureIndex]['display'] =
                        homepage_sanitize_home_card_display($mergedDisplay);
                }
            }
        }
    }

    if (is_array($payload['municipalities'] ?? null)) {
        foreach ($payload['municipalities'] as $cardIndex => $card) {
            if (!is_array($card) || !is_array($card['features'] ?? null)) {
                continue;
            }
            foreach ($card['features'] as $featureIndex => $featureCard) {
                if (!is_array($featureCard) || !is_array($featureCard['display'] ?? null)) {
                    continue;
                }
                $payload['municipalities'][$cardIndex]['features'][$featureIndex]['display'] =
                    homepage_sanitize_home_card_display($featureCard['display']);
            }
        }
    }

    $taskPayload = homepage_build_task_status_payload();
    if (is_array($taskPayload['task_state_summaries'] ?? null)) {
        $payload['task_state_summaries'] = $taskPayload['task_state_summaries'];
    }
    if (is_array($taskPayload['running_tasks'] ?? null)) {
        $payload['running_tasks'] = $taskPayload['running_tasks'];
    }
    return $payload;
}

function homepage_api_cache_path(): string
{
    return data_path('background_tasks/home_api_payload.json');
}

function homepage_api_cache_refresh_lock_path(): string
{
    return data_path('background_tasks/home_api_payload.lock');
}

function homepage_store_cached_api_payload(string $path, array $payload): void
{
    write_json_cache_file($path, $payload);
    management_db_store_homepage_payload($payload);
}

function homepage_api_cache_dependencies_missing(): bool
{
    return !is_file(municipality_catalog_cache_path());
}

function homepage_rebuild_api_payload_cache(): array
{
    $cachePath = homepage_api_cache_path();
    $payload = homepage_build_api_payload();
    homepage_store_cached_api_payload($cachePath, $payload);
    return $payload;
}

function homepage_task_status_baseline(): array
{
    $baseline = management_db_homepage_meta_payload();
    if (is_array($baseline)) {
        return $baseline;
    }
    $cached = read_json_cache_file(homepage_api_cache_path(), 0);
    if (is_array($cached)) {
        unset($cached['municipalities']);
        return $cached;
    }
    return [];
}

function homepage_task_status_summary_by_key(array $baseline): array
{
    $byKey = [];
    foreach (($baseline['task_state_summaries'] ?? []) as $summary) {
        if (!is_array($summary)) {
            continue;
        }
        $taskKey = trim((string)($summary['task_key'] ?? ''));
        if ($taskKey !== '') {
            $byKey[$taskKey] = $summary;
        }
    }
    return $byKey;
}

function homepage_task_status_stat_value(array $summary, string $label): string
{
    foreach (($summary['stats'] ?? []) as $stat) {
        if (is_array($stat) && (string)($stat['label'] ?? '') === $label) {
            return (string)($stat['value'] ?? '');
        }
    }
    return '';
}

function homepage_task_status_stat_complete_count(string $value): int
{
    $value = trim($value);
    if ($value === '') {
        return 0;
    }
    if (preg_match('/^(\d+)(?:\/\d+)?$/', $value, $matches) !== 1) {
        return 0;
    }
    return (int)$matches[1];
}

function homepage_task_status_item_is_countable(array $item): bool
{
    $current = $item['progress_current'] ?? null;
    $total = $item['progress_total'] ?? null;
    return is_numeric($current) && is_numeric($total) && (int)$total > 0 && (int)$current > 0;
}

function homepage_task_status_item_is_complete(array $item): bool
{
    if (!homepage_task_status_item_is_countable($item)) {
        return false;
    }
    return (int)$item['progress_current'] >= (int)$item['progress_total'];
}

function homepage_task_status_completed_slugs(array ...$statuses): array
{
    $complete = [];
    foreach ($statuses as $status) {
        $items = is_array($status['items'] ?? null) ? $status['items'] : [];
        foreach ($items as $rawSlug => $item) {
            if (!is_array($item) || !homepage_task_status_item_is_complete($item)) {
                continue;
            }
            $slug = resolve_municipality_slug((string)($item['slug'] ?? $rawSlug));
            if ($slug !== '') {
                $complete[$slug] = true;
            }
        }
    }
    return $complete;
}

function homepage_task_status_data_slugs(array ...$statuses): array
{
    $slugs = [];
    foreach ($statuses as $status) {
        $items = is_array($status['items'] ?? null) ? $status['items'] : [];
        foreach ($items as $rawSlug => $item) {
            if (!is_array($item) || !homepage_task_status_item_is_countable($item)) {
                continue;
            }
            $slug = resolve_municipality_slug((string)($item['slug'] ?? $rawSlug));
            if ($slug !== '') {
                $slugs[$slug] = true;
            }
        }
    }
    return $slugs;
}

function homepage_task_status_downloaded_value(
    string $featureKey,
    array $baselineSummary,
    array $completionStatus = [],
    array $liveStatus = []
): string
{
    $baselineValue = homepage_task_status_stat_value($baselineSummary, '取得完了');
    if ($baselineValue === '') {
        $baselineValue = homepage_task_status_stat_value($baselineSummary, 'DL済');
    }
    $baselineComplete = homepage_task_status_stat_complete_count($baselineValue);
    $targetCount = count(array_filter(
        homepage_feature_target_codes($featureKey),
        static fn(mixed $code): bool => trim((string)$code) !== ''
    ));

    $dataSlugs = homepage_task_status_data_slugs($completionStatus, $liveStatus);
    if ($dataSlugs !== [] && $targetCount > 0) {
        $completeSlugs = homepage_task_status_completed_slugs($completionStatus, $liveStatus);
        return min(count($completeSlugs), $targetCount) . '/' . $targetCount;
    }

    $cardComplete = function_exists('management_db_homepage_feature_complete_count')
        ? management_db_homepage_feature_complete_count($featureKey)
        : null;
    if ($cardComplete !== null && $targetCount > 0 && $cardComplete !== $baselineComplete) {
        return min($cardComplete, $targetCount) . '/' . $targetCount;
    }
    return $baselineValue;
}

function homepage_task_status_state(array $taskStatus): array
{
    if (background_task_is_stale($taskStatus)) {
        return ['停止の可能性', 'task-summary-stale'];
    }
    if ((bool)($taskStatus['running'] ?? false)) {
        return ['実行中', 'task-summary-running'];
    }
    return ['待機中', 'task-summary-idle'];
}

function homepage_task_status_worker_stat(array $taskStatus, int $defaultCapacity): ?array
{
    $capacity = (int)($taskStatus['worker_capacity'] ?? 0);
    if ($capacity <= 0) {
        $capacity = $defaultCapacity;
    }
    $active = (int)($taskStatus['worker_active_count'] ?? ($taskStatus['active_count'] ?? 0));
    if ($capacity <= 0 && $active <= 0) {
        return null;
    }
    return [
        'label' => '稼働',
        'value' => $capacity > 0 ? ($active . '/' . $capacity) : (string)$active,
    ];
}

function homepage_task_status_index_summary_from_baseline(
    string $taskKey,
    array $taskStatus,
    array $baselineSummary,
    array $indexStatus = [],
    array $completionStatus = []
): ?array {
    if (!in_array($taskKey, ['gijiroku', 'reiki'], true)) {
        return null;
    }
    $baselineIndex = is_array($baselineSummary['index_summary'] ?? null) ? $baselineSummary['index_summary'] : [];
    [$stateLabel, $stateClass] = homepage_task_status_index_state($taskStatus);
    $capacity = max(1, (int)($taskStatus['index_capacity'] ?? 1));
    $active = max(0, (int)($taskStatus['index_active_count'] ?? 0));
    $stats = [];
    $stats[] = ['label' => '稼働', 'value' => $active . '/' . $capacity];
    $completed = homepage_task_status_index_value(
        $taskKey === 'reiki' ? 'reiki' : 'gijiroku',
        $baselineIndex,
        $completionStatus,
        $taskStatus
    );
    if ($completed === '') {
        $completed = homepage_task_status_stat_value($baselineIndex, '検索可');
    }
    if ($completed === '') {
        $completed = homepage_task_status_stat_value($baselineIndex, '完了');
    }
    if ($completed !== '') {
        $stats[] = ['label' => '検索可', 'value' => $completed];
    }
    homepage_task_summary_append_run_time_stats(
        $stats,
        $taskStatus,
        $indexStatus,
        'index_started_at',
        'index_finished_at',
        $stateClass === 'task-summary-running'
    );
    return [
        'label' => (string)($baselineIndex['label'] ?? ($taskKey === 'reiki' ? '例規集 インデックス更新' : '会議録 インデックス更新')),
        'icon' => (string)($baselineIndex['icon'] ?? ''),
        'state_label' => $stateLabel,
        'state_class' => $stateClass,
        'stats' => $stats,
        'tasks' => [],
    ];
}

function homepage_task_status_index_value(
    string $featureKey,
    array $baselineIndex,
    array $completionStatus,
    array $liveStatus = []
): string {
    $dataSlugs = homepage_task_status_data_slugs($completionStatus, $liveStatus);
    if ($dataSlugs === []) {
        return '';
    }

    $sets = homepage_search_indexed_slug_sets();
    $indexed = is_array($sets[$featureKey] ?? null) ? $sets[$featureKey] : [];
    if ($indexed === []) {
        return '';
    }

    $indexedCount = 0;
    foreach ($dataSlugs as $slug => $_) {
        if (isset($indexed[$slug]) && (int)$indexed[$slug] > 0) {
            $indexedCount += 1;
        }
    }
    $total = count($dataSlugs);
    if ($total <= 0) {
        return '';
    }
    return min($indexedCount, $total) . '/' . $total;
}

function homepage_task_status_summary(
    string $taskKey,
    array $taskStatus,
    array $baselineSummary,
    array $options
): array {
    [$stateLabel, $stateClass] = homepage_task_status_state($taskStatus);
    $stats = [];
    $workerStat = homepage_task_status_worker_stat($taskStatus, (int)($options['default_capacity'] ?? 0));
    if (is_array($workerStat)) {
        $stats[] = $workerStat;
    }

    if ($taskKey === 'search_rebuild') {
        $completed = (int)($taskStatus['published_municipality_count'] ?? ($taskStatus['published_slug_count'] ?? 0));
        $total = (int)($options['municipality_count'] ?? 0);
        if ($total <= 0) {
            $baselineComplete = homepage_task_status_stat_value($baselineSummary, '完了');
            if (preg_match('/^\d+\/(\d+)$/', $baselineComplete, $matches) === 1) {
                $total = (int)$matches[1];
            }
        }
        if ($total > 0) {
            $stats[] = ['label' => '完了', 'value' => min($completed, $total) . '/' . $total];
        }
    } else {
        $featureKey = (string)($options['feature_key'] ?? $taskKey);
        $completionStatus = is_array($options['completion_status'] ?? null) ? $options['completion_status'] : [];
        $downloaded = homepage_task_status_downloaded_value(
            $featureKey,
            $baselineSummary,
            $completionStatus,
            $taskStatus
        );
        if ($downloaded !== '') {
            $stats[] = ['label' => '取得完了', 'value' => $downloaded];
        }
    }
    $completionStatus = is_array($options['completion_status'] ?? null) ? $options['completion_status'] : [];
    homepage_task_summary_append_run_time_stats(
        $stats,
        $taskStatus,
        $completionStatus,
        'started_at',
        'finished_at',
        $stateClass === 'task-summary-running'
    );

    return [
        'task_key' => $taskKey,
        'feature_key' => (string)($options['feature_key'] ?? ''),
        'label' => (string)($options['label'] ?? $taskKey),
        'icon' => (string)($options['icon'] ?? ''),
        'state_label' => $stateLabel,
        'state_class' => $stateClass,
        'stats' => $stats,
        'index_summary' => homepage_task_status_index_summary_from_baseline(
            $taskKey,
            $taskStatus,
            $baselineSummary,
            is_array($options['index_status'] ?? null) ? $options['index_status'] : [],
            $completionStatus
        ),
    ];
}

function homepage_task_status_feature_display_fallback(string $slug, string $featureKey): ?array
{
    $snapshotTask = $featureKey . '_snapshot';
    $snapshotStatus = homepage_normalize_task_status_items(load_background_task_status_fast($snapshotTask));
    $snapshotDisplay = background_task_item_display($snapshotStatus, $slug);
    if (is_array($snapshotDisplay) && homepage_task_display_has_count_detail($snapshotDisplay)) {
        return $snapshotDisplay;
    }

    if (function_exists('management_db_homepage_feature_display')) {
        $display = management_db_homepage_feature_display($slug, $featureKey);
        if (is_array($display)) {
            return $display;
        }
    }
    return null;
}

function homepage_task_status_running_tasks_from_items(
    string $taskKey,
    string $featureKey,
    string $featureLabel,
    string $featureIcon,
    array $taskStatus
): array {
    $tasks = [];
    $items = is_array($taskStatus['items'] ?? null) ? $taskStatus['items'] : [];
    foreach ($items as $slug => $item) {
        if (!is_array($item) || trim((string)($item['status'] ?? '')) !== 'running') {
            continue;
        }
        $normalizedSlug = resolve_municipality_slug((string)($item['slug'] ?? $slug));
        if ($normalizedSlug === '') {
            $normalizedSlug = trim((string)$slug);
        }
        if ($normalizedSlug === '') {
            continue;
        }
        $display = background_task_item_display($taskStatus, $normalizedSlug);
        if (!is_array($display) || ($display['class'] ?? '') !== 'task-running') {
            continue;
        }
        if (homepage_task_display_is_index_waiting($display)) {
            continue;
        }
        $isIndexActivity = homepage_task_item_is_index_activity($item);
        $fallbackDisplay = $isIndexActivity
            ? null
            : homepage_task_status_feature_display_fallback($normalizedSlug, $featureKey);
        if (is_array($fallbackDisplay)) {
            $display = homepage_merge_task_display($display, $fallbackDisplay) ?? $display;
        }
        $tasks[] = [
            'slug' => municipality_public_slug($normalizedSlug),
            'municipality_name' => (string)($item['name'] ?? $normalizedSlug),
            'feature_key' => $featureKey,
            'task_key' => $taskKey,
            'task_area' => $isIndexActivity ? 'index' : 'scrape',
            'feature_label' => $featureLabel,
            'feature_icon' => $featureIcon,
            'display' => $display,
        ];
    }
    usort($tasks, static fn(array $a, array $b): int => strcmp(
        (string)($a['municipality_name'] ?? ''),
        (string)($b['municipality_name'] ?? '')
    ));
    return $tasks;
}

function homepage_search_rebuild_running_task(array $searchRebuildStatus): ?array
{
    if (!(bool)($searchRebuildStatus['running'] ?? false)) {
        return null;
    }
    $currentName = trim((string)($searchRebuildStatus['current_municipality_name'] ?? ''));
    $currentSlug = trim((string)($searchRebuildStatus['current_slug'] ?? ''));
    $processedCount = max(0, (int)($searchRebuildStatus['processed_count'] ?? 0));
    $currentSlugProcessed = max(0, (int)($searchRebuildStatus['current_slug_processed_count'] ?? 0));
    $currentSlugTotal = max(0, (int)($searchRebuildStatus['current_slug_total_count'] ?? 0));
    $currentSlugCountCache = homepage_search_rebuild_current_slug_count_fallback($searchRebuildStatus);
    if ((int)$currentSlugCountCache['total_count'] > 0) {
        $currentSlugTotal = (int)$currentSlugCountCache['total_count'];
    }
    if ((int)$currentSlugCountCache['processed_count'] > 0) {
        $currentSlugProcessed = (int)$currentSlugCountCache['processed_count'];
    }
    if ($currentSlugTotal <= 0) {
        $currentSlugTotal = homepage_search_rebuild_current_slug_total($searchRebuildStatus);
    }
    if ($currentSlugProcessed <= 0 && $processedCount > 0) {
        $currentSlugProcessed = max(0, $processedCount - homepage_search_rebuild_visible_index_count($searchRebuildStatus));
    }
    if ($currentSlugTotal > 0) {
        $currentSlugProcessed = min($currentSlugProcessed, $currentSlugTotal);
    }
    $detailLines = [];
    $activityDetail = homepage_search_rebuild_activity_detail($searchRebuildStatus);
    if ($activityDetail !== '') {
        $detailLines[] = $activityDetail;
    }
    return [
        'slug' => $currentSlug,
        'municipality_name' => $currentName !== '' ? $currentName : $currentSlug,
        'feature_key' => trim((string)($searchRebuildStatus['current_stage'] ?? '')) === 'reiki' ? 'reiki' : 'gijiroku',
        'task_key' => trim((string)($searchRebuildStatus['current_stage'] ?? '')) === 'reiki' ? 'reiki' : 'gijiroku',
        'task_area' => 'index',
        'feature_label' => '検索インデックス更新',
        'feature_icon' => '',
        'display' => [
            'label' => 'インデックス作成中',
            'class' => 'task-running',
            'detail' => implode("\n", $detailLines),
            'progress_current' => $currentSlugProcessed,
            'progress_total' => $currentSlugTotal > 0 ? $currentSlugTotal : null,
            'batch_running' => true,
        ],
    ];
}

function homepage_build_task_status_payload(): array
{
    $baseline = homepage_task_status_baseline();
    $baselineByKey = homepage_task_status_summary_by_key($baseline);
    $statuses = [
        'gijiroku' => homepage_normalize_task_status_items(load_background_task_status_fast('gijiroku')),
        'reiki' => homepage_normalize_task_status_items(load_background_task_status_fast('reiki')),
        'gijiroku_snapshot' => homepage_normalize_task_status_items(load_background_task_status_fast('gijiroku_snapshot')),
        'reiki_snapshot' => homepage_normalize_task_status_items(load_background_task_status_fast('reiki_snapshot')),
        'gijiroku_reflect' => homepage_normalize_task_status_items(load_background_task_status_fast('gijiroku_reflect')),
        'reiki_reflect' => homepage_normalize_task_status_items(load_background_task_status_fast('reiki_reflect')),
        'search_rebuild' => homepage_normalize_task_status_items(load_background_task_status_fast('search_rebuild')),
    ];
    $definitions = [
        'gijiroku' => [
            'feature_key' => 'gijiroku',
            'label' => '会議録 スクレイピング',
            'icon' => '🏛️',
            'default_capacity' => 3,
        ],
        'reiki' => [
            'feature_key' => 'reiki',
            'label' => '例規集 スクレイピング',
            'icon' => '⚖️',
            'default_capacity' => 3,
        ],
    ];

    $summaries = [];
    $runningTasks = [];
    foreach ($definitions as $taskKey => $definition) {
        $status = is_array($statuses[$taskKey] ?? null) ? $statuses[$taskKey] : [];
        if ($status === []) {
            continue;
        }
        $summaries[] = homepage_task_status_summary(
            $taskKey,
            $status,
            is_array($baselineByKey[$taskKey] ?? null) ? $baselineByKey[$taskKey] : [],
            [
                ...$definition,
                'completion_status' => is_array($statuses[$taskKey . '_snapshot'] ?? null)
                    ? $statuses[$taskKey . '_snapshot']
                    : [],
                'index_status' => is_array($statuses[$taskKey . '_reflect'] ?? null)
                    ? $statuses[$taskKey . '_reflect']
                    : [],
            ]
        );
        if ($taskKey === 'search_rebuild') {
            $searchTask = homepage_search_rebuild_running_task($status);
            if (is_array($searchTask)) {
                $runningTasks[] = $searchTask;
            }
            continue;
        }
        array_push(
            $runningTasks,
            ...homepage_task_status_running_tasks_from_items(
                $taskKey,
                (string)$definition['feature_key'],
                (string)$definition['label'],
                (string)$definition['icon'],
                $status
            )
        );
    }
    $searchTask = homepage_search_rebuild_running_task(
        is_array($statuses['search_rebuild'] ?? null) ? $statuses['search_rebuild'] : []
    );
    if (is_array($searchTask)) {
        $runningTasks[] = $searchTask;
    }

    $versionParts = [];
    foreach ($statuses as $taskKey => $status) {
        $versionParts[] = $taskKey . ':' . (string)($status['updated_at'] ?? '') . ':' . (string)($status['heartbeat_at'] ?? '');
    }
    $payload = [
        'generated_at' => app_now_tokyo(),
        'version' => sha1(implode('|', $versionParts)),
        'task_state_summaries' => $summaries,
        'running_tasks' => $runningTasks,
    ];
    management_db_update_homepage_task_status($summaries, $runningTasks);
    return $payload;
}

function homepage_schedule_api_payload_cache_refresh(): void
{
    static $scheduled = false;
    if ($scheduled || PHP_SAPI === 'cli') {
        return;
    }

    $lockPath = homepage_api_cache_refresh_lock_path();
    $lockDir = dirname($lockPath);
    if (!is_dir($lockDir)) {
        @mkdir($lockDir, 0755, true);
    }

    $lockHandle = @fopen($lockPath, 'c');
    if ($lockHandle === false) {
        return;
    }

    if (!@flock($lockHandle, LOCK_EX | LOCK_NB)) {
        @fclose($lockHandle);
        return;
    }

    $scheduled = true;
    register_shutdown_function(static function () use ($lockHandle, $lockPath): void {
        if (function_exists('fastcgi_finish_request')) {
            @fastcgi_finish_request();
        }

        try {
            homepage_rebuild_api_payload_cache();
        } catch (Throwable $error) {
            error_log('[home_api] background cache refresh failed: ' . $error->getMessage());
        } finally {
            @flock($lockHandle, LOCK_UN);
            @fclose($lockHandle);
            @unlink($lockPath);
        }
    });
}

function homepage_cached_payload_needs_self_heal(array $payload): bool
{
    $cards = $payload['municipalities'] ?? null;
    if (!is_array($cards)) {
        return false;
    }

    foreach ($cards as $card) {
        if (!is_array($card)) {
            continue;
        }
        $slug = resolve_municipality_slug((string)($card['slug'] ?? ''));
        if ($slug === '') {
            continue;
        }
        $municipality = municipality_entry($slug);
        if (!is_array($municipality)) {
            continue;
        }

        $features = $card['features'] ?? null;
        if (!is_array($features)) {
            continue;
        }
        foreach ($features as $featureCard) {
            if (!is_array($featureCard)) {
                continue;
            }
            $featureKey = trim((string)($featureCard['feature_key'] ?? ''));
            if ($featureKey === '') {
                continue;
            }
            $feature = $municipality[$featureKey] ?? null;
            if (!is_array($feature)) {
                continue;
            }
            $display = is_array($featureCard['display'] ?? null) ? $featureCard['display'] : null;
            if (!homepage_task_display_is_complete($display)) {
                continue;
            }
            $statusLabel = trim((string)($featureCard['status_label'] ?? ''));
            if (!in_array($statusLabel, ['要反映', '未公開'], true)) {
                continue;
            }
            if (municipality_feature_live_has_data_with_cache_heal($slug, $featureKey, $feature)) {
                return true;
            }
        }
    }

    return false;
}

function homepage_build_api_payload_cached(int $ttlSeconds = 15): array
{
    $cachePath = homepage_api_cache_path();
    if ($ttlSeconds <= 0) {
        return homepage_rebuild_api_payload_cache();
    }

    $staleCached = read_json_cache_file($cachePath, 0);
    if ($ttlSeconds > 0) {
        $cached = read_json_cache_file($cachePath, $ttlSeconds);
        if (is_array($cached)) {
            if (homepage_api_cache_dependencies_missing()) {
                homepage_schedule_api_payload_cache_refresh();
                if (!headers_sent()) {
                    header('X-Homepage-Cache: stale-dependency');
                }
            } elseif (!headers_sent()) {
                header('X-Homepage-Cache: hit');
            }
            return $cached;
        }
    }

    if (is_array($staleCached)) {
        if (PHP_SAPI === 'cli') {
            return homepage_rebuild_api_payload_cache();
        }
        homepage_schedule_api_payload_cache_refresh();
        if (!headers_sent()) {
            header('X-Homepage-Cache: stale');
        }
        return $staleCached;
    }

    // トップ API は 5 秒ごとに複数クライアントから叩かれるため、
    // 期限切れでも既存 payload を即返し、再生成はレスポンス完了後に 1 本だけ走らせる。
    // キャッシュがまだ無い初回だけは同期生成する。deploy 時は prewarm でここを先に固める。
    return homepage_rebuild_api_payload_cache();
}
