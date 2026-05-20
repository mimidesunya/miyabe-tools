<?php
declare(strict_types=1);

require_once __DIR__ . DIRECTORY_SEPARATOR . 'municipalities.php';
require_once __DIR__ . DIRECTORY_SEPARATOR . 'management_db.php';

// スクレイピングの live task JSON と snapshot JSON を読み、UI 向け表示へ整形する。

function background_task_status_path(string $task): string
{
    $task = trim($task);
    if ($task === '') {
        return data_path('background_tasks/unknown.json');
    }
    return data_path('background_tasks/' . $task . '.json');
}

function load_background_task_status(string $task): array
{
    $path = background_task_status_path($task);
    if (is_file($path)) {
        $sourceMtime = (float)@filemtime($path);
        $cached = management_db_task_status_if_fresh($task, $sourceMtime);
        if (is_array($cached)) {
            return $cached;
        }

        $decoded = json_decode((string)file_get_contents($path), true);
        if (is_array($decoded)) {
            management_db_store_task_status($task, $decoded, $sourceMtime);
            return $decoded;
        }
    }

    $dbStatus = management_db_task_status($task);
    if (is_array($dbStatus)) {
        return $dbStatus;
    }
    return [];
}

function load_background_task_status_fast(string $task): array
{
    $path = background_task_status_path($task);
    if (!is_file($path)) {
        $dbStatus = management_db_task_status($task);
        return is_array($dbStatus) ? $dbStatus : [];
    }
    $sourceMtime = (float)@filemtime($path);
    $cached = management_db_task_status_if_fresh($task, $sourceMtime);
    if (is_array($cached)) {
        return $cached;
    }
    return load_background_task_status($task);
}

function background_task_is_stale(array $taskStatus, int $staleSeconds = 900): bool
{
    if (!(bool)($taskStatus['running'] ?? false)) {
        return false;
    }

    // updated_at は「件数や表示文が最後に変わった時刻」で、
    // 長い build 中は止めておきたい。死活監視は heartbeat_at を優先する。
    $updatedAt = app_parse_timestamp_tokyo_unix((string)($taskStatus['heartbeat_at'] ?? ($taskStatus['updated_at'] ?? '')));
    if ($updatedAt === null) {
        return false;
    }
    return (time() - $updatedAt) > $staleSeconds;
}

function background_task_item_running_heartbeat_detail(array $taskStatus, array $item): string
{
    if (!(bool)($taskStatus['running'] ?? false)) {
        return '';
    }

    $status = trim((string)($item['status'] ?? ''));
    if (!in_array($status, ['pending', 'running'], true)) {
        return '';
    }

    $heartbeatAt = trim((string)($taskStatus['heartbeat_at'] ?? ''));
    if ($heartbeatAt === '') {
        return '';
    }

    $heartbeatUnix = app_parse_timestamp_tokyo_unix($heartbeatAt);
    if ($heartbeatUnix === null) {
        return '';
    }

    $progressUpdatedAt = trim((string)($item['progress_updated_at'] ?? ''));
    $updatedAt = trim((string)($item['updated_at'] ?? ''));
    $itemUpdatedUnix = app_parse_timestamp_tokyo_unix($progressUpdatedAt !== '' ? $progressUpdatedAt : $updatedAt);
    if ($itemUpdatedUnix !== null && $heartbeatUnix <= $itemUpdatedUnix) {
        return '';
    }

    if ($itemUpdatedUnix !== null && ($heartbeatUnix - $itemUpdatedUnix) < 30) {
        return '';
    }

    return '応答 ' . $heartbeatAt;
}

function background_task_item_progress_numbers(array $item): array
{
    $currentRaw = $item['progress_current'] ?? null;
    $totalRaw = $item['progress_total'] ?? null;
    if ($currentRaw === null || $totalRaw === null) {
        return ['current' => null, 'total' => null];
    }

    $total = max(0, (int)$totalRaw);
    if ($total <= 0) {
        return ['current' => null, 'total' => null];
    }

    $current = min(max(0, (int)$currentRaw), $total);
    return ['current' => $current, 'total' => $total];
}

function background_task_item_progress_detail(array $item): string
{
    $progress = background_task_item_progress_numbers($item);
    $current = $progress['current'];
    $total = $progress['total'];
    if ($current === null || $total === null) {
        return '';
    }
    $status = trim((string)($item['status'] ?? ''));
    if ($current >= $total && $status !== 'running') {
        return sprintf('%d件', $current);
    }
    return sprintf('%d/%d件', $current, $total);
}

