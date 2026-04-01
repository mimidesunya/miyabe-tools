<?php
declare(strict_types=1);

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
    return is_array($decoded) ? $decoded : [];
}

function background_task_is_stale(array $taskStatus, int $staleSeconds = 900): bool
{
    if (!(bool)($taskStatus['running'] ?? false)) {
        return false;
    }

    $updatedAt = strtotime((string)($taskStatus['updated_at'] ?? ''));
    if ($updatedAt === false) {
        return false;
    }
    return (time() - $updatedAt) > $staleSeconds;
}

function background_task_item_display(array $taskStatus, string $slug): ?array
{
    $items = $taskStatus['items'] ?? null;
    if (!is_array($items) || !isset($items[$slug]) || !is_array($items[$slug])) {
        return null;
    }

    $item = $items[$slug];
    $status = trim((string)($item['status'] ?? ''));
    $running = (bool)($taskStatus['running'] ?? false);
    $stale = background_task_is_stale($taskStatus);
    $totalCount = (int)($taskStatus['total_count'] ?? 0);
    $completedCount = (int)($taskStatus['completed_count'] ?? 0);
    $updatedAt = trim((string)($item['updated_at'] ?? ($taskStatus['updated_at'] ?? '')));
    $finishedAt = trim((string)($item['finished_at'] ?? ''));
    $timeLabel = $finishedAt !== '' ? $finishedAt : $updatedAt;
    $detailParts = [];
    $progressPercent = $totalCount > 0 ? max(0.0, min(100.0, ($completedCount / $totalCount) * 100.0)) : 0.0;
    if ($running && $totalCount > 0) {
        $detailParts[] = sprintf('バッチ %d/%d 完了', $completedCount, $totalCount);
    }
    if ($timeLabel !== '') {
        $detailParts[] = '更新 ' . $timeLabel;
    }
    $detail = implode(' / ', $detailParts);

    if ($stale && in_array($status, ['pending', 'running'], true)) {
        return [
            'label' => '停止の可能性',
            'class' => 'task-stale',
            'detail' => $detail,
            'batch_running' => $running,
            'progress_percent' => $progressPercent,
            'progress_current' => $completedCount,
            'progress_total' => $totalCount,
        ];
    }
    if ($running && $status === 'running') {
        return [
            'label' => 'スクレイピング中',
            'class' => 'task-running',
            'detail' => $detail,
            'batch_running' => $running,
            'progress_percent' => $progressPercent,
            'progress_current' => $completedCount,
            'progress_total' => $totalCount,
        ];
    }
    if ($running && $status === 'pending') {
        return [
            'label' => '待機中',
            'class' => 'task-pending',
            'detail' => $detail,
            'batch_running' => $running,
            'progress_percent' => $progressPercent,
            'progress_current' => $completedCount,
            'progress_total' => $totalCount,
        ];
    }
    if ($status === 'done' || $status === 'ok') {
        return [
            'label' => '直近完了',
            'class' => 'task-done',
            'detail' => $detail,
            'batch_running' => $running,
            'progress_percent' => $progressPercent,
            'progress_current' => $completedCount,
            'progress_total' => $totalCount,
        ];
    }
    if ($status === 'failed') {
        $returncode = $item['returncode'] ?? null;
        if ($returncode !== null && $returncode !== '') {
            $detail = trim($detail . ' / rc=' . (string)$returncode, ' /');
        }
        return [
            'label' => '直近失敗',
            'class' => 'task-failed',
            'detail' => $detail,
            'batch_running' => $running,
            'progress_percent' => $progressPercent,
            'progress_current' => $completedCount,
            'progress_total' => $totalCount,
        ];
    }

    return null;
}
