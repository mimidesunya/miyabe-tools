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

function homepage_merge_task_display(?array $taskDisplay, ?array $fallbackDisplay): ?array
{
    if (!is_array($taskDisplay)) {
        return is_array($fallbackDisplay) ? $fallbackDisplay : null;
    }
    if (!is_array($fallbackDisplay)) {
        return $taskDisplay;
    }

    $taskHasCount = homepage_task_display_has_count_detail($taskDisplay);
    $taskClass = trim((string)($taskDisplay['class'] ?? ''));
    $taskCurrent = (int)($taskDisplay['progress_current'] ?? 0);
    $fallbackCurrent = (int)($fallbackDisplay['progress_current'] ?? 0);
    $preferFallbackCount = !$taskHasCount
        || (
            in_array($taskClass, ['task-running', 'task-failed', 'task-stale'], true)
            && $fallbackCurrent > $taskCurrent
        );
    if (!$preferFallbackCount) {
        return $taskDisplay;
    }

    // 旧 task JSON に件数がない場合と、公開済み件数のほうが新しい場合だけ
    // DB/ファイル走査で復元した件数を優先して合成する。
    $merged = $taskDisplay;
    $fallbackDetail = trim((string)($fallbackDisplay['detail'] ?? ''));
    $taskDetail = trim((string)($taskDisplay['detail'] ?? ''));
    $taskDetail = preg_replace('/(^| \/ )件数未集計(?= \/ |$)/u', '$1', $taskDetail) ?? $taskDetail;
    $taskDetail = trim(preg_replace('/\s*\/\s*\/\s*/u', ' / ', $taskDetail) ?? $taskDetail, ' /');
    $fallbackLines = preg_split('/\R/u', $fallbackDetail, -1, PREG_SPLIT_NO_EMPTY) ?: [];
    if ($fallbackDetail !== '') {
        $taskLines = preg_split('/\R/u', $taskDetail, -1, PREG_SPLIT_NO_EMPTY) ?: [];
        if ($taskLines !== [] && preg_match('/^\d+(?:\/\d+)?\s*件$/u', trim((string)$taskLines[0])) === 1) {
            array_shift($taskLines);
            $taskDetail = implode("\n", $taskLines);
        }
    }
    if (($fallbackDisplay['label'] ?? '') === '完了') {
        $merged['label'] = '完了';
        $merged['class'] = 'task-done';
    }
    if ($fallbackDetail !== '') {
        $mergedFallbackDetail = $fallbackDetail;
        if ($taskDetail !== '' && $fallbackLines !== []) {
            $mergedFallbackDetail = trim((string)$fallbackLines[0]);
        }
        $merged['detail'] = $mergedFallbackDetail . ($taskDetail !== '' ? "\n" . $taskDetail : '');
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
            if (is_array($runtimeState) && !empty($runtimeState['has_data'])) {
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
            'text' => sprintf('%s %s: 対象 %d / 利用可能 %d', $icon, $label, $targetCount, $availableCount),
        ];
    }

    return $summaries;
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
        $isEnabled = $hasData;
        $display = $primaryDisplay;
        if (!$hasData && $publishDisplay !== null) {
            // 未公開のあいだは、scrape 完了件数ではなく「いま何件反映したか」を優先表示する。
            $display = $publishDisplay;
        }
        if (!$hasData && $display === null) {
            $display = $primaryDisplay;
        }

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
        'gijiroku_reflect' => homepage_normalize_task_status_items(load_background_task_status('gijiroku_reflect')),
        'reiki_reflect' => homepage_normalize_task_status_items(load_background_task_status('reiki_reflect')),
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
        ['task_key' => 'gijiroku_reflect', 'feature_key' => 'gijiroku', 'running_label' => '会議録 反映'],
        ['task_key' => 'gijiroku', 'feature_key' => 'gijiroku', 'running_label' => '会議録'],
        ['task_key' => 'reiki_reflect', 'feature_key' => 'reiki', 'running_label' => '例規集 反映'],
        ['task_key' => 'reiki', 'feature_key' => 'reiki', 'running_label' => '例規集'],
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
            if ($taskKey === $featureKey) {
                $display = is_array($runtimeState['displays'] ?? null)
                    ? ($runtimeState['displays']['primary'] ?? null)
                    : null;
            } else {
                $display = background_task_item_display($taskStatus, $normalizedSlug);
            }
            if (!is_array($display) || ($display['class'] ?? '') !== 'task-running') {
                continue;
            }

            $runningTaskEntries[] = [
                'slug' => $normalizedSlug,
                'municipality_name' => (string)($municipality['name'] ?? ($item['name'] ?? $slug)),
                'feature_key' => $featureKey,
                'feature_label' => $featureLabel,
                'task_key' => $taskKey,
                'feature_icon' => (string)($featureIcons[$featureKey] ?? ''),
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
        'featureIcons' => $featureIcons,
        'backgroundTaskStatuses' => $backgroundTaskStatuses,
        'backgroundTaskSnapshots' => $backgroundTaskSnapshots,
        'featureRuntimeStates' => $featureRuntimeStates,
        'runningTaskDefinitions' => $runningTaskDefinitions,
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
            $display = is_array($item['display'] ?? null) ? $item['display'] : null;
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
            'feature_label' => (string)($entry['feature_label'] ?? ''),
            'feature_icon' => (string)($entry['feature_icon'] ?? ''),
            'display' => is_array($entry['display'] ?? null) ? $entry['display'] : null,
        ];
    }

    return [
        // トップページはこの payload だけを見て描画する。
        // 空の自治体や空の機能はサーバー側で落としてから返す。
        'generated_at' => app_now_tokyo(),
        'municipality_count' => count($municipalities),
        'display_municipality_count' => count($municipalityCards),
        'feature_summaries' => $featureSummaries,
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

function homepage_rebuild_api_payload_cache(): array
{
    $cachePath = homepage_api_cache_path();
    $payload = homepage_build_api_payload();
    homepage_store_cached_api_payload($cachePath, $payload);
    return $payload;
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
    if ($ttlSeconds > 0) {
        $cached = read_json_cache_file($cachePath, $ttlSeconds);
        if (is_array($cached)) {
            if (homepage_cached_payload_needs_self_heal($cached)) {
                return homepage_rebuild_api_payload_cache();
            }
            return $cached;
        }
    }

    // トップ API は 5 秒ごとに複数クライアントから叩かれるため、
    // 数十秒単位の遅延は許容して、同じ payload を再利用して SQLite/JSON 走査を減らす。
    // ttlSeconds=0 は「強制再生成」として扱い、debug / prewarm からも迷わず使えるようにする。
    return homepage_rebuild_api_payload_cache();
}
