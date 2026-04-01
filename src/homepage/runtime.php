<?php
declare(strict_types=1);

require_once 'municipalities.php';
require_once 'background_tasks.php';

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

        // 既存データは .json / .json.gz が混在するので両方を透過的に扱う。
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
        $cache[$path] = is_array($decoded) ? count($decoded) : 0;
        return $cache[$path];
    }

    $cache[$path] = 0;
    return 0;
}

function homepage_sqlite_row_count(string $dbPath, string $table): int
{
    static $cache = [];
    $key = $dbPath . '|' . $table;
    if (array_key_exists($key, $cache)) {
        return $cache[$key];
    }
    if (!is_file($dbPath) || !class_exists(PDO::class) || !in_array('sqlite', PDO::getAvailableDrivers(), true)) {
        $cache[$key] = 0;
        return 0;
    }
    if (!preg_match('/^[a-z_][a-z0-9_]*$/i', $table)) {
        $cache[$key] = 0;
        return 0;
    }

    try {
        // テーブル名だけはホワイトリスト形式で検証し、SQL 文字列連結でも安全側に寄せる。
        $pdo = new PDO('sqlite:' . $dbPath);
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $value = $pdo->query('SELECT COUNT(*) FROM ' . $table)->fetchColumn();
        $cache[$key] = max(0, (int)$value);
    } catch (Throwable) {
        $cache[$key] = 0;
    }

    return $cache[$key];
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
            return sprintf('%d件処理済', $currentCount);
        }
        return sprintf('%d/%d件処理済', $currentCount, $totalCount);
    }
    return sprintf('%d件処理済', $currentCount);
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