function background_task_public_text(string $value): string
{
    $value = trim($value);
    if ($value === '') {
        return '';
    }

    if (preg_match('/(?:^|\s)result:\s+\S+.*\bstderr\s+(\d+)\s+bytes\b/i', $value, $matches) === 1) {
        return '処理結果の出力後にエラー出力あり ' . (string)$matches[1] . 'バイト';
    }
    if (preg_match('/^result:\s+\S+/i', $value) === 1) {
        return '処理結果を出力しました';
    }
    if (preg_match('/^\[WARN\]\s+No regulations found\.?$/i', $value) === 1) {
        return '例規本文を検出できませんでした（対象サイトの構造変更または空ページの可能性）';
    }

    $value = preg_replace('/(?<!:)\/(?:mnt|var|workspace|home|tmp|srv|opt)\/[^\s,;:)]+/i', '内部ファイル', $value) ?? $value;
    $value = preg_replace('/[A-Za-z]:[\\\\\/][^\s,;:)]+/', '内部ファイル', $value) ?? $value;
    return $value;
}

function background_task_compact_detail_text(string $value, int $maxLength = 96): string
{
    $value = trim($value);
    if ($value === '') {
        return '';
    }
    $value = preg_replace('/\x1b\[[0-9;?]*[A-Za-z]/', '', $value) ?? $value;
    $value = preg_replace('/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]+/', ' ', $value) ?? $value;
    $value = preg_replace('/\s+/u', ' ', $value) ?? $value;
    $value = trim($value);
    if ($value === '') {
        return '';
    }

    foreach (['[INFO] ', '[DONE] '] as $prefix) {
        if (str_starts_with($value, $prefix)) {
            $value = trim(substr($value, strlen($prefix)));
            break;
        }
    }

    if (str_starts_with($value, '[PROGRESS] ')) {
        return '';
    }
    $value = background_task_public_text($value);
    if ($value === '') {
        return '';
    }
    if (preg_match('/^\[\d+\/\d+\]\s*(.*)$/u', $value, $matches) === 1) {
        $value = trim((string)$matches[1]);
        if ($value === '') {
            $value = '処理中';
        }
    }
    if (preg_match('/\b(downloaded|checked|skipped|parsed|reused)=\d+\b/u', $value) === 1) {
        $value = '既存データを確認中';
    }
    if (preg_match('/^Found\s+\d+\s+(?:unique regulation IDs|ordinance pages)\b/i', $value) === 1) {
        $value = '例規一覧を確認中';
    }
    if (preg_match('/^stderr\s+(\d+)\s+bytes$/i', $value, $matches) === 1) {
        return 'エラー出力あり ' . (string)$matches[1] . 'バイト';
    }
    if ($value === 'starting...') {
        return '起動中';
    }

    if (function_exists('mb_strlen') && function_exists('mb_substr')) {
        if (mb_strlen($value, 'UTF-8') > $maxLength) {
            return rtrim(mb_substr($value, 0, max(1, $maxLength - 3), 'UTF-8')) . '...';
        }
        return $value;
    }

    if (strlen($value) > $maxLength) {
        return rtrim(substr($value, 0, max(1, $maxLength - 3))) . '...';
    }
    return $value;
}

function background_task_item_activity_detail(array $item, string $prefix = '作業'): string
{
    $message = background_task_compact_detail_text((string)($item['message'] ?? ''));
    if ($message === '') {
        return '';
    }
    $prefix = trim($prefix);
    return ($prefix !== '' ? ($prefix . ' ') : '') . $message;
}

function background_task_item_freshness_detail(array $item): string
{
    $date = trim((string)($item['freshness_date'] ?? ''));
    if ($date === '' || preg_match('/^\d{4}-\d{2}-\d{2}$/', $date) !== 1) {
        return '';
    }
    $basis = trim((string)($item['freshness_basis'] ?? ''));
    $label = match ($basis) {
        'content_current' => '内容現在',
        'latest_document' => '最新日付',
        default => '鮮度',
    };
    return $label . ' ' . $date;
}

function background_task_display_freshness_fields(array $item): array
{
    return [
        'freshness_date' => trim((string)($item['freshness_date'] ?? '')),
        'freshness_basis' => trim((string)($item['freshness_basis'] ?? '')),
        'last_checked_at' => trim((string)($item['last_checked_at'] ?? '')),
    ];
}

