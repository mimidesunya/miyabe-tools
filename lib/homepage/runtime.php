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

function homepage_gijiroku_scrape_state(array $feature): ?array
{
    static $cache = [];
    $workDir = trim((string)($feature['work_dir'] ?? ''));
    if ($workDir === '') {
        return null;
    }
    $statePath = rtrim($workDir, DIRECTORY_SEPARATOR . '/\\') . DIRECTORY_SEPARATOR . 'scrape_state.json';
    if (!array_key_exists($statePath, $cache)) {
        $state = read_json_cache_file($statePath, 0);
        $cache[$statePath] = is_array($state) ? $state : null;
    }
    return is_array($cache[$statePath]) ? $cache[$statePath] : null;
}

// 取得したファイルの種別内訳。index 構築時に取得元ごとへ書き出している。
// 目次しか公開していない取得元を「反映待ち」と取り違えないために使う。
function homepage_gijiroku_document_kinds(array $feature): ?array
{
    static $cache = [];
    $workDir = trim((string)($feature['work_dir'] ?? ''));
    if ($workDir === '') {
        return null;
    }
    $path = rtrim($workDir, DIRECTORY_SEPARATOR . '/\\') . DIRECTORY_SEPARATOR . 'document_kinds.json';
    if (!array_key_exists($path, $cache)) {
        $kinds = read_json_cache_file($path, 0);
        $cache[$path] = is_array($kinds) ? $kinds : null;
    }
    return is_array($cache[$path]) ? $cache[$path] : null;
}

// 取得件数と検索できる件数の差を説明する。
//
// 種別内訳が残っている取得元では、差の理由が「本文でないものを除いた」のか
// 「本文はあるが検索へ反映されていない」のかを言い分けられる。内訳が無い
// 取得元だけ、従来どおり一般的な理由を添える。
function homepage_gijiroku_availability_note(array $feature, int $storedCount, int $indexedCount): string
{
    $indexedCount = max(0, $indexedCount);
    $kinds = homepage_gijiroku_document_kinds($feature);
    if (is_array($kinds)) {
        $total = max(0, (int)($kinds['total'] ?? 0));
        $indexable = max(0, (int)($kinds['indexable'] ?? 0));
        if ($total > 0 && $indexable > $indexedCount) {
            return sprintf(
                '取得した%d件のうち本文は%d件ですが、いま検索できるのは%d件です。残りは検索への反映待ちです。',
                $total,
                $indexable,
                $indexedCount
            );
        }
        if ($total > $indexable && $indexable > 0) {
            return sprintf(
                '取得した%d件のうち本文%d件を検索できます（%s）。',
                $total,
                $indexable,
                HOMEPAGE_MINUTES_INDEX_EXCLUSION_REASON
            );
        }
        if ($total > 0 && $total === $indexable) {
            return '';
        }
    }
    return homepage_search_availability_note($storedCount, $indexedCount, HOMEPAGE_MINUTES_INDEX_EXCLUSION_REASON);
}


// 取得はできているのに検索に載る本文が 1 件も無い取得元がある。
// 目次だけを公開している場合で、待っても検索できるようにはならない。
function homepage_gijiroku_body_missing_status(array $feature): array
{
    $kinds = homepage_gijiroku_document_kinds($feature);
    if (!is_array($kinds)) {
        return ['state' => '', 'label' => '', 'detail' => '', 'source_coverage' => null];
    }
    $total = max(0, (int)($kinds['total'] ?? 0));
    $indexable = max(0, (int)($kinds['indexable'] ?? 0));
    if ($total <= 0 || $indexable > 0) {
        return ['state' => '', 'label' => '', 'detail' => '', 'source_coverage' => null];
    }
    $tocCount = max(0, (int)($kinds['kinds']['toc'] ?? 0));
    return [
        'state' => 'body_not_published',
        'label' => '本文なし（目次のみ公開）',
        'detail' => $tocCount >= $total
            ? sprintf('取得元が公開しているのは目次だけで、検索できる本文はありません。目次%d件を取得済みです。', $tocCount)
            : sprintf('取得した%d件から本文を取り出せませんでした。検索できる本文はありません。', $total),
        'source_coverage' => null,
    ];
}

function homepage_gijiroku_classified_progress(array $feature): ?array
{
    $state = homepage_gijiroku_scrape_state($feature);
    if (!is_array($state) || !is_array($state['validation'] ?? null)) {
        return null;
    }
    $validation = $state['validation'];
    if (trim((string)($validation['mode'] ?? '')) !== 'classified_scrape_result') {
        return null;
    }

    return [
        'current' => max(0, (int)($validation['progress_current'] ?? 0)),
        'total' => max(0, (int)($validation['progress_total'] ?? 0)),
        'discovered' => max(0, (int)($validation['discovered_count'] ?? 0)),
        'excluded' => max(0, (int)($validation['excluded_count'] ?? 0)),
        'failed' => max(0, (int)($validation['failed_count'] ?? 0)),
        'unknown_missing' => max(0, (int)($validation['unknown_missing_count'] ?? 0)),
    ];
}

// 走査記録から読み取れる状態。tools/gijiroku/gijiroku_storage.py の
// effective_walk_state と同じ答えを返す必要がある。
// tests/fixtures/source_coverage_rules.json で両方に同じ入力を流して確かめている。
function homepage_minutes_walk_state(?array $coverage): string
{
    if (!is_array($coverage) || $coverage === []) {
        return 'unknown';
    }
    // 判定ルールの版。古い規則で書かれた complete は、ページ送りを諦めた
    // 回数を数えていないので完了の意味が違う。
    if ((int)($coverage['rule_version'] ?? 0) < 2) {
        return 'stale_rule';
    }
    $state = trim((string)($coverage['state'] ?? ''));
    if ($state === '') {
        $state = 'unknown';
    }
    // 歩き直しを始めたまま終われていないなら、前回の complete は当てにしない。
    $startedAt = (string)($coverage['walk_started_at'] ?? '');
    $updatedAt = (string)($coverage['updated_at'] ?? '');
    if ($state === 'complete' && $startedAt !== '' && $startedAt > $updatedAt) {
        return 'rewalking';
    }
    return $state;
}

// 例規の走査記録。tools/reiki/reiki_io.py の effective_coverage_complete と
// 同じ答えを返す必要がある。
function homepage_reiki_coverage_complete(?array $coverage): bool
{
    if (!is_array($coverage) || empty($coverage['complete'])) {
        return false;
    }
    if ((int)($coverage['version'] ?? 0) < 2) {
        return false;
    }
    $startedAt = (string)($coverage['walk_started_at'] ?? '');
    $observedAt = (string)($coverage['observed_at'] ?? '');
    return !($startedAt !== '' && $startedAt > $observedAt);
}

function homepage_gijiroku_source_coverage(array $feature): ?array
{
    // 走査の記録は source_coverage.json を見る。scrape_state.json は実行の頭で
    // 消されるので、走っている最中と殺されたあとが「記録なし」で同じに見える。
    $workDir = trim((string)($feature['work_dir'] ?? ''));
    $coverage = null;
    if ($workDir !== '') {
        $durablePath = rtrim($workDir, DIRECTORY_SEPARATOR . '/\\')
            . DIRECTORY_SEPARATOR . 'source_coverage.json';
        $durable = read_json_cache_file($durablePath, 0);
        if (is_array($durable) && $durable !== []) {
            $coverage = $durable;
        }
    }
    $state = homepage_gijiroku_scrape_state($feature);
    $inState = is_array($state) && is_array($state['source_coverage'] ?? null)
        ? $state['source_coverage']
        : null;
    if (is_array($inState)
        && (!is_array($coverage)
            || (string)($inState['updated_at'] ?? '') > (string)($coverage['updated_at'] ?? ''))) {
        $coverage = $inState;
    }
    if (!is_array($coverage) || trim((string)($coverage['mode'] ?? '')) !== 'source_discovery_coverage') {
        return null;
    }

    $coverageState = homepage_minutes_walk_state($coverage);
    if ($coverageState === 'stale_rule' || $coverageState === 'rewalking') {
        return null;
    }
    if (!in_array($coverageState, ['complete', 'partial_planned', 'partial_limit', 'partial_error', 'partial_recent_only'], true)) {
        return null;
    }
    return [
        'state' => $coverageState,
        'discovered_count' => max(0, (int)($coverage['discovered_count'] ?? 0)),
        'list_page_count' => max(0, (int)($coverage['list_page_count'] ?? 0)),
        'failed_list_page_count' => max(0, (int)($coverage['failed_list_page_count'] ?? 0)),
        'limit' => max(0, (int)($coverage['limit'] ?? 0)),
        'updated_at' => trim((string)($coverage['updated_at'] ?? '')),
    ];
}

