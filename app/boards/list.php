<?php
// 管理者用: タスクフィルター付き掲示場一覧
// ステータスごとにグループ化された掲示場と統計合計を表示します。
// ログインが必要です。

declare(strict_types=1);
require '/var/www/lib/session.php';

function h(?string $s): string { 
    return htmlspecialchars($s ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8'); 
}

// スラッグを取得し、DBを初期化（boards + attached tasks/users）
$slug = get_slug();
$boardsPdo = open_boards_with_tasks_pdo($slug);

// 初期値
$boards = [];
$message = $_GET['msg'] ?? null;
$me = current_user();
// ステータス絞り込み（不正な値は無視）
$selectedStatus = $_GET['status'] ?? '';
$allowedStatuses = ['pending', 'in_progress', 'done', 'issue'];
if (!in_array($selectedStatus, $allowedStatuses, true)) {
    $selectedStatus = '';
}
// 自分が更新したものだけ表示するか
$onlyMine = isset($_GET['mine']) && $_GET['mine'] === '1';

try {
    if ($boardsPdo) {
        // boards と tasks.task_status を LEFT JOIN して、ステータスと更新日時を取得
        // tasks.sqlite が無い場合や task_status に行が無い場合は NULL になる
        $whereParts = [];
        $params = [];

        if ($selectedStatus !== '') {
            $whereParts[] = 'ts.status = :status';
            $params[':status'] = $selectedStatus;
        }

        // "自分が手を付けたもの" = task_status.updated_by が現在ログイン中ユーザー
        if ($onlyMine && $me) {
            // users.sqlite は open_boards_pdo 内で users という名前で ATTACH 済み想定
            // LINE の user id から users.users.id を取得
            $lineId = (string)($me['id'] ?? '');
            if ($lineId !== '') {
                $uStmt = $boardsPdo->prepare('SELECT id FROM users.users WHERE line_user_id = :lid');
                $uStmt->execute([':lid' => $lineId]);
                $userRow = $uStmt->fetch();
                if ($userRow && isset($userRow['id'])) {
                    $whereParts[] = 'ts.updated_by = :uid';
                    $params[':uid'] = (int)$userRow['id'];
                }
            }
        }

        $whereSql = '';
        if (!empty($whereParts)) {
            $whereSql = 'WHERE ' . implode(' AND ', $whereParts);
        }

        $sql = "SELECT b.code,
                       b.address,
                       b.place,
                       ts.status,
                       ts.updated_at
                  FROM boards b
             LEFT JOIN tasks.task_status ts
                    ON ts.board_code = b.code
                  {$whereSql}
              ORDER BY b.code";

        $stmt = $boardsPdo->prepare($sql);
        $stmt->execute($params);
        $boards = $stmt->fetchAll();
    }
} catch (Throwable $e) {
    $message = 'データの取得に失敗しました: ' . h($e->getMessage());
    $boards = [];
}

$statusLabels = [
    'pending' => '未着手',
    'in_progress' => '⏳ 着手',
    'done' => '✅ 掲示',
    'issue' => '⚠️ 異常'
];
?><!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>掲示場一覧 - <?php echo h($slug); ?></title>
    <style>
        * { box-sizing: border-box; }
        body { 
            margin: 0; 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans JP', sans-serif;
            background: #f6f8fb;
            color: #222;
        }
        
        .header {
            background: #fff;
            padding: 12px 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        
        .header h1 { margin: 0; font-size: 20px; }
        .header-links a { 
            margin-left: 12px; 
            color: #275ea3; 
            text-decoration: none;
        }
        
        .container {
            max-width: 1200px;
            margin: 20px auto;
            padding: 0 20px;
        }
        
        .message {
            background: #d4edda;
            border: 1px solid #c3e6cb;
            color: #155724;
            padding: 10px 15px;
            border-radius: 6px;
            margin-bottom: 20px;
        }
        
        .controls {
            background: #fff;
            padding: 15px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
            margin-bottom: 20px;
        }
        
        .batch-controls {
            display: flex;
            gap: 10px;
            align-items: center;
            flex-wrap: wrap;
        }
        
        select {
            padding: 6px 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 14px;
        }
        
        .table-container {
            background: #fff;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
            overflow: auto;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
        }
        
        th, td {
            padding: 12px 15px;
            text-align: left;
            border-bottom: 1px solid #eee;
        }
        
        th {
            background: #f8f9fa;
            font-weight: 600;
            color: #495057;
            position: sticky;
            top: 0;
            z-index: 10;
        }
        
        tbody tr:hover {
            background: #f8f9fa;
        }
        
        .status {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 500;
        }
        
        .status-pending { background: #e9eefc; color: #4c6ef5; }
        .status-in_progress { background: #fff3cd; color: #856404; }
        .status-done { background: #d4edda; color: #155724; }
        .status-issue { background: #f8d7da; color: #721c24; }
        
        @media (max-width: 768px) {
            .container { padding: 0 10px; }
            table { font-size: 14px; }
            th, td { padding: 8px 10px; }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>掲示場一覧</h1>
        <div class="header-links">
            <span style="color: #666; margin-right: 15px;"><?php echo count($boards); ?>件表示中</span>
            <a href="/boards/<?php echo h($slug); ?>/">マップ表示</a>
            <?php if ($me): ?>
                <span>ようこそ、<?php echo h($me['name'] ?? ''); ?>さん</span>
                <?php if (is_admin($me)): ?>
                    <a href="/boards/users.php?slug=<?php echo h($slug); ?>">ユーザー一覧</a>
                <?php endif; ?>
                <a href="/line/logout.php">ログアウト</a>
            <?php else: ?>
                <a href="/line/login.php">LINEでログイン</a>
            <?php endif; ?>
        </div>
    </div>
    
    <div class="container">
        <?php if ($message): ?>
            <div class="message"><?php echo h($message); ?></div>
        <?php endif; ?>
        
        <div class="controls">
            <form method="get">
                <input type="hidden" name="slug" value="<?php echo h($slug); ?>">
                <div class="batch-controls">
                    <label for="status">ステータスで絞り込み:</label>
                    <select name="status" id="status" onchange="this.form.submit()">
                        <option value="">すべて表示</option>
                        <option value="pending" <?php echo $selectedStatus === 'pending' ? 'selected' : ''; ?>>未着手</option>
                        <option value="in_progress" <?php echo $selectedStatus === 'in_progress' ? 'selected' : ''; ?>>⏳ 着手</option>
                        <option value="done" <?php echo $selectedStatus === 'done' ? 'selected' : ''; ?>>✅ 掲示</option>
                        <option value="issue" <?php echo $selectedStatus === 'issue' ? 'selected' : ''; ?>>⚠️ 異常</option>
                    </select>

                    <?php if ($me): ?>
                        <label style="margin-left: 15px;">
                            <input type="checkbox" name="mine" value="1" onchange="this.form.submit()" <?php echo $onlyMine ? 'checked' : ''; ?>>
                            自分が手を付けたものだけ
                        </label>
                    <?php endif; ?>
                </div>
            </form>
        </div>
        
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th>コード</th>
                        <th>住所</th>
                        <th>設置場所</th>
                        <th>ステータス</th>
                        <th>更新日時</th>
                    </tr>
                </thead>
                <tbody>
                    <?php if (empty($boards)): ?>
                        <tr>
                            <td colspan="5" style="text-align: center; padding: 40px 20px; color: #999;">
                                データがありません
                            </td>
                        </tr>
                    <?php else: ?>
                        <?php foreach ($boards as $board): ?>
                            <tr>
                                <td><?php echo h($board['code']); ?></td>
                                <td><?php echo h($board['address']); ?></td>
                                <td><?php echo h($board['place']); ?></td>
                                <td>
                                    <?php if (!empty($board['status'])): ?>
                                        <span class="status status-<?php echo h($board['status']); ?>">
                                            <?php echo h($statusLabels[$board['status']] ?? $board['status']); ?>
                                        </span>
                                    <?php else: ?>
                                        -
                                    <?php endif; ?>
                                </td>
                                <td><?php echo h($board['updated_at'] ?? '-'); ?></td>
                            </tr>
                        <?php endforeach; ?>
                    <?php endif; ?>
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>