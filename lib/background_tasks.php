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
    if (!is_file($path)) {
        return [];
    }

    $decoded = json_decode((string)file_get_contents($path), true);
    if (!is_array($decoded)) {
        return [];
    }
    management_db_store_task_status($task, $decoded);
    return $decoded;
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
    ];
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
    if ($timeLabel !== '') {
        $detailParts[] = '更新 ' . $timeLabel;
    }
    $heartbeatDetail = background_task_item_running_heartbeat_detail($taskStatus, $item);
    if ($heartbeatDetail !== '') {
        $detailParts[] = $heartbeatDetail;
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
        ];
    }
    if ($running && $status === 'running') {
        $label = $customRunningLabel !== '' ? $customRunningLabel : ($isReflectTask ? '反映中' : 'スクレイピング中');
        if ($message === 'インデックス更新中') {
            $label = $message;
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
        ];
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
        ];
    }
    if ($status === 'done' || $status === 'ok') {
        return [
            'label' => background_task_item_is_complete($item) ? '完了' : ($isReflectTask ? '前回反映成功' : '前回更新成功'),
            'class' => 'task-done',
            'detail' => $detail,
            'progress_current' => $progress['current'],
            'progress_total' => $progress['total'],
            'batch_running' => $running,
        ];
    }
    if ($status === 'failed') {
        $returncode = $item['returncode'] ?? null;
        if ($returncode !== null && $returncode !== '') {
            if ($detail === '') {
                $detail = 'rc=' . (string)$returncode;
            } else {
                $detailLines = preg_split('/\R/u', $detail, -1, PREG_SPLIT_NO_EMPTY) ?: [];
                if ($detailLines === []) {
                    $detailLines[] = 'rc=' . (string)$returncode;
                } else {
                    $detailLines[count($detailLines) - 1] .= ' / rc=' . (string)$returncode;
                }
                $detail = implode("\n", $detailLines);
            }
        }
        return [
            'label' => $isReflectTask ? '直近反映失敗' : '直近失敗',
            'class' => 'task-failed',
            'detail' => $detail,
            'progress_current' => $progress['current'],
            'progress_total' => $progress['total'],
            'batch_running' => $running,
        ];
    }

    return null;
}