function homepage_gijiroku_acquisition_status(
    array $feature,
    string $systemType,
    bool $hasData,
    bool $hasError,
    int $indexedCount,
    ?array $display
): array {
    if (!$hasData) {
        return ['state' => 'unacquired', 'label' => '未取得', 'detail' => '', 'source_coverage' => null];
    }

    $systemType = strtolower(trim($systemType));
    // 走査の記録を書くのは dbsr だけではない。kaigiroku.net と gijiroku.com も
    // 歩いた年の数を記録するようになった。名前で弾くと、記録があるのに
    // 「取得範囲未判定」と出し続けることになる。
    $recordsWalk = ['dbsr', 'db-search', 'kaigiroku-indexphp', 'kaigiroku.net', 'gijiroku.com', 'voices'];
    if (!in_array($systemType, $recordsWalk, true)) {
        if ($hasError) {
            return [
                'state' => 'partial_error',
                'label' => '一部検索可（エラー停止）',
                'detail' => '検索データはありますが、取得処理がエラーになっています。全件取得済みか確認できません。',
                'source_coverage' => null,
            ];
        }
        // 走査記録を持たない系統でも、検索反映の遅れは起きる。
        $storedCount = (int)(is_array($display) ? ($display['count_current'] ?? 0) : 0);
        $shortfall = homepage_indexed_shortfall_status($storedCount, $indexedCount);
        if ($shortfall['state'] !== '') {
            return $shortfall;
        }
        $note = homepage_gijiroku_availability_note($feature, $storedCount, $indexedCount);
        return [
            'state' => 'coverage_unknown',
            'label' => '検索可（取得範囲未判定）',
            'detail' => trim(
                '検索データはありますが、取得元の全一覧を走査済みか確認できる記録がありません。'
                . ($note !== '' ? ' ' . $note : '')
            ),
            'source_coverage' => null,
        ];
    }

    $sourceCoverage = homepage_gijiroku_source_coverage($feature);
    $sourceState = is_array($sourceCoverage) ? (string)($sourceCoverage['state'] ?? '') : '';
    $progress = homepage_gijiroku_classified_progress($feature);
    $progressIncomplete = is_array($progress)
        && (int)($progress['total'] ?? 0) > 0
        && (int)($progress['current'] ?? 0) < (int)($progress['total'] ?? 0);
    $validationMatchesDiscovery = is_array($progress)
        && is_array($sourceCoverage)
        && (int)($sourceCoverage['discovered_count'] ?? 0) > 0
        && (int)($progress['discovered'] ?? 0) === (int)($sourceCoverage['discovered_count'] ?? 0);
    $isRunning = is_array($display) && trim((string)($display['class'] ?? '')) === 'task-running';

    if (
        $isRunning
        || in_array($sourceState, ['partial_planned', 'partial_limit'], true)
        || ($sourceState === 'complete' && !$validationMatchesDiscovery)
    ) {
        $limit = is_array($sourceCoverage) ? (int)($sourceCoverage['limit'] ?? 0) : 0;
        if ($isRunning || $sourceState === 'partial_planned') {
            $detail = '既存データを公開したまま、上限なしの追加取得を実行または再実行待ちです。';
        } elseif ($sourceState === 'complete' && !$validationMatchesDiscovery) {
            $detail = '取得元の全一覧走査は終わり、発見した会議録の取得・検証を続けています。';
        } else {
            $detail = '取得上限に達した部分データです。次回スクレイピングで上限なしの追加取得対象になります。';
        }
        if ($limit > 0 && $sourceState === 'partial_limit') {
            $detail .= sprintf(' 前回上限: %d件。', $limit);
        }
        return [
            'state' => 'partial_planned',
            'label' => '一部検索可（追加取得中・予定）',
            'detail' => $detail,
            'source_coverage' => $sourceCoverage,
        ];
    }

    if ($sourceState === 'partial_error' || $progressIncomplete) {
        return [
            'state' => 'partial_error',
            'label' => '一部検索可（エラー停止）',
            'detail' => '取得元の一部を取得できず、全件取得が完了していません。修正または再実行が必要です。',
            'source_coverage' => $sourceCoverage,
        ];
    }

    if ($hasError) {
        $wasComplete = $sourceState === 'complete'
            && is_array($progress)
            && homepage_progress_count_is_complete((int)$progress['current'], (int)$progress['total']);
        return [
            'state' => $wasComplete ? 'update_error' : 'partial_error',
            'label' => $wasComplete ? '検索可（更新エラー）' : '一部検索可（エラー停止）',
            'detail' => $wasComplete
                ? '前回の全件取得データは検索できますが、最新の更新確認がエラーになっています。'
                : '取得途中でエラーになり、取得済みの範囲だけ検索できます。',
            'source_coverage' => $sourceCoverage,
        ];
    }

    if (
        $sourceState === 'complete'
        && is_array($progress)
        && homepage_progress_count_is_complete((int)$progress['current'], (int)$progress['total'])
    ) {
        $acquiredCount = max(0, (int)($progress['current'] ?? 0));
        $indexedCount = max(0, $indexedCount);
        if ($acquiredCount > 0 && $indexedCount <= 0) {
            $bodyMissing = homepage_gijiroku_body_missing_status($feature);
            if ($bodyMissing['state'] !== '') {
                $bodyMissing['source_coverage'] = $sourceCoverage;
                return $bodyMissing;
            }
        }
        // 取得した会議数と検索できる件数は、数えている対象が違う。1 つの会議が
        // 目次と本文の複数ファイルになることもあり、休会や目次のように本文の無い
        // 記録は索引に載らない。差をそのまま「反映待ち」と書くと、永久に埋まらない
        // 差を待ちに見せてしまう（福岡県で 1859/1899 と表示していた）。
        // 種別内訳が残っていれば、本文のある件数と突き合わせて判断する。
        $indexableCount = 0;
        $kinds = homepage_gijiroku_document_kinds($feature);
        if (is_array($kinds)) {
            $indexableCount = max(0, (int)($kinds['indexable'] ?? 0));
        }
        $indexPending = $indexableCount > 0
            ? $indexedCount < $indexableCount
            : ($acquiredCount > 0 && $indexedCount < $acquiredCount);
        if ($indexPending) {
            $expectedCount = $indexableCount > 0 ? $indexableCount : $acquiredCount;
            return [
                'state' => 'index_pending',
                'label' => '取得完了・検索反映待ち',
                'detail' => sprintf(
                    '取得元の全一覧から%d件を取得済みです。現在は%d/%d件を検索でき、残りを検索へ反映中または反映待ちです。',
                    $acquiredCount,
                    $indexedCount,
                    $expectedCount
                ),
                'source_coverage' => $sourceCoverage,
            ];
        }
        $note = homepage_gijiroku_availability_note($feature, $acquiredCount, $indexedCount);
        return [
            'state' => 'complete',
            'label' => '取得完了（検索可）',
            'detail' => trim(
                '取得元の全一覧を走査し、見つかった会議録を取得済みです。'
                . ($note !== '' ? ' ' . $note : '')
            ),
            'source_coverage' => $sourceCoverage,
        ];
    }

    if ((int)($progress['current'] ?? 0) > 0 && $indexedCount <= 0) {
        $bodyMissing = homepage_gijiroku_body_missing_status($feature);
        if ($bodyMissing['state'] !== '') {
            $bodyMissing['source_coverage'] = $sourceCoverage;
            return $bodyMissing;
        }
    }

    $shortfall = homepage_indexed_shortfall_status(
        (int)($progress['current'] ?? 0),
        $indexedCount
    );
    if ($shortfall['state'] !== '') {
        $shortfall['source_coverage'] = $sourceCoverage;
        return $shortfall;
    }

    $note = homepage_gijiroku_availability_note($feature, (int)($progress['current'] ?? 0), $indexedCount);
    return [
        'state' => 'coverage_unknown',
        'label' => '検索可（取得範囲未判定）',
        'detail' => trim(
            '検索データはありますが、取得元の全一覧を走査済みか確認できる記録がありません。'
            . ($note !== '' ? ' ' . $note : '')
        ),
        'source_coverage' => $sourceCoverage,
    ];
}


// 取得したファイルには目次など本文以外も含まれ、検索に載るのは本文だけ。
// したがって「保存件数 > 検索できる件数」はそれだけでは異常ではない。
// 1 件も検索できないときだけ反映待ちとして扱う。
function homepage_indexed_shortfall_status(int $storedCount, int $indexedCount): array
{
    $storedCount = max(0, $storedCount);
    $indexedCount = max(0, $indexedCount);
    if ($storedCount > 0 && $indexedCount <= 0) {
        return [
            'state' => 'index_pending',
            'label' => '検索反映待ち',
            'detail' => sprintf(
                '%d件を取得済みですが、まだ検索できません。検索への反映を待っています。',
                $storedCount
            ),
            'source_coverage' => null,
        ];
    }
    return ['state' => '', 'label' => '', 'detail' => '', 'source_coverage' => null];
}


// 取得件数と検索できる件数の差を一文にする。差が出る理由が分かっている
// 機能だけ $reason を渡す。
function homepage_search_availability_note(int $storedCount, int $indexedCount, string $reason = ''): string
{
    $storedCount = max(0, $storedCount);
    $indexedCount = max(0, $indexedCount);
    if ($storedCount <= 0 || $indexedCount <= 0 || $indexedCount >= $storedCount) {
        return '';
    }
    return sprintf(
        '取得した%d件のうち%d件を検索できます%s。',
        $storedCount,
        $indexedCount,
        $reason !== '' ? '（' . $reason . '）' : ''
    );
}


