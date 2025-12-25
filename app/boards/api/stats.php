<?php
// 掲示板ステータスのグローバルカウントエンドポイント: 合計とログインユーザーのカウントを返す
// レスポンス JSON:
// { totals: { in_progress, done, issue }, mine: { in_progress, done, issue }, loggedIn: bool }

declare(strict_types=1);

require '/var/www/lib/session.php';

header('Content-Type: application/json; charset=UTF-8');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
header('Pragma: no-cache');

$slug = get_slug();
$tasksPdo = open_tasks_pdo($slug);

// 合計を取得
try {
    $stmt = $tasksPdo->query("SELECT status, COUNT(*) AS cnt FROM task_status GROUP BY status");
    $rows = $stmt->fetchAll();
} catch (Throwable $e) {
    http_response_code(500);
    echo json_encode(['error' => 'task_status のクエリに失敗しました'], JSON_UNESCAPED_UNICODE);
    exit;
}

$totals = ['in_progress' => 0, 'done' => 0, 'issue' => 0];
foreach ($rows as $r) {
    $st = (string)$r['status'];
    $c = (int)$r['cnt'];
    if ($st === 'in_progress') $totals['in_progress'] = $c;
    elseif ($st === 'done') $totals['done'] = $c;
    elseif ($st === 'issue') $totals['issue'] = $c;
}

// 自分のカウントを取得
$mine = ['in_progress' => 0, 'done' => 0, 'issue' => 0];
$me = current_user();
$loggedIn = ($me !== null);
if ($me) {
    $lineId = (string)($me['id'] ?? '');
    if ($lineId !== '') {
        try {
            $stmt = $tasksPdo->prepare("
                SELECT ts.status, COUNT(*) AS cnt
                FROM task_status ts
                JOIN users.users u ON u.id = ts.updated_by
                WHERE u.line_user_id = :lid
                GROUP BY ts.status
            ");
            $stmt->execute([':lid' => $lineId]);
            $mineRows = $stmt->fetchAll();
            foreach ($mineRows as $r) {
                $st = (string)$r['status'];
                $c = (int)$r['cnt'];
                if ($st === 'in_progress') $mine['in_progress'] = $c;
                elseif ($st === 'done') $mine['done'] = $c;
                elseif ($st === 'issue') $mine['issue'] = $c;
            }
        } catch (Throwable $e) {
            // エラーは無視して0を返す
        }
    }
}

echo json_encode([
    'loggedIn' => $loggedIn,
    'totals' => $totals,
    'mine' => $mine,
], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
?>
