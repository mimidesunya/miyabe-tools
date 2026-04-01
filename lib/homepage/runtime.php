<?php
declare(strict_types=1);

require_once __DIR__ . DIRECTORY_SEPARATOR . '..' . DIRECTORY_SEPARATOR . 'municipalities.php';
require_once __DIR__ . DIRECTORY_SEPARATOR . '..' . DIRECTORY_SEPARATOR . 'background_tasks.php';

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
    // トップ一覧では「厳密件数」より「いま何件入っているか」を軽く知ることが大事。
    // 検索用 DB は rebuild 前提なので、MAX(id) を現在件数として使う。
    $cache[$key] = sqlite_table_max_id($dbPath, $table);
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
            return sprintf('%d件', $currentCount);
        }
        return sprintf('%d/%d件', $currentCount, $totalCount);
    }
    return sprintf('%d件', $currentCount);
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
            'progress_current' => $currentCount,
            'progress_total' => $totalCount > 0 ? $totalCount : null,
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
            'progress_current' => $indexedCount,
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
        $merged['detail'] = $fallbackDetail . ($taskDetail !== '' ? "\n" . $taskDetail : '');
    }
    if (!isset($merged['progress_current']) || !isset($merged['progress_total'])) {
        $merged['progress_current'] = $fallbackDisplay['progress_current'] ?? null;
        $merged['progress_total'] = $fallbackDisplay['progress_total'] ?? null;
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
    array $backgroundTaskStatuses,
    array $backgroundTaskSnapshots
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
        $snapshotDisplay = null;
        $fallbackDisplay = null;
        if (isset($backgroundTaskSnapshots[$featureKey]) && is_array($backgroundTaskSnapshots[$featureKey])) {
            $snapshotDisplay = background_task_item_display($backgroundTaskSnapshots[$featureKey], $slug);
            $fallbackDisplay = background_task_item_fallback_display($backgroundTaskSnapshots[$featureKey], $slug);
        }
        if ($fallbackDisplay === null) {
            $fallbackDisplay = homepage_feature_fallback_display($featureKey, $feature);
        }
        $mergedDisplay = $taskDisplay !== null
            ? homepage_merge_task_display($taskDisplay, $fallbackDisplay)
            : ($snapshotDisplay ?? $fallbackDisplay);

        $hasData = (bool)($feature['has_data'] ?? false);
        $isEnabled = !empty($feature['enabled']) && $hasData;
        if (!$hasData && $mergedDisplay === null) {
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
            'display' => $mergedDisplay,
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
        // カード表示可否は visible_features を作った結果だけで決める。
        // ポスター掲示場・会議録・例規集の三つがすべて非表示なら、自治体カード自体も出さない。
        $summary = homepage_collect_visible_features(
            $municipality,
            (string)$slug,
            $featureLabels,
            $backgroundTaskStatuses,
            $backgroundTaskSnapshots
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
    foreach ($featureLabels as $featureKey => $featureLabel) {
        $taskStatus = $backgroundTaskStatuses[$featureKey] ?? [];
        $items = $taskStatus['items'] ?? null;
        if (!is_array($items)) {
            continue;
        }

        foreach ($items as $slug => $item) {
            if (!is_array($item) || trim((string)($item['status'] ?? '')) !== 'running') {
                continue;
            }

            $display = background_task_item_display($taskStatus, (string)$slug);
            if (!is_array($display) || ($display['class'] ?? '') !== 'task-running') {
                continue;
            }

            $municipality = is_array($municipalities[$slug] ?? null) ? $municipalities[$slug] : [];
            $runningTaskEntries[] = [
                'slug' => (string)$slug,
                'municipality_name' => (string)($municipality['name'] ?? ($item['name'] ?? $slug)),
                'feature_key' => $featureKey,
                'feature_label' => $featureLabel,
                'display' => $display,
            ];
        }
    }

    usort($runningTaskEntries, static function (array $a, array $b): int {
        $featureCompare = strcmp((string)($a['feature_key'] ?? ''), (string)($b['feature_key'] ?? ''));
        if ($featureCompare !== 0) {
            return $featureCompare;
        }
        return strcmp((string)($a['municipality_name'] ?? ''), (string)($b['municipality_name'] ?? ''));
    });

    return [
        'municipalities' => $municipalities,
        'displayMunicipalities' => $displayMunicipalities,
        'runningTaskEntries' => $runningTaskEntries,
        'featureLabels' => $featureLabels,
        'backgroundTaskStatuses' => $backgroundTaskStatuses,
        'backgroundTaskSnapshots' => $backgroundTaskSnapshots,
    ];
}

function homepage_build_api_payload(): array
{
    $context = homepage_build_context();
    $municipalities = is_array($context['municipalities'] ?? null) ? $context['municipalities'] : [];
    $displayMunicipalities = is_array($context['displayMunicipalities'] ?? null) ? $context['displayMunicipalities'] : [];
    $backgroundTaskStatuses = is_array($context['backgroundTaskStatuses'] ?? null) ? $context['backgroundTaskStatuses'] : [];
    $runningTaskEntries = is_array($context['runningTaskEntries'] ?? null) ? $context['runningTaskEntries'] : [];

    $taskSummaries = [];
    foreach (['gijiroku' => '会議録', 'reiki' => '例規集'] as $featureKey => $label) {
        $taskStatus = is_array($backgroundTaskStatuses[$featureKey] ?? null) ? $backgroundTaskStatuses[$featureKey] : [];
        $taskSummaries[] = [
            'feature_key' => $featureKey,
            'label' => $label,
            'running' => (bool)($taskStatus['running'] ?? false),
            'text' => sprintf(
                '%sスクレイピング: %s',
                $label,
                background_task_progress_detail((int)($taskStatus['completed_count'] ?? 0), (int)($taskStatus['total_count'] ?? 0))
            ),
        ];
    }

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
            $display = is_array($item['display'] ?? null) ? $item['display'] : null;
            $features[] = [
                'feature_key' => (string)($item['feature_key'] ?? ''),
                'label' => (string)($item['label'] ?? ''),
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
            'slug' => (string)($card['slug'] ?? ''),
            'name' => (string)($municipality['name'] ?? ''),
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
            'slug' => (string)($entry['slug'] ?? ''),
            'municipality_name' => (string)($entry['municipality_name'] ?? ''),
            'feature_key' => (string)($entry['feature_key'] ?? ''),
            'feature_label' => (string)($entry['feature_label'] ?? ''),
            'display' => is_array($entry['display'] ?? null) ? $entry['display'] : null,
        ];
    }

    return [
        // トップページはこの payload だけを見て描画する。
        // 空の自治体や空の機能はサーバー側で落としてから返す。
        'generated_at' => date('Y-m-d H:i:s'),
        'municipality_count' => count($municipalities),
        'display_municipality_count' => count($municipalityCards),
        'task_summaries' => $taskSummaries,
        'running_tasks' => $runningTasks,
        'municipalities' => $municipalityCards,
    ];
}

function homepage_api_cache_path(): string
{
    return data_path('background_tasks/home_api_payload.json');
}

function homepage_store_cached_api_payload(string $path, array $payload): void
{
    write_json_cache_file($path, $payload);
}

function homepage_build_api_payload_cached(int $ttlSeconds = 60): array
{
    $cachePath = homepage_api_cache_path();
    $cached = read_json_cache_file($cachePath, $ttlSeconds);
    if (is_array($cached)) {
        return $cached;
    }

    // トップ API は 5 秒ごとに複数クライアントから叩かれるため、
    // 毎分程度の遅延は許容して、同じ payload を再利用して SQLite/JSON 走査を減らす。
    $payload = homepage_build_api_payload();
    homepage_store_cached_api_payload($cachePath, $payload);
    return $payload;
}