function background_task_readable_log_path(string $rawPath): string
{
    $rawPath = trim($rawPath);
    if ($rawPath === '') {
        return '';
    }

    $candidates = [];
    $normalized = str_replace(['\\', '/'], DIRECTORY_SEPARATOR, $rawPath);
    $isAbsolute = preg_match('/^(?:[A-Za-z]:)?[\\\\\/]/', $rawPath) === 1;
    if ($isAbsolute) {
        $candidates[] = $normalized;
    } else {
        $candidates[] = project_root_path() . DIRECTORY_SEPARATOR . $normalized;
        $candidates[] = dirname(project_root_path()) . DIRECTORY_SEPARATOR . $normalized;
    }

    foreach ($candidates as $candidate) {
        if (is_file($candidate) && is_readable($candidate)) {
            return $candidate;
        }
    }
    return '';
}

function background_task_tail_log_lines(string $path, int $maxBytes = 4096, int $maxLines = 10): array
{
    if ($path === '' || !is_file($path) || !is_readable($path)) {
        return [];
    }
    $size = (int)@filesize($path);
    if ($size <= 0) {
        return [];
    }
    $handle = @fopen($path, 'rb');
    if ($handle === false) {
        return [];
    }
    try {
        $readSize = min($size, $maxBytes);
        if (@fseek($handle, -$readSize, SEEK_END) !== 0) {
            @fseek($handle, 0, SEEK_SET);
        }
        $chunk = (string)@fread($handle, $readSize);
    } finally {
        @fclose($handle);
    }
    if ($chunk === '') {
        return [];
    }
    if (function_exists('mb_convert_encoding')) {
        $chunk = @mb_convert_encoding($chunk, 'UTF-8', 'UTF-8, SJIS-win, CP932, EUC-JP, ISO-2022-JP') ?: $chunk;
    }
    $lines = preg_split('/\R/u', $chunk, -1, PREG_SPLIT_NO_EMPTY) ?: [];
    $cleaned = [];
    foreach ($lines as $line) {
        $line = background_task_compact_detail_text((string)$line, 180);
        if ($line !== '') {
            $cleaned[] = $line;
        }
    }
    return array_slice($cleaned, -$maxLines);
}

function background_task_item_failure_log_lines(array $item): array
{
    $failureLines = [];
    $message = background_task_compact_detail_text((string)($item['message'] ?? ''), 180);
    if ($message !== '') {
        $failureLines[] = '失敗理由: ' . $message;
    }
    $returncode = $item['returncode'] ?? null;
    if ($returncode !== null && $returncode !== '') {
        $failureLines[] = '終了コード: ' . (string)$returncode;
    }

    $candidates = [
        'index_stderr_log' => 'インデックスのエラー出力',
        'stderr_log' => 'スクレイピングのエラー出力',
        'index_stdout_log' => 'インデックスの標準出力',
        'stdout_log' => 'スクレイピングの標準出力',
    ];

    foreach ($candidates as $field => $label) {
        $path = background_task_readable_log_path((string)($item[$field] ?? ''));
        if ($path === '') {
            continue;
        }
        $lines = background_task_tail_log_lines($path);
        if ($lines !== []) {
            array_unshift($lines, $label . ' 末尾');
            return array_merge($failureLines, $lines);
        }
    }
    if ($failureLines !== []) {
        $failureLines[] = '詳細ログは公開画面では表示していません';
    }
    return $failureLines;
}

function background_task_item_warning_lines(array $item): array
{
    $rawLines = $item['warning_lines'] ?? null;
    $warningLines = [];
    if (is_array($rawLines)) {
        foreach ($rawLines as $line) {
            $line = background_task_compact_detail_text((string)$line, 220);
            if ($line !== '' && !in_array($line, $warningLines, true)) {
                $warningLines[] = $line;
            }
        }
    }

    $warningCount = max(0, (int)($item['warning_count'] ?? 0));
    if ($warningLines !== [] || $warningCount <= 0) {
        return array_slice($warningLines, -20);
    }

    foreach (['index_stderr_log', 'stderr_log', 'index_stdout_log', 'stdout_log'] as $field) {
        $path = background_task_readable_log_path((string)($item[$field] ?? ''));
        if ($path === '') {
            continue;
        }
        foreach (background_task_tail_log_lines($path, 32768, 40) as $line) {
            if (
                str_contains($line, '[WARN]')
                || str_contains(strtoupper($line), 'WARNING')
                || str_contains($line, '警告')
            ) {
                $warningLines[] = $line;
            }
        }
    }
    $warningLines = array_slice(array_values(array_unique($warningLines)), -20);
    if ($warningLines === [] && $warningCount > 0) {
        return ['警告が記録されましたが、詳細ログを取得できませんでした'];
    }
    return $warningLines;
}