const HOMEPAGE_MINUTES_INDEX_EXCLUSION_REASON = '目次など本文以外は検索の対象外です';
const HOMEPAGE_REIKI_INDEX_EXCLUSION_REASON = '本文として取り出せなかった資料は検索の対象外です';


// 例規集は取得元の走査記録を持たない。取得した生ファイルのうち本文として
// 整形できたものだけが検索に載るため、件数差はそれで説明がつく
// （札幌市 1106 件中 967 件、旭川市 1126 件中 923 件が本文）。
// 例規の走査記録。取得元が上限を持つ検索型（legal-square）だけが書く。
// 目録型は母数を申告しないので、記録が無いことは異常ではない。
function homepage_reiki_source_coverage(array $feature): ?array
{
    $workDir = trim((string)($feature['work_dir'] ?? ''));
    if ($workDir === '') {
        return null;
    }
    $path = rtrim($workDir, DIRECTORY_SEPARATOR . '/\\')
        . DIRECTORY_SEPARATOR . 'source_coverage.json';
    $payload = read_json_cache_file($path, 0);
    if (!is_array($payload) || $payload === []) {
        return null;
    }
    // 判定ルールの版。tools/reiki/scrapers/legal_square.py の version と揃える。
    if ((int)($payload['version'] ?? 0) < 2) {
        return null;
    }
    return $payload;
}

function homepage_reiki_acquisition_status(
    int $storedCount,
    int $indexedCount,
    array $feature = [],
    bool $hasError = false
): array {
    $status = homepage_indexed_shortfall_status($storedCount, $indexedCount);
    if ($status['state'] !== '') {
        return $status;
    }
    // 直近の取得が失敗していても、古い索引が 1 件でも残っていれば
    // 「利用可能」に落ちていた。エラーの表示は $hasData が偽のときしか
    // 届かないので、ここで拾う。
    if ($hasError) {
        return [
            'state' => 'last_run_failed',
            'label' => '検索可（直近の取得に失敗）',
            'detail' => '検索できるのは前回までに取れた分です。'
                . '直近の取得は失敗しています。',
            'source_coverage' => null,
        ];
    }
    // 取り切れなかった区間が残っているなら、そう出す。会議録では出している
    // のに例規だけ「利用可能」と出していた。
    $coverage = $feature === [] ? null : homepage_reiki_source_coverage($feature);
    if (is_array($coverage)) {
        $startedAt = (string)($coverage['walk_started_at'] ?? '');
        $observedAt = (string)($coverage['observed_at'] ?? '');
        $rewalking = $startedAt !== '' && $startedAt > $observedAt;
        if (!homepage_reiki_coverage_complete($coverage)) {
            $unresolved = is_array($coverage['unresolved'] ?? null)
                ? count($coverage['unresolved'])
                : 0;
            return [
                'state' => 'coverage_incomplete',
                'label' => '検索可（一部未取得）',
                'detail' => $rewalking
                    ? '取得元を確認し直している途中です。'
                    : ($unresolved > 0
                        ? '取得元の上限に阻まれて取り切れていない区間が ' . $unresolved . ' 件あります。'
                        : '取得元の全件を取り切れた記録がありません。'),
                'source_coverage' => $coverage,
            ];
        }
    }
    return [
        'state' => '',
        'label' => '',
        'detail' => homepage_search_availability_note(
            $storedCount,
            $indexedCount,
            HOMEPAGE_REIKI_INDEX_EXCLUSION_REASON
        ),
        'source_coverage' => null,
    ];
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
    $freshnessFields = [];
    if (is_array($snapshotDisplay)) {
        foreach (['freshness_date', 'freshness_basis', 'last_checked_at'] as $field) {
            $value = trim((string)($snapshotDisplay[$field] ?? ''));
            if ($value !== '') {
                $freshnessFields[$field] = $value;
            }
        }
    }

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
        ] + $freshnessFields;
    }

    if ($featureKey === 'gijiroku') {
        $indexPath = (string)($feature['index_json_path'] ?? '');
        $downloadsDir = (string)($feature['downloads_dir'] ?? '');
        $classifiedProgress = homepage_gijiroku_classified_progress($feature);
        if (is_array($classifiedProgress)) {
            $downloadedCount = (int)$classifiedProgress['current'];
            $totalCount = (int)$classifiedProgress['total'];
        } else {
            $totalCount = homepage_gijiroku_index_unique_count($indexPath);
            $downloadedCount = homepage_gijiroku_indexed_download_count($indexPath, $downloadsDir);
            $totalCount = max($totalCount, $downloadedCount);
        }
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
        ] + $freshnessFields;
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

    // 失敗した自治体は、件数が出ていなくてもカード上で原因を確認できる必要がある。
    return false;
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
    $freshnessLine = homepage_task_display_freshness_line($fallbackDisplay);
    if ($freshnessLine === '') {
        $freshnessLine = homepage_task_display_freshness_line($taskDisplay);
    }
    if ($freshnessLine !== '' && !in_array($freshnessLine, $fallbackLines, true)) {
        $fallbackLines[] = $freshnessLine;
    }
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