function homepage_feature_fallback_display(string $featureKey, array $feature): ?array
{
    if ($featureKey === 'reiki') {
        // manifest / 整形 HTML / SQLite の三つを見比べ、古い task JSON でも件数が落ちないようにする。
        $manifestPath = dirname((string)($feature['source_dir'] ?? '')) . DIRECTORY_SEPARATOR . 'source_manifest.json';
        $totalCount = homepage_json_array_count_auto($manifestPath);
        $indexedCount = homepage_sqlite_row_count((string)($feature['db_path'] ?? ''), 'ordinances');
        $cleanHtmlCount = homepage_directory_matching_file_count(
            (string)($feature['clean_html_dir'] ?? ''),
            ['/\.(?:html|htm)(?:\.gz)?$/i']
        );
        $totalCount = max($totalCount, $indexedCount, $cleanHtmlCount);
        $currentCount = max($indexedCount, $cleanHtmlCount);
        $detail = homepage_progress_count_detail($currentCount, $totalCount);
        if ($detail === '') {
            return null;
        }
        $isComplete = homepage_progress_count_is_complete($currentCount, $totalCount);
        return [
            'label' => $isComplete ? '完了' : '取得状況',
            'class' => $isComplete ? 'task-done' : 'task-info',
            'detail' => $detail,
        ];
    }

    if ($featureKey === 'gijiroku') {
        // 会議録は index_json と minutes.sqlite の両方から現在件数を拾う。
        $totalCount = homepage_json_array_count_auto((string)($feature['index_json_path'] ?? ''));
        $indexedCount = homepage_sqlite_row_count((string)($feature['db_path'] ?? ''), 'minutes');
        $detail = homepage_progress_count_detail($indexedCount, $totalCount);
        if ($detail === '') {
            return null;
        }
        $isComplete = homepage_progress_count_is_complete($indexedCount, $totalCount);
        return [
            'label' => $isComplete ? '完了' : '取得状況',
            'class' => $isComplete ? 'task-done' : 'task-info',
            'detail' => $detail,
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
    return preg_match('/\d+(?:\/\d+)?\s*件(?:処理済|取得済)/u', $detail) === 1;
}

function homepage_merge_task_display(?array $taskDisplay, ?array $fallbackDisplay): ?array
{
    if (!is_array($taskDisplay)) {
        return is_array($fallbackDisplay) ? $fallbackDisplay : null;
    }
    if (!is_array($fallbackDisplay) || homepage_task_display_has_count_detail($taskDisplay)) {
        return $taskDisplay;
    }

    // 旧 task JSON に件数がないときだけ、DB/ファイル走査で復元した件数を上書き合成する。
    $merged = $taskDisplay;
    $fallbackDetail = trim((string)($fallbackDisplay['detail'] ?? ''));
    $taskDetail = trim((string)($taskDisplay['detail'] ?? ''));
    $taskDetail = preg_replace('/(^| \/ )件数未集計(?= \/ |$)/u', '$1', $taskDetail) ?? $taskDetail;
    $taskDetail = trim(preg_replace('/\s*\/\s*\/\s*/u', ' / ', $taskDetail) ?? $taskDetail, ' /');
    if (($fallbackDisplay['label'] ?? '') === '完了') {
        $merged['label'] = '完了';
        $merged['class'] = 'task-done';
    }
    if ($fallbackDetail !== '') {
        $merged['detail'] = $fallbackDetail . ($taskDetail !== '' ? ' / ' . $taskDetail : '');
    }
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

function homepage_collect_visible_features(
    array $municipality,
    string $slug,
    array $featureLabels,
    array $backgroundTaskStatuses
): array {
    $visibleFeatures = [];
    $readyVisibleCount = 0;

    foreach ($featureLabels as $featureKey => $label) {
        $feature = is_array($municipality[$featureKey] ?? null) ? $municipality[$featureKey] : [];
        $featureTitle = (string)($feature['title'] ?? (($municipality['name'] ?? $slug) . $label));
        $taskDisplay = null;
        if (isset($backgroundTaskStatuses[$featureKey]) && is_array($backgroundTaskStatuses[$featureKey])) {
            $taskDisplay = background_task_item_display($backgroundTaskStatuses[$featureKey], $slug);
        }

        $hasData = (bool)($feature['has_data'] ?? false);
        $isEnabled = !empty($feature['enabled']) && $hasData;
        if (!$hasData && $taskDisplay === null) {
            continue;
        }

        // 公開中のデータがあるものと、まだ未公開でも進捗を見せたいものだけを残す。
        if ($isEnabled) {
            $statusLabel = '利用可能';
            $statusClass = 'status-ready';
            $mode = 'link';
            $readyVisibleCount += 1;
        } elseif ($hasData) {
            $statusLabel = '休止中';
            $statusClass = 'status-suspended';
            $mode = 'disabled';
        } else {
            $statusLabel = '未公開';
            $statusClass = 'status-unpublished';
            $mode = 'disabled';
        }

        $visibleFeatures[] = [
            'feature_key' => $featureKey,
            'label' => $label,
            'feature' => $feature,
            'title' => $featureTitle,
            'task_display' => $taskDisplay,
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

function homepage_should_be_listed(array $municipality, string $slug, array $featureLabels, array $backgroundTaskStatuses): bool
{
    foreach ($featureLabels as $featureKey => $_label) {
        $feature = is_array($municipality[$featureKey] ?? null) ? $municipality[$featureKey] : [];
        $hasData = (bool)($feature['has_data'] ?? false);
        $taskDisplay = null;
        if (isset($backgroundTaskStatuses[$featureKey]) && is_array($backgroundTaskStatuses[$featureKey])) {
            $taskDisplay = background_task_item_display($backgroundTaskStatuses[$featureKey], $slug);
        }
        if ($hasData || $taskDisplay !== null) {
            return true;
        }
    }

    return false;
}

function homepage_build_context(): array
{
    $municipalities = municipality_catalog();
    $featureLabels = [
        'boards' => 'ポスター掲示場',
        'gijiroku' => '会議録',
        'reiki' => '例規集',
    ];
    $backgroundTaskStatuses = [
        'gijiroku' => homepage_normalize_task_status_items(load_background_task_status('gijiroku')),
        'reiki' => homepage_normalize_task_status_items(load_background_task_status('reiki')),
    ];
    $backgroundTaskSnapshots = [
        'gijiroku' => homepage_normalize_task_status_items(load_background_task_status('gijiroku_snapshot')),
        'reiki' => homepage_normalize_task_status_items(load_background_task_status('reiki_snapshot')),
    ];

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
        // 一覧に出すかどうかは「公開データ」か「取得進捗」のどちらかがあるかで判定する。
        if (!homepage_should_be_listed($municipality, (string)$slug, $featureLabels, $backgroundTaskStatuses)) {
            continue;
        }
        $summary = homepage_collect_visible_features($municipality, (string)$slug, $featureLabels, $backgroundTaskStatuses);
        $displayMunicipalities[] = [
            'slug' => (string)$slug,
            'municipality' => $municipality,
            'visible_features' => $summary['visible_features'],
            'ready_visible_count' => $summary['ready_visible_count'],
            'available_summary' => $summary['available_summary'],
        ];
    }

    return [
        'municipalities' => $municipalities,
        'displayMunicipalities' => $displayMunicipalities,
        'featureLabels' => $featureLabels,
        'backgroundTaskStatuses' => $backgroundTaskStatuses,
        'backgroundTaskSnapshots' => $backgroundTaskSnapshots,
    ];
}