function background_task_item_warning_count(array $item, array $warningLines): int
{
    return max(count($warningLines), max(0, (int)($item['warning_count'] ?? 0)));
}

function background_task_item_is_complete(array $item): bool
{
    $progress = background_task_item_progress_numbers($item);
    $current = $progress['current'];
    $total = $progress['total'];
    if ($current === null || $total === null) {
        return false;
    }
    return $current >= $total;
}

function background_task_item_has_started(array $item): bool
{
    if (trim((string)($item['started_at'] ?? '')) !== '') {
        return true;
    }
    $progress = background_task_item_progress_numbers($item);
    return $progress['current'] !== null && $progress['total'] !== null;
}

function background_task_item(array $taskStatus, string $slug): ?array
{
    $items = $taskStatus['items'] ?? null;
    if (!is_array($items)) {
        return null;
    }

    return isset($items[$slug]) && is_array($items[$slug]) ? $items[$slug] : null;
}

function background_task_item_fallback_display(array $taskStatus, string $slug): ?array
{
    $item = background_task_item($taskStatus, $slug);
    if (!is_array($item)) {
        return null;
    }

    $detail = background_task_item_progress_detail($item);
    if ($detail === '') {
        return null;
    }

    return [
        'label' => background_task_item_is_complete($item) ? '完了' : '取得状況',
        'class' => background_task_item_is_complete($item) ? 'task-done' : 'task-info',
        'detail' => $detail,
        'progress_current' => background_task_item_progress_numbers($item)['current'],
        'progress_total' => background_task_item_progress_numbers($item)['total'],
        'batch_running' => (bool)($taskStatus['running'] ?? false),
    ] + background_task_display_freshness_fields($item);
}