function homepage_task_display_freshness_line(?array $display): string
{
    if (!is_array($display)) {
        return '';
    }
    $date = trim((string)($display['freshness_date'] ?? ''));
    if ($date === '' || preg_match('/^\d{4}-\d{2}-\d{2}$/', $date) !== 1) {
        return '';
    }
    $basis = trim((string)($display['freshness_basis'] ?? ''));
    $label = match ($basis) {
        'content_current' => '内容現在',
        'latest_document' => '最新日付',
        default => '鮮度',
    };
    return $label . ' ' . $date;
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
    $freshnessLine = homepage_task_display_freshness_line($display);
    if ($freshnessLine !== '' && !in_array($freshnessLine, $detailLines, true)) {
        $detailLines[] = $freshnessLine;
    }
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

    $clean = [];
    foreach ([
        'label',
        'class',
        'detail',
        'count_current',
        'count_total',
        'progress_current',
        'progress_total',
        'freshness_date',
        'freshness_basis',
        'last_checked_at',
    ] as $key) {
        if (array_key_exists($key, $display)) {
            $clean[$key] = $display[$key];
        }
    }
    foreach (['warning_lines', 'log_lines'] as $key) {
        if (is_array($display[$key] ?? null)) {
            $clean[$key] = array_slice(array_values(array_map('strval', $display[$key])), 0, 12);
        }
    }
    return $clean;
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

function homepage_task_display_has_error(?array $display): bool
{
    if (!is_array($display)) {
        return false;
    }
    return trim((string)($display['class'] ?? '')) === 'task-failed';
}

// 走査は最後まで通ったのに、取得元から会議録が 1 件も見つからなかった状態。
// 取得処理の失敗とは区別する（やり直しても結果は変わらない）。
function homepage_task_display_found_nothing(?array $display): bool
{
    if (!is_array($display)) {
        return false;
    }
    $detail = (string)($display['detail'] ?? '');
    // 以前の走査は「取得対象件数が0件です」と書いていた。次の走査で
    // 書き換わるまでの間も、同じ状態として扱う。
    return str_contains($detail, '会議録を見つけられませんでした')
        || str_contains($detail, '取得対象件数が0件です');
}


function homepage_task_display_has_warning(?array $display): bool
{
    if (!is_array($display)) {
        return false;
    }
    if (trim((string)($display['class'] ?? '')) === 'task-stale') {
        return true;
    }
    if (homepage_task_display_warning_lines($display) !== []) {
        return true;
    }
    return str_contains((string)($display['detail'] ?? ''), '警告あり');
}

function homepage_task_display_has_issue(?array $display): bool
{
    return homepage_task_display_has_error($display) || homepage_task_display_has_warning($display);
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
                    'aggs' => [
                        'oldest_sort_date' => [
                            'min' => [
                                'field' => 'sort_date',
                                'format' => 'yyyy-MM-dd',
                            ],
                        ],
                        'newest_sort_date' => [
                            'max' => [
                                'field' => 'sort_date',
                                'format' => 'yyyy-MM-dd',
                            ],
                        ],
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
    $dateRanges = [];
    foreach ($buckets as $bucket) {
        if (!is_array($bucket)) {
            continue;
        }
        $slug = trim((string)($bucket['key'] ?? ''));
        $count = max(0, (int)($bucket['doc_count'] ?? 0));
        if ($slug !== '' && $count > 0) {
            $slugs[$slug] = $count;
            $oldestDate = trim((string)($bucket['oldest_sort_date']['value_as_string'] ?? ''));
            $newestDate = trim((string)($bucket['newest_sort_date']['value_as_string'] ?? ''));
            if (
                preg_match('/^\d{4}-\d{2}-\d{2}$/', $oldestDate) === 1
                && preg_match('/^\d{4}-\d{2}-\d{2}$/', $newestDate) === 1
            ) {
                $dateRanges[$slug] = [
                    'from' => $oldestDate,
                    'to' => $newestDate,
                    'document_count' => $count,
                ];
            }
        }
    }
    return [
        'counts' => $slugs,
        'date_ranges' => $dateRanges,
    ];
}

function homepage_search_index_metadata(): array
{
    static $cachedMetadata = null;
    if (is_array($cachedMetadata)) {
        return $cachedMetadata;
    }

    $cachePath = homepage_search_index_cache_path();
    $cached = read_json_cache_file($cachePath, homepage_search_index_cache_ttl_seconds());
    if (
        is_array($cached)
        && is_array($cached['features'] ?? null)
        && is_array($cached['date_ranges'] ?? null)
    ) {
        $cachedMetadata = $cached;
        return $cachedMetadata;
    }

    $features = [];
    $dateRanges = [];
    try {
        foreach (['gijiroku', 'reiki'] as $featureKey) {
            $docType = homepage_feature_search_doc_type($featureKey);
            $featureMetadata = $docType !== null
                ? homepage_fetch_indexed_slugs_for_doc_type($docType)
                : ['counts' => [], 'date_ranges' => []];
            $features[$featureKey] = is_array($featureMetadata['counts'] ?? null)
                ? $featureMetadata['counts']
                : [];
            $dateRanges[$featureKey] = is_array($featureMetadata['date_ranges'] ?? null)
                ? $featureMetadata['date_ranges']
                : [];
        }
        $cachedMetadata = [
            'generated_at' => app_now_tokyo(),
            'features' => $features,
            'date_ranges' => $dateRanges,
        ];
        write_json_cache_file($cachePath, $cachedMetadata);
        return $cachedMetadata;
    } catch (Throwable $error) {
        error_log('[home_api] search index availability check failed: ' . $error->getMessage());
        $staleCached = read_json_cache_file($cachePath, 0);
        $cachedMetadata = [
            'features' => is_array($staleCached) && is_array($staleCached['features'] ?? null)
                ? $staleCached['features']
                : [],
            'date_ranges' => is_array($staleCached) && is_array($staleCached['date_ranges'] ?? null)
                ? $staleCached['date_ranges']
                : [],
        ];
        return $cachedMetadata;
    }
}

function homepage_search_indexed_slug_sets(): array
{
    $metadata = homepage_search_index_metadata();
    return is_array($metadata['features'] ?? null) ? $metadata['features'] : [];
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

function homepage_feature_search_indexed_count(string $featureKey, string $slug): int
{
    if (homepage_feature_search_doc_type($featureKey) === null) {
        return 0;
    }

    $slug = trim($slug);
    if ($slug === '') {
        return 0;
    }

    $sets = homepage_search_indexed_slug_sets();
    $featureSet = is_array($sets[$featureKey] ?? null) ? $sets[$featureKey] : [];
    return max(0, (int)($featureSet[$slug] ?? 0));
}

function homepage_feature_search_coverage(string $featureKey, string $slug): ?array
{
    if ($featureKey !== 'gijiroku') {
        return null;
    }

    $slug = trim($slug);
    if ($slug === '') {
        return null;
    }

    $metadata = homepage_search_index_metadata();
    $featureRanges = is_array($metadata['date_ranges'][$featureKey] ?? null)
        ? $metadata['date_ranges'][$featureKey]
        : [];
    $coverage = is_array($featureRanges[$slug] ?? null) ? $featureRanges[$slug] : null;
    if (!is_array($coverage)) {
        return null;
    }

    $from = trim((string)($coverage['from'] ?? ''));
    $to = trim((string)($coverage['to'] ?? ''));
    if (
        preg_match('/^\d{4}-\d{2}-\d{2}$/', $from) !== 1
        || preg_match('/^\d{4}-\d{2}-\d{2}$/', $to) !== 1
    ) {
        return null;
    }

    return [
        'from' => $from,
        'to' => $to,
        'document_count' => max(0, (int)($coverage['document_count'] ?? 0)),
    ];
}

function homepage_feature_card_display(
    string $featureKey,
    array $feature,
    ?array $primaryDisplay,
    ?array $publishDisplay,
    ?array $snapshotDisplay,
    bool $hasData,
    ?array $fallbackDisplayOverride = null
): ?array {
    $fallbackDisplay = $fallbackDisplayOverride
        ?? homepage_feature_fallback_display($featureKey, $feature, $snapshotDisplay);
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
    $freshnessLine = homepage_task_display_freshness_line($fallbackDisplay);
    if ($freshnessLine === '') {
        $freshnessLine = homepage_task_display_freshness_line($statusDisplay);
    }
    if ($freshnessLine !== '' && !in_array($freshnessLine, $detailLines, true)) {
        $detailLines[] = $freshnessLine;
    }
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
        // 反映タスクが成功していても、0件反映では公開データとは扱わない。
        // インデックスまたは実ファイルの存在を確認して、空の成功結果をカード化しない。
        $hasData = homepage_feature_search_indexed($featureKey, $slug)
            || municipality_feature_live_has_data_with_cache_heal($slug, $featureKey, $feature);
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

    $index = homepage_feature_registry_index($featureKey);

    $supportedSystemTypes = homepage_feature_supported_system_types($featureKey);
    $codes = [];
    foreach ($index as $code => $row) {
        if (!is_array($row)) {
            continue;
        }
        if (trim((string)($row['url'] ?? '')) === '') {
            continue;
        }
        $crawlStatus = trim((string)($row['crawl_status'] ?? ''));
        if ($crawlStatus !== '' && $crawlStatus !== 'enabled') {
            continue;
        }
        $systemType = trim((string)($row['system_type'] ?? ''));
        if ($supportedSystemTypes !== [] && !isset($supportedSystemTypes[$systemType])) {
            continue;
        }
        $codes[] = trim((string)$code);
    }
    $cache[$featureKey] = $codes;
    return $cache[$featureKey];
}

function homepage_feature_target_code_set(string $featureKey): array
{
    static $cache = [];
    if (!array_key_exists($featureKey, $cache)) {
        $cache[$featureKey] = array_fill_keys(homepage_feature_target_codes($featureKey), true);
    }
    return $cache[$featureKey];
}

function homepage_feature_supported_system_types(string $featureKey): array
{
    return match ($featureKey) {
        // tools/gijiroku/scrape_all_minutes.py の SUPPORTED_SYSTEMS / SUPPORTED_INPUT_SYSTEMS と同期する。
        'gijiroku' => array_fill_keys([
            'gijiroku.com',
            'voices',
            'kaigiroku.net',
            'dbsr',
            'db-search',
            'kaigiroku-indexphp',
            'kensakusystem',
            'amivoice',
            'msearch',
            'kami-city-pdf',
            'site-gikai-pdf',
            'static-kaigiroku-dir',
            '独自',
        ], true),
        // tools/reiki/scrape_all_reiki.py の SUPPORTED_SYSTEMS と同期する。
        'reiki' => array_fill_keys([
            'd1-law',
            'taikei',
            'g-reiki',
            'joureikun',
            'legalcrud',
            'reiki.html',
            'reiki_menu',
            'h-chosonkai',
            'jourei-v5',
            'legal-square',
        ], true),
        default => [],
    };
}

function homepage_feature_registry_index(string $featureKey): array
{
    static $cache = [];
    if (!array_key_exists($featureKey, $cache)) {
        $cache[$featureKey] = match ($featureKey) {
            'gijiroku' => load_system_url_index('municipalities/assembly_minutes_system_urls.tsv'),
            'reiki' => load_system_url_index('municipalities/reiki_system_urls.tsv'),
            default => [],
        };
    }
    return $cache[$featureKey];
}

/**
 * 取得元台帳だけで判定できる状態を返す。
 * 実行時エラー・公開済み・検索反映待ちは呼び出し側で優先して上書きする。
 */
// 台帳で「取得しない」と決めた取得元は、走らせない以上エラーも出ない。
// 過去に残った失敗記録より、台帳の判断を優先して表示する。
function homepage_registry_state_overrides_error(array $registryState): bool
{
    if (!($registryState['registered'] ?? false)) {
        return false;
    }
    return in_array(
        (string)($registryState['state'] ?? ''),
        ['excluded', 'unsupported', 'source_unresolved', 'review_required'],
        true
    );
}


function homepage_feature_registry_state(string $featureKey, string $municipalityCode): array
{
    $entry = homepage_feature_registry_index($featureKey)[$municipalityCode] ?? null;
    if (!is_array($entry)) {
        return ['registered' => false, 'state' => '', 'label' => '', 'detail' => '', 'system_type' => ''];
    }

    $sourceUrl = trim((string)($entry['url'] ?? ''));
    $systemType = trim((string)($entry['system_type'] ?? ''));
    $crawlStatus = strtolower(trim((string)($entry['crawl_status'] ?? '')));
    $detail = trim((string)($entry['exclusion_detail'] ?? ''));

    if ($sourceUrl === '' || $crawlStatus === 'unresolved') {
        return [
            'registered' => true,
            'state' => 'source_unresolved',
            'label' => '取得元未特定',
            'detail' => $detail !== '' ? $detail : '取得元URLをまだ特定できていません。',
            'system_type' => $systemType,
        ];
    }
    if (in_array($crawlStatus, ['excluded', 'disabled'], true)) {
        return [
            'registered' => true,
            'state' => 'excluded',
            'label' => '取得対象外',
            'detail' => $detail !== '' ? $detail : '取得方針により自動取得の対象外です。',
            'system_type' => $systemType,
        ];
    }
    if (in_array($crawlStatus, ['review_required', 'review'], true)) {
        return [
            'registered' => true,
            'state' => 'review_required',
            'label' => '取得可否確認中',
            'detail' => $detail !== '' ? $detail : '取得可否の確認が必要です。',
            'system_type' => $systemType,
        ];
    }

    $supported = homepage_feature_supported_system_types($featureKey);
    if ($systemType === '' || ($supported !== [] && !isset($supported[$systemType]))) {
        return [
            'registered' => true,
            'state' => 'unsupported',
            'label' => '未実装',
            'detail' => $systemType !== ''
                ? $systemType . ' 形式の取得処理は未実装です。'
                : '取得元の形式をまだ判定できていません。',
            'system_type' => $systemType,
        ];
    }

    return [
        'registered' => true,
        'state' => 'unacquired',
        'label' => '未取得',
        'detail' => '取得元と取得処理は登録済みですが、公開データをまだ取得していません。',
        'system_type' => $systemType,
    ];
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
    array $featureRuntimeStates,
    array $displayMunicipalities = []
): array {
    // 「分子/分母」だと、どこで何件落ちているかが読めない。全国の自治体から
    // 検索できる状態までを段階で数え、届かない分は理由別に内訳を出す。
    // 届かない理由は自治体カードが持つ availability_state が唯一の正なので、
    // ここで再判定せずそのまま集計する。カード側と数字がずれないようにする。
    $cardStates = [];
    foreach ($displayMunicipalities as $card) {
        if (!is_array($card)) {
            continue;
        }
        $cardSlug = (string)($card['slug'] ?? '');
        foreach (($card['visible_features'] ?? []) as $visibleFeature) {
            if (!is_array($visibleFeature)) {
                continue;
            }
            $cardStates[$cardSlug][(string)($visibleFeature['feature_key'] ?? '')] =
                trim((string)($visibleFeature['availability_state'] ?? ''));
        }
    }

    $summaries = [];
    foreach (['gijiroku', 'reiki'] as $featureKey) {
        $index = homepage_feature_registry_index($featureKey);
        $supported = homepage_feature_supported_system_types($featureKey);

        $urlResolved = 0;
        $urlUnresolved = 0;
        $blocked = 0;          // 認証が要るなど、取得しないと決めたもの
        $unsupported = 0;      // 取得元は分かるが形式が未実装
        // 取得元に本文が存在しない。こちらの方針ではなく相手の公開状況の話なので、
        // 実行時に判明する body_not_published と同じ「取得できない」に数える。
        $unavailable = 0;
        $targetCodes = [];
        foreach ($index as $code => $row) {
            if (!is_array($row)) {
                continue;
            }
            if (trim((string)($row['url'] ?? '')) === '') {
                $urlUnresolved += 1;
                continue;
            }
            $urlResolved += 1;
            $crawlStatus = trim((string)($row['crawl_status'] ?? ''));
            // 空欄と、書き出し不良で入った文字列 "None" は有効扱いにする。
            if ($crawlStatus !== '' && $crawlStatus !== 'enabled' && $crawlStatus !== 'None') {
                $reason = trim((string)($row['exclusion_reason'] ?? ''));
                if ($reason === 'video_only' || $reason === 'body_not_published') {
                    $unavailable += 1;
                } else {
                    $blocked += 1;
                }
                continue;
            }
            $systemType = trim((string)($row['system_type'] ?? ''));
            if ($supported !== [] && !isset($supported[$systemType])) {
                $unsupported += 1;
                continue;
            }
            $targetCodes[trim((string)$code)] = true;
        }

        $searchable = 0;
        $pending = 0;          // 取得済みで公開・索引待ち
        $notImplemented = 0;   // 取得元は辿れたが、この形式にまだ対応できていない
        $error = 0;
        foreach ($municipalities as $slug => $municipality) {
            if (!is_array($municipality)) {
                continue;
            }
            $code = trim((string)($municipality['code'] ?? ''));
            if ($code === '' || !isset($targetCodes[$code])) {
                continue;
            }
            $runtimeState = $featureRuntimeStates[(string)$slug][$featureKey] ?? null;
            if (is_array($runtimeState) && !empty($runtimeState['has_data']) && !empty($runtimeState['search_indexed'])) {
                $searchable += 1;
                continue;
            }
            $state = $cardStates[(string)$slug][$featureKey] ?? '';
            switch ($state) {
                // 取得元が本文を公開していない。待っても検索できるようにはならない。
                case 'excluded':
                case 'review_required':
                case 'body_not_published':
                    $unavailable += 1;
                    break;
                case 'not_found':
                case 'unsupported':
                    $notImplemented += 1;
                    break;
                case 'runtime_error':
                case 'update_error':
                case 'partial_error':
                case 'warning':
                    $error += 1;
                    break;
                default:
                    // publish_pending / search_pending / index_pending / unacquired など
                    $pending += 1;
                    break;
            }
        }

        $summaries[] = [
            'feature_key' => $featureKey,
            'label' => (string)($featureLabels[$featureKey] ?? $featureKey),
            'icon' => (string)($featureIcons[$featureKey] ?? ''),
            'municipality_count' => count($municipalities),
            'url_resolved' => $urlResolved,
            'url_unresolved' => $urlUnresolved,
            'blocked_count' => $blocked,
            'unsupported_count' => $unsupported,
            'target_count' => count($targetCodes),
            'searchable_count' => $searchable,
            'pending_count' => $pending,
            'not_implemented_count' => $notImplemented,
            'error_count' => $error,
            'unavailable_count' => $unavailable,
            // 旧フィールド。既存の利用箇所を壊さないため残す。
            'available_count' => $searchable,
            'text' => sprintf(
                '%s %s: 全国 %d / 取得元判明 %d / 検索できる %d',
                (string)($featureIcons[$featureKey] ?? ''),
                (string)($featureLabels[$featureKey] ?? $featureKey),
                count($municipalities),
                $urlResolved,
                $searchable
            ),
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

function homepage_task_failure_slug_key(array $item, string $fallbackSlug): string
{
    $slug = trim((string)($item['slug'] ?? $fallbackSlug));
    if ($slug === '') {
        $slug = $fallbackSlug;
    }
    $resolved = resolve_municipality_slug($slug);
    return $resolved !== '' ? $resolved : $slug;
}

function homepage_task_failure_is_stop(array $item): bool
{
    $message = trim((string)($item['message'] ?? ''));
    if (str_starts_with($message, '停止')) {
        return true;
    }

    $stopReturnCodes = [-15 => true, -2 => true, 130 => true, 143 => true];
    foreach (['returncode', 'scrape_returncode', 'index_returncode'] as $key) {
        if (!array_key_exists($key, $item) || $item[$key] === '' || $item[$key] === null) {
            continue;
        }
        if (isset($stopReturnCodes[(int)$item[$key]])) {
            return true;
        }
    }

    return false;
}

function homepage_task_failure_counts(array $taskStatus, array $indexTaskStatus = []): array
{
    $items = is_array($taskStatus['items'] ?? null) ? $taskStatus['items'] : [];
    $scrapeFailedSlugs = [];
    $indexFailedSlugs = [];

    foreach ($items as $rawSlug => $item) {
        if (!is_array($item)) {
            continue;
        }

        $slug = homepage_task_failure_slug_key($item, (string)$rawSlug);
        $status = trim((string)($item['status'] ?? ''));
        $indexStatus = trim((string)($item['index_status'] ?? ''));
        $hasScrapeReturncode = array_key_exists('scrape_returncode', $item)
            && $item['scrape_returncode'] !== ''
            && $item['scrape_returncode'] !== null;
        $scrapeReturncode = $hasScrapeReturncode ? (int)$item['scrape_returncode'] : null;
        $stopped = homepage_task_failure_is_stop($item);

        if ($indexStatus === 'failed' && !$stopped) {
            $indexFailedSlugs[$slug] = true;
        }

        if ($status !== 'failed' || $stopped) {
            continue;
        }

        if ($hasScrapeReturncode) {
            $returncode = array_key_exists('returncode', $item)
                && $item['returncode'] !== ''
                && $item['returncode'] !== null
                ? (int)$item['returncode']
                : null;
            if ((int)$scrapeReturncode !== 0 || ($returncode !== null && $returncode !== 0 && $indexStatus !== 'failed')) {
                $scrapeFailedSlugs[$slug] = true;
            }
            continue;
        }

        if ($indexStatus !== 'failed') {
            $scrapeFailedSlugs[$slug] = true;
        }
    }

    $indexItems = is_array($indexTaskStatus['items'] ?? null) ? $indexTaskStatus['items'] : [];
    foreach ($indexItems as $rawSlug => $item) {
        if (!is_array($item)) {
            continue;
        }
        $slug = homepage_task_failure_slug_key($item, (string)$rawSlug);
        if (trim((string)($item['status'] ?? '')) !== 'failed'
            || homepage_task_failure_is_stop($item)
        ) {
            continue;
        }
        $indexFailedSlugs[$slug] = true;
    }

    return [
        'scrape_failed' => count($scrapeFailedSlugs),
        'index_failed' => count($indexFailedSlugs),
    ];
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
    array $municipalities,
    array $indexTaskStatus = []
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
    $failureCounts = homepage_task_failure_counts($taskStatus, $indexTaskStatus);
    homepage_task_summary_append_stat($stats, '更新失敗', (string)(int)$failureCounts['index_failed']);
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
    $indexFailureStatus = is_array($taskDefinition['index_failure_status'] ?? null)
        ? $taskDefinition['index_failure_status']
        : [];
    if (in_array($taskKey, ['gijiroku', 'reiki'], true)) {
        $failureCounts = homepage_task_failure_counts($taskStatus, $indexFailureStatus);
        homepage_task_summary_append_stat($stats, '取得失敗', (string)(int)$failureCounts['scrape_failed']);
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
            ? homepage_scraper_index_summary(
                $taskStatus,
                $featureKey,
                $featureIcons,
                $featureRuntimeStates,
                $municipalities,
                $indexFailureStatus
            )
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
        $indexFailureTaskKey = trim((string)($taskDefinition['index_failure_task_key'] ?? ''));
        if ($indexFailureTaskKey !== '' && is_array($backgroundTaskStatuses[$indexFailureTaskKey] ?? null)) {
            $taskDefinition['index_failure_status'] = $backgroundTaskStatuses[$indexFailureTaskKey];
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
    array $featureRuntimeStates = [],
    bool $includeRegistryStates = false
): array {
    $visibleFeatures = [];
    $readyVisibleCount = 0;
    $municipalityCode = trim((string)($municipality['code'] ?? ''));

    foreach ($featureLabels as $featureKey => $label) {
        $targetCodeSet = homepage_feature_target_code_set($featureKey);
        $isPlannedTarget = $municipalityCode !== '' && isset($targetCodeSet[$municipalityCode]);
        $registryState = homepage_feature_registry_state($featureKey, $municipalityCode);
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
        $searchIndexedCount = $isSearchBacked
            ? homepage_feature_search_indexed_count($featureKey, $slug)
            : 0;
        $isEnabled = $hasData && (!$isSearchBacked || $searchIndexed);
        $display = homepage_feature_card_display(
            $featureKey,
            $feature,
            $primaryDisplay,
            $publishDisplay,
            is_array($displays['snapshot'] ?? null) ? $displays['snapshot'] : null,
            $hasData,
            is_array($displays['fallback'] ?? null) ? $displays['fallback'] : null
        );
        if ($isPlannedTarget && !$hasData && $display === null) {
            $display = [
                'label' => '取得予定',
                'class' => 'task-info',
                'detail' => '取得待ち',
                'progress_current' => null,
                'progress_total' => null,
            ];
        }
        if (
            $includeRegistryStates
            && !$hasData
            && $display === null
            && (bool)($registryState['registered'] ?? false)
        ) {
            $display = [
                'label' => (string)($registryState['label'] ?? ''),
                'class' => 'task-info',
                'detail' => (string)($registryState['detail'] ?? ''),
                'progress_current' => null,
                'progress_total' => null,
            ];
        }

        $hasError = homepage_task_display_has_error($display)
            || homepage_task_display_has_error($primaryDisplay)
            || homepage_task_display_has_error($publishDisplay);
        $hasWarning = homepage_task_display_has_warning($display)
            || homepage_task_display_has_warning($primaryDisplay)
            || homepage_task_display_has_warning($publishDisplay);
        $hasIssue = $hasError || $hasWarning;
        $acquisition = match ($featureKey) {
            'gijiroku' => homepage_gijiroku_acquisition_status(
                $feature,
                (string)($registryState['system_type'] ?? ($feature['system_type'] ?? '')),
                $hasData,
                $hasError,
                $searchIndexedCount,
                $display
            ),
            // 例規集は取得元の走査記録を持たないので、検索反映の遅れだけを見る。
            // 検索対象外の機能（選挙ポスター掲示場など）はここへ入れない。
            'reiki' => homepage_reiki_acquisition_status(
                (int)(is_array($display) ? ($display['count_current'] ?? 0) : 0),
                $searchIndexedCount,
                is_array($feature) ? $feature : [],
                $hasError
            ),
            default => ['state' => '', 'label' => '', 'detail' => '', 'source_coverage' => null],
        };
        $acquisitionState = trim((string)($acquisition['state'] ?? ''));
        $acquisitionDetail = trim((string)($acquisition['detail'] ?? ''));
        if ($acquisitionDetail !== '' && $hasData) {
            if (!is_array($display)) {
                $display = [
                    'label' => (string)($acquisition['label'] ?? ''),
                    'class' => 'task-info',
                    'detail' => $acquisitionDetail,
                ];
            } else {
                $existingDetail = trim((string)($display['detail'] ?? ''));
                if (!str_contains($existingDetail, $acquisitionDetail)) {
                    $display['detail'] = $existingDetail !== ''
                        ? ($existingDetail . "\n" . $acquisitionDetail)
                        : $acquisitionDetail;
                }
            }
        }
        if (in_array($acquisitionState, ['partial_error', 'update_error'], true)) {
            $hasError = true;
            $hasIssue = true;
        }
        $needsPublish = !$hasData && homepage_task_display_is_complete($primaryDisplay);
        $hasRegistryState = $includeRegistryStates && (bool)($registryState['registered'] ?? false);
        if (!$hasData && !$needsPublish && !$hasIssue && !$isPlannedTarget && !$hasRegistryState) {
            continue;
        }
        if (
            !$hasData
            && !$hasIssue
            && !$isPlannedTarget
            && !$hasRegistryState
            && homepage_unpublished_display_should_hide($display)
        ) {
            continue;
        }
        if (!$hasData && $display === null && !$isPlannedTarget && !$hasRegistryState) {
            continue;
        }

        // 公開中のデータに加え、enabled の取得予定対象は未着手でも「未公開」として残す。
        // 会議録以外も、検索反映が追いついていない間は「利用可能」と言わない。
        if ($isEnabled && $acquisitionState !== '') {
            $statusLabel = (string)($acquisition['label'] ?? '検索可');
            $statusClass = match ($acquisitionState) {
                'complete' => 'status-ready',
                'partial_error', 'update_error' => 'status-error',
                'partial_planned', 'index_pending' => 'status-needs-build',
                // 本文が公開されていないのは取得元の都合で、待っても変わらない。
                'body_not_published' => 'status-excluded',
                default => 'status-warning',
            };
            $availabilityState = $acquisitionState === 'complete' ? 'ready' : $acquisitionState;
            $mode = 'link';
            $readyVisibleCount += 1;
        } elseif ($isEnabled) {
            $statusLabel = '利用可能';
            $statusClass = 'status-ready';
            $availabilityState = 'ready';
            $mode = 'link';
            $readyVisibleCount += 1;
        } elseif ($hasData && $isSearchBacked && !$searchIndexed) {
            // 取得元が本文を公開していない場合、待っても検索できるようには
            // ならない。「準備中」と言うと反映を待てば使えると読めてしまう。
            if ($acquisitionState === 'body_not_published') {
                $statusLabel = (string)($acquisition['label'] ?? '本文なし（目次のみ公開）');
                $statusClass = 'status-excluded';
                $availabilityState = 'body_not_published';
            } else {
                $statusLabel = '検索準備中';
                $statusClass = 'status-needs-build';
                $availabilityState = 'search_pending';
            }
            $mode = 'disabled';
        } elseif ($hasData) {
            $statusLabel = '休止中';
            $statusClass = 'status-suspended';
            $availabilityState = 'suspended';
            $mode = 'disabled';
        } elseif ($hasError && !homepage_registry_state_overrides_error($registryState)) {
            // 走査は通ったが取得元に会議録が見当たらない場合がある。実装が
            // その形式に届いていないという話で、直せば取れる不具合ではない。
            if (homepage_task_display_found_nothing($display) || homepage_task_display_found_nothing($primaryDisplay)) {
                $statusLabel = '未対応（会議録を見つけられず）';
                $statusClass = 'status-unsupported';
                $availabilityState = 'not_found';
            } else {
                $statusLabel = '取得エラー（実装済み）';
                $statusClass = 'status-error';
                $availabilityState = 'runtime_error';
            }
            $mode = 'disabled';
        } elseif ($hasWarning) {
            $statusLabel = '警告あり';
            $statusClass = 'status-warning';
            $availabilityState = 'warning';
            $mode = 'disabled';
        } elseif ($needsPublish) {
            $statusLabel = '要反映';
            $statusClass = 'status-needs-build';
            $availabilityState = 'publish_pending';
            $mode = 'disabled';
        } elseif ($hasRegistryState && (string)($registryState['state'] ?? '') !== '') {
            $availabilityState = (string)$registryState['state'];
            $statusLabel = (string)($registryState['label'] ?? '未取得');
            $statusClass = match ($availabilityState) {
                'excluded', 'review_required' => 'status-excluded',
                'unsupported' => 'status-unsupported',
                'source_unresolved' => 'status-unresolved',
                default => 'status-unacquired',
            };
            $mode = 'disabled';
        } elseif ($isPlannedTarget) {
            $statusLabel = '未公開';
            $statusClass = 'status-unacquired';
            $availabilityState = 'unacquired';
            $mode = 'disabled';
        } else {
            $statusLabel = '未取得';
            $statusClass = 'status-unacquired';
            $availabilityState = 'unacquired';
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
            'availability_state' => $availabilityState,
            'system_type' => (string)($registryState['system_type'] ?? ''),
            'mode' => $mode,
            'acquisition_state' => $acquisitionState,
            'acquisition_label' => (string)($acquisition['label'] ?? ''),
            'acquisition_detail' => $acquisitionDetail,
            'source_coverage' => is_array($acquisition['source_coverage'] ?? null)
                ? $acquisition['source_coverage']
                : null,
            'has_error' => $hasError,
            'has_warning' => $hasWarning,
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

function homepage_document_feature_labels(): array
{
    return [
        'gijiroku' => '会議録',
        'reiki' => '例規集',
    ];
}

function homepage_document_feature_icons(): array
{
    return [
        'gijiroku' => '🏛️',
        'reiki' => '⚖️',
    ];
}

function homepage_build_context(bool $includeRegistryStates = false): array
{
    $municipalities = municipality_catalog();
    $featureLabels = homepage_document_feature_labels();
    $featureIcons = homepage_document_feature_icons();
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
            'index_failure_task_key' => 'gijiroku_reflect',
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
            'index_failure_task_key' => 'reiki_reflect',
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
        // 自治体文書（会議録・例規集）がどちらも非表示なら、自治体カード自体も出さない。
        $summary = homepage_collect_visible_features(
            $municipality,
            (string)$slug,
            $featureLabels,
            $featureIcons,
            $backgroundTaskStatuses,
            $backgroundTaskSnapshots,
            is_array($featureRuntimeStates[(string)$slug] ?? null) ? $featureRuntimeStates[(string)$slug] : [],
            $includeRegistryStates
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
                    (bool)($runtimeState['has_data'] ?? false),
                    is_array($runtimeState['displays']['fallback'] ?? null)
                        ? $runtimeState['displays']['fallback']
                        : null
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

function homepage_build_api_payload(bool $includeRegistryStates = false): array
{
    $context = homepage_build_context($includeRegistryStates);
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
        $featureRuntimeStates,
        $displayMunicipalities
    );

    $municipalityCards = [];
    foreach ($displayMunicipalities as $card) {
        if (!is_array($card)) {
            continue;
        }
        $municipality = is_array($card['municipality'] ?? null) ? $card['municipality'] : [];
        $features = [];
        $cardHasError = false;
        $cardHasWarning = false;
        foreach (($card['visible_features'] ?? []) as $item) {
            if (!is_array($item)) {
                continue;
            }
            $feature = is_array($item['feature'] ?? null) ? $item['feature'] : [];
            $featureKey = (string)($item['feature_key'] ?? '');
            $display = homepage_sanitize_home_card_display(
                is_array($item['display'] ?? null) ? $item['display'] : null
            );
            $featureHasError = (bool)($item['has_error'] ?? false);
            $featureHasWarning = (bool)($item['has_warning'] ?? false);
            $cardHasError = $cardHasError || $featureHasError;
            $cardHasWarning = $cardHasWarning || $featureHasWarning;
            $features[] = [
                'feature_key' => $featureKey,
                'label' => (string)($item['label'] ?? ''),
                'icon' => (string)($item['icon'] ?? ''),
                'title' => (string)($item['title'] ?? ''),
                'status_label' => (string)($item['status_label'] ?? ''),
                'status_class' => (string)($item['status_class'] ?? ''),
                'availability_state' => (string)($item['availability_state'] ?? ''),
                'system_type' => (string)($item['system_type'] ?? ''),
                'mode' => (string)($item['mode'] ?? 'disabled'),
                'url' => (string)($feature['url'] ?? ''),
                'display' => $display,
                'search_coverage' => homepage_feature_search_coverage(
                    $featureKey,
                    (string)($card['slug'] ?? '')
                ),
                'acquisition_state' => (string)($item['acquisition_state'] ?? ''),
                'acquisition_label' => (string)($item['acquisition_label'] ?? ''),
                'acquisition_detail' => (string)($item['acquisition_detail'] ?? ''),
                'source_coverage' => is_array($item['source_coverage'] ?? null)
                    ? $item['source_coverage']
                    : null,
                'has_error' => $featureHasError,
                'has_warning' => $featureHasWarning,
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
            'has_error' => $cardHasError,
            'has_warning' => $cardHasWarning,
            'features' => $features,
        ];
    }

    // 状態ごとの件数。収集状況ページはこれだけで描けるので、1794件のカードを
    // 送らずに済む。ラベルはカードが持っているものをそのまま使い、表記が
    // 二重管理にならないようにする。
    $stateCounts = [];
    foreach ($municipalityCards as $card) {
        foreach (($card['features'] ?? []) as $feature) {
            $stateFeatureKey = (string)($feature['feature_key'] ?? '');
            $stateName = (string)($feature['availability_state'] ?? '');
            if ($stateFeatureKey === '' || $stateName === '') {
                continue;
            }
            if (!isset($stateCounts[$stateFeatureKey][$stateName])) {
                $stateCounts[$stateFeatureKey][$stateName] = [
                    'state' => $stateName,
                    'label' => (string)($feature['status_label'] ?? $stateName),
                    'count' => 0,
                ];
            }
            $stateCounts[$stateFeatureKey][$stateName]['count'] += 1;
        }
    }
    $stateCountGroups = [];
    foreach (['gijiroku', 'reiki'] as $stateFeatureKey) {
        if (!isset($stateCounts[$stateFeatureKey])) {
            continue;
        }
        $rows = array_values($stateCounts[$stateFeatureKey]);
        usort($rows, static fn(array $a, array $b): int => $b['count'] <=> $a['count']);
        $stateCountGroups[] = [
            'feature_key' => $stateFeatureKey,
            'label' => (string)($featureLabels[$stateFeatureKey] ?? $stateFeatureKey),
            'icon' => (string)($featureIcons[$stateFeatureKey] ?? ''),
            'total' => array_sum(array_column($rows, 'count')),
            'states' => $rows,
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
        'state_counts' => $stateCountGroups,
        'task_state_summaries' => $taskStateSummaries,
        'running_tasks' => $runningTasks,
        'municipalities' => $municipalityCards,
    ];
}

function homepage_overlay_live_status(array $payload, bool $includeTaskStatusPayload = true): array
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

    if ($includeTaskStatusPayload) {
        $taskPayload = homepage_build_task_status_payload_cached();
        if (is_array($taskPayload['task_state_summaries'] ?? null)) {
            $payload['task_state_summaries'] = $taskPayload['task_state_summaries'];
        }
        if (is_array($taskPayload['running_tasks'] ?? null)) {
            $payload['running_tasks'] = $taskPayload['running_tasks'];
        }
    }
    return $payload;
}

function homepage_filter_document_catalog_payload(array $payload): array
{
    $allowedFeatures = array_fill_keys(array_keys(homepage_document_feature_labels()), true);

    foreach (['feature_summaries', 'task_state_summaries', 'running_tasks'] as $listKey) {
        if (!is_array($payload[$listKey] ?? null)) {
            continue;
        }
        $payload[$listKey] = array_values(array_filter(
            $payload[$listKey],
            static function ($item) use ($allowedFeatures): bool {
                return is_array($item)
                    && isset($allowedFeatures[trim((string)($item['feature_key'] ?? ''))]);
            }
        ));
    }

    if (!is_array($payload['municipalities'] ?? null)) {
        return $payload;
    }

    $municipalities = [];
    foreach ($payload['municipalities'] as $card) {
        if (!is_array($card) || !is_array($card['features'] ?? null)) {
            continue;
        }
        $features = array_values(array_filter(
            $card['features'],
            static function ($feature) use ($allowedFeatures): bool {
                return is_array($feature)
                    && isset($allowedFeatures[trim((string)($feature['feature_key'] ?? ''))]);
            }
        ));
        if ($features === []) {
            continue;
        }

        $availableLabels = [];
        foreach ($features as $feature) {
            if ((string)($feature['mode'] ?? '') === 'link') {
                $availableLabels[] = (string)($feature['label'] ?? '');
            }
        }
        $card['features'] = $features;
        $card['feature_count'] = count($features);
        $card['ready_visible_count'] = count($availableLabels);
        $card['available_summary'] = implode('・', array_filter($availableLabels));
        $municipalities[] = $card;
    }

    $payload['municipalities'] = $municipalities;
    $payload['display_municipality_count'] = count($municipalities);
    $payload['prefectures'] = homepage_prefecture_options_from_cards($municipalities);
    return $payload;
}

function homepage_sanitize_api_payload_displays(array $payload): array
{
    $payload = homepage_filter_document_catalog_payload($payload);
    unset($payload['task_state_summaries'], $payload['running_tasks']);

    if (!is_array($payload['municipalities'] ?? null)) {
        return $payload;
    }

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

    return $payload;
}

function homepage_api_cache_path(): string
{
    return data_path('background_tasks/home_api_payload.json');
}

function homepage_status_api_cache_path(): string
{
    return data_path('background_tasks/status_api_payload.json');
}

function homepage_status_api_cache_refresh_lock_path(): string
{
    return data_path('background_tasks/status_api_payload.lock');
}

function homepage_rebuild_status_api_payload_cache(): array
{
    $payload = homepage_build_api_payload(true);
    write_json_cache_file(homepage_status_api_cache_path(), $payload);
    return $payload;
}

function homepage_build_status_api_payload_cached(int $ttlSeconds = 60): array
{
    $cached = read_json_cache_file(homepage_status_api_cache_path(), $ttlSeconds);
    if (is_array($cached)) {
        if (!headers_sent()) {
            header('X-Status-Cache: hit');
        }
        return $cached;
    }

    $staleCached = read_json_cache_file(homepage_status_api_cache_path(), 0);
    if (is_array($staleCached) && PHP_SAPI !== 'cli') {
        homepage_schedule_status_api_payload_cache_refresh();
        if (!headers_sent()) {
            header('X-Status-Cache: stale');
        }
        return $staleCached;
    }

    $payload = homepage_rebuild_status_api_payload_cache();
    if (!headers_sent()) {
        header('X-Status-Cache: miss');
    }
    return $payload;
}

function homepage_schedule_status_api_payload_cache_refresh(): void
{
    static $scheduled = false;
    if ($scheduled || PHP_SAPI === 'cli') {
        return;
    }

    $lockPath = homepage_status_api_cache_refresh_lock_path();
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
            homepage_rebuild_status_api_payload_cache();
        } catch (Throwable $error) {
            error_log('[status_api] background cache refresh failed: ' . $error->getMessage());
        } finally {
            @flock($lockHandle, LOCK_UN);
            @fclose($lockHandle);
            @unlink($lockPath);
        }
    });
}

function homepage_api_cache_refresh_lock_path(): string
{
    return data_path('background_tasks/home_api_payload.lock');
}

function homepage_filtered_api_cache_path(?string $prefecture): string
{
    $key = trim((string)$prefecture);
    if ($key === '') {
        $key = 'all';
    }
    return data_path('background_tasks/home_api_filtered_v3_' . sha1($key) . '.json');
}

function homepage_filtered_api_cache_ttl_seconds(): int
{
    return 300;
}

function homepage_store_filtered_api_payload(string $path, array $payload): void
{
    write_json_cache_file($path, $payload);
}

function homepage_task_status_cache_path(): string
{
    return data_path('background_tasks/home_task_status_payload.json');
}

function homepage_task_status_cache_refresh_lock_path(): string
{
    return data_path('background_tasks/home_task_status_payload.lock');
}

function homepage_task_status_source_version(): string
{
    $parts = [];
    foreach ([
        'gijiroku',
        'reiki',
        'gijiroku_snapshot',
        'reiki_snapshot',
        'gijiroku_reflect',
        'reiki_reflect',
        'search_rebuild',
    ] as $task) {
        $path = background_task_status_path($task);
        $parts[] = $task . ':' . (is_file($path) ? ((string)@filemtime($path) . ':' . (string)@filesize($path)) : 'missing');
    }

    $searchIndexCachePath = homepage_search_index_cache_path();
    $parts[] = 'search_index:' . (is_file($searchIndexCachePath)
        ? ((string)@filemtime($searchIndexCachePath) . ':' . (string)@filesize($searchIndexCachePath))
        : 'missing');

    return sha1(implode('|', $parts));
}

function homepage_build_task_status_payload_cached(int $ttlSeconds = 10): array
{
    if ($ttlSeconds <= 0) {
        return homepage_build_task_status_payload();
    }

    $cachePath = homepage_task_status_cache_path();
    $cached = read_json_cache_file($cachePath, $ttlSeconds);
    if (is_array($cached) && is_array($cached['payload'] ?? null)) {
        if (!headers_sent()) {
            header('X-Homepage-Task-Status-Cache: hit');
        }
        return $cached['payload'];
    }

    $staleCached = read_json_cache_file($cachePath, 0);
    if (is_array($staleCached) && is_array($staleCached['payload'] ?? null)) {
        homepage_schedule_task_status_payload_cache_refresh();
        if (!headers_sent()) {
            header('X-Homepage-Task-Status-Cache: stale');
        }
        return $staleCached['payload'];
    }

    $payload = homepage_build_task_status_payload();
    write_json_cache_file($cachePath, [
        'source_version' => homepage_task_status_source_version(),
        'payload' => $payload,
    ]);
    if (!headers_sent()) {
        header('X-Homepage-Task-Status-Cache: miss');
    }
    return $payload;
}

function homepage_schedule_task_status_payload_cache_refresh(): void
{
    static $scheduled = false;
    if ($scheduled || PHP_SAPI === 'cli') {
        return;
    }

    $lockPath = homepage_task_status_cache_refresh_lock_path();
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
            $payload = homepage_build_task_status_payload();
            write_json_cache_file(homepage_task_status_cache_path(), [
                'source_version' => homepage_task_status_source_version(),
                'payload' => $payload,
            ]);
        } catch (Throwable $error) {
            error_log('[task_status_api] background cache refresh failed: ' . $error->getMessage());
        } finally {
            @flock($lockHandle, LOCK_UN);
            @fclose($lockHandle);
            @unlink($lockPath);
        }
    });
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
            if (!is_array($item)) {
                continue;
            }
            $slug = resolve_municipality_slug((string)($item['slug'] ?? $rawSlug));
            if ($slug === '') {
                continue;
            }
            if (trim((string)($item['status'] ?? '')) === 'failed' && !homepage_task_failure_is_stop($item)) {
                unset($complete[$slug]);
                continue;
            }
            if (homepage_task_status_item_is_complete($item)) {
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
    $targetCount = count(array_filter(
        homepage_feature_target_codes($featureKey),
        static fn(mixed $code): bool => trim((string)$code) !== ''
    ));

    $dataSlugs = homepage_task_status_data_slugs($completionStatus, $liveStatus);
    if ($dataSlugs !== [] && $targetCount > 0) {
        $completeSlugs = homepage_task_status_completed_slugs($completionStatus, $liveStatus);
        return min(count($completeSlugs), $targetCount) . '/' . $targetCount;
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
    $indexRuntimeStatus = $indexStatus !== [] ? $indexStatus : $taskStatus;
    [$stateLabel, $stateClass] = homepage_task_status_index_state($indexRuntimeStatus);
    $capacity = max(1, (int)($indexRuntimeStatus['index_capacity'] ?? 1));
    $active = max(0, (int)($indexRuntimeStatus['index_active_count'] ?? 0));
    $stats = [];
    $stats[] = ['label' => '稼働', 'value' => $active . '/' . $capacity];
    $completed = homepage_task_status_index_value(
        $taskKey === 'reiki' ? 'reiki' : 'gijiroku',
        $baselineIndex,
        $completionStatus,
        $taskStatus
    );
    if ($completed !== '') {
        $stats[] = ['label' => '検索可', 'value' => $completed];
    }
    $failureCounts = homepage_task_failure_counts($taskStatus, $indexStatus);
    $stats[] = ['label' => '更新失敗', 'value' => (string)(int)$failureCounts['index_failed']];
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
    $completeSlugs = homepage_task_status_completed_slugs($completionStatus, $liveStatus);
    if ($completeSlugs === []) {
        return '';
    }

    $sets = homepage_search_indexed_slug_sets();
    $indexed = is_array($sets[$featureKey] ?? null) ? $sets[$featureKey] : [];
    if ($indexed === []) {
        return '';
    }

    $indexedCount = 0;
    foreach ($completeSlugs as $slug => $_) {
        if (isset($indexed[$slug]) && (int)$indexed[$slug] > 0) {
            $indexedCount += 1;
        }
    }
    $total = count($completeSlugs);
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
        if (in_array($taskKey, ['gijiroku', 'reiki'], true)) {
            $indexStatus = is_array($options['index_status'] ?? null) ? $options['index_status'] : [];
            $failureCounts = homepage_task_failure_counts($taskStatus, $indexStatus);
            $stats[] = ['label' => '取得失敗', 'value' => (string)(int)$failureCounts['scrape_failed']];
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
        $indexStatus = is_array($statuses[$taskKey . '_reflect'] ?? null) ? $statuses[$taskKey . '_reflect'] : [];
        if ($indexStatus !== []) {
            array_push(
                $runningTasks,
                ...homepage_task_status_running_tasks_from_items(
                    $taskKey,
                    (string)$definition['feature_key'],
                    (string)$definition['label'],
                    (string)$definition['icon'],
                    $indexStatus
                )
            );
        }
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