// 生の status を、そのまま画面へ出せるラベルと詳細文へ変換する。
function background_task_item_display(array $taskStatus, string $slug): ?array
{
    $item = background_task_item($taskStatus, $slug);
    if (!is_array($item)) {
        return null;
    }

    $status = trim((string)($item['status'] ?? ''));
    $message = trim((string)($item['message'] ?? ''));
    $running = (bool)($taskStatus['running'] ?? false);
    $stale = background_task_is_stale($taskStatus);
    $taskName = trim((string)($taskStatus['task'] ?? ''));
    $customRunningLabel = trim((string)($taskStatus['running_label'] ?? ''));
    $isReflectTask = str_ends_with($taskName, '_reflect');
    $hasStarted = background_task_item_has_started($item);
    $progress = background_task_item_progress_numbers($item);
    $updatedAt = trim((string)($item['updated_at'] ?? ($taskStatus['updated_at'] ?? '')));
    $progressUpdatedAt = trim((string)($item['progress_updated_at'] ?? ''));
    $finishedAt = trim((string)($item['finished_at'] ?? ''));
    $detailParts = [];
    $itemProgress = background_task_item_progress_detail($item);
    if ($itemProgress !== '') {
        $detailParts[] = $itemProgress;
    } elseif (in_array($status, ['done', 'ok', 'failed'], true)) {
        $detailParts[] = '件数未集計';
    }
    $freshnessDetail = background_task_item_freshness_detail($item);
    if ($freshnessDetail !== '') {
        $detailParts[] = $freshnessDetail;
    }
    $activityDetail = background_task_item_activity_detail($item, $status === 'failed' ? '理由' : '作業');
    if ($activityDetail !== '' && in_array($status, ['pending', 'running', 'failed'], true)) {
        $detailParts[] = $activityDetail;
    }
    if ($finishedAt !== '') {
        $timeLabel = $finishedAt;
    } elseif ($itemProgress !== '' && $progressUpdatedAt !== '') {
        // 件数つきの表示では、task JSON の heartbeat ではなく最後に件数が動いた時刻を見せる。
        $timeLabel = $progressUpdatedAt;
    } elseif ($running && in_array($status, ['pending', 'running'], true)) {
        // 進行中なのに件数を出せないケースでは、heartbeat 由来の updated_at を見せない。
        // ここを表示すると「件数は動かないのに更新だけ進む」と誤解されやすい。
        $timeLabel = '';
    } else {
        $timeLabel = $updatedAt;
    }
    if ($timeLabel !== '' && !($running && in_array($status, ['pending', 'running'], true))) {
        $detailParts[] = '更新 ' . $timeLabel;
    }
    $warningLines = background_task_item_warning_lines($item);
    $warningCount = background_task_item_warning_count($item, $warningLines);
    if ($warningCount > 0) {
        $detailParts[] = '警告あり ' . (string)$warningCount . '件';
    }
    $detail = implode("\n", $detailParts);

    if ($stale && in_array($status, ['pending', 'running'], true) && !$hasStarted) {
        return null;
    }
    if ($stale && in_array($status, ['pending', 'running'], true)) {
        return [
            'label' => '停止の可能性',
            'class' => 'task-stale',
            'detail' => $detail,
            'progress_current' => $progress['current'],
            'progress_total' => $progress['total'],
            'batch_running' => $running,
            'warning_lines' => $warningLines,
        ] + background_task_display_freshness_fields($item);
    }
    if ($running && $status === 'running') {
        $label = $customRunningLabel !== '' ? $customRunningLabel : ($isReflectTask ? '反映中' : 'スクレイピング中');
        if (str_contains($message, 'インデックス待機中')) {
            $label = 'インデックス待機中';
        } elseif (str_contains($message, 'インデックス')) {
            $label = 'インデックス更新中';
        } elseif ($isReflectTask && $message !== '' && $message !== '反映中') {
            $label = $message;
        }
        return [
            'label' => $label,
            'class' => 'task-running',
            'detail' => $detail,
            'progress_current' => $progress['current'],
            'progress_total' => $progress['total'],
            'batch_running' => $running,
            'warning_lines' => $warningLines,
        ] + background_task_display_freshness_fields($item);
    }
    if ($running && $status === 'pending') {
        // まだ着手していない queued item は、自治体カードに出しても
        // 「未公開の空箱」にしか見えないためトップ一覧では非表示にする。
        return null;
    }
    if ($status === 'snapshot') {
        return [
            'label' => background_task_item_is_complete($item) ? '完了' : '取得状況',
            'class' => background_task_item_is_complete($item) ? 'task-done' : 'task-info',
            'detail' => $detail,
            'progress_current' => $progress['current'],
            'progress_total' => $progress['total'],
            'batch_running' => $running,
            'warning_lines' => $warningLines,
        ] + background_task_display_freshness_fields($item);
    }
    if ($status === 'done' || $status === 'ok') {
        return [
            'label' => background_task_item_is_complete($item) ? '完了' : ($isReflectTask ? '前回反映成功' : '前回更新成功'),
            'class' => 'task-done',
            'detail' => $detail,
            'progress_current' => $progress['current'],
            'progress_total' => $progress['total'],
            'batch_running' => $running,
            'warning_lines' => $warningLines,
        ] + background_task_display_freshness_fields($item);
    }
    if ($status === 'failed') {
        if (background_task_item_is_complete($item)) {
            $completeDetailLines = preg_split('/\R/u', $detail, -1, PREG_SPLIT_NO_EMPTY) ?: [];
            $completeDetailLines = array_values(array_filter(
                $completeDetailLines,
                static fn($line): bool => !str_starts_with(trim((string)$line), '理由 ')
            ));
            $completeDetail = implode("\n", $completeDetailLines);
            return [
                'label' => '完了',
                'class' => 'task-done',
                'detail' => $completeDetail,
                'progress_current' => $progress['current'],
                'progress_total' => $progress['total'],
                'batch_running' => $running,
                'warning_lines' => $warningLines,
            ] + background_task_display_freshness_fields($item);
        }
        $returncode = $item['returncode'] ?? null;
        if ($returncode !== null && $returncode !== '') {
            if ($detail === '') {
                $detail = '終了コード ' . (string)$returncode;
            } else {
                $detailLines = preg_split('/\R/u', $detail, -1, PREG_SPLIT_NO_EMPTY) ?: [];
                if ($detailLines === []) {
                    $detailLines[] = '終了コード ' . (string)$returncode;
                } else {
                    $detailLines[count($detailLines) - 1] .= ' / 終了コード ' . (string)$returncode;
                }
                $detail = implode("\n", $detailLines);
            }
        }
        return [
            'label' => $isReflectTask ? '直近反映失敗' : '直近失敗',
            'class' => 'task-failed',
            'detail' => $detail,
            'log_lines' => background_task_item_failure_log_lines($item),
            'progress_current' => $progress['current'],
            'progress_total' => $progress['total'],
            'batch_running' => $running,
            'warning_lines' => $warningLines,
        ] + background_task_display_freshness_fields($item);
    }

    return null;
}
