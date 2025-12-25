<?php
/**
 * 掲示場タスクのステータス・コメント管理API
 * 
 * メソッド:
 *   GET /boards/api/tasks.php?slug=...&code=BOARD_CODE
 *        -> { board_code, status, last_comment, updated_at, updated_by, updated_by_name, history: [...] }
 *   POST /boards/api/tasks.php  JSON: { action: 'set_status', slug, board_code, status, note? }
 *   POST /boards/api/tasks.php  JSON: { action: 'comment', slug, board_code, note }
 * 
 * POSTはログイン必須、GETは公開（読み取り専用）
 */

declare(strict_types=1);

require '/var/www/lib/session.php';
header('Content-Type: application/json; charset=UTF-8');

// 掲示場の座標を設定する
function set_board_coords(PDO $boardsPdo, string $code, float $lat, float $lon): array {
    // 基本的なバリデーション
    if (!is_finite($lat) || !is_finite($lon) || $lat < -90 || $lat > 90 || $lon < -180 || $lon > 180) {
        http_response_code(400);
        return ['error' => '不正な座標です'];
    }
    try {
        $stmt = $boardsPdo->prepare('UPDATE boards SET lat = :lat, lon = :lon WHERE code = :code');
        $stmt->execute([':lat' => $lat, ':lon' => $lon, ':code' => $code]);
        if ($stmt->rowCount() === 0) {
            http_response_code(404);
            return ['error' => '掲示場が見つかりません'];
        }
    } catch (Throwable $e) {
        http_response_code(500);
        return ['error' => '座標の設定に失敗しました'];
    }
    // boardsテーブルのトリガーによってrtreeも更新される
    return ['ok' => true, 'board_code' => $code, 'lat' => $lat, 'lon' => $lon];
}

function json_input(): array {
    $ctype = $_SERVER['CONTENT_TYPE'] ?? '';
    if (stripos($ctype, 'application/json') !== false) {
        $raw = file_get_contents('php://input');
        $data = json_decode($raw, true);
        return is_array($data) ? $data : [];
    }
    // フォームエンコードへのフォールバック
    return $_POST;
}

function get_status(PDO $pdo, string $code): array {
    $stmt = $pdo->prepare('SELECT ts.board_code, ts.status, ts.last_comment, ts.updated_at, ts.updated_by, u.name AS updated_by_name, u.line_user_id AS updated_by_line_id
                           FROM task_status ts
                           LEFT JOIN users.users u ON u.id = ts.updated_by
                           WHERE ts.board_code = :code');
    $stmt->execute([':code' => $code]);
    $status = $stmt->fetch() ?: [
        'board_code' => $code,
        'status' => 'pending',
        'last_comment' => null,
        'updated_at' => null,
        'updated_by' => null,
    'updated_by_name' => null,
    'updated_by_line_id' => null,
    ];

    $h = $pdo->prepare('SELECT sh.id, sh.board_code, sh.user_id, u.name AS user_name, sh.old_status, sh.new_status, sh.note, sh.created_at
                        FROM status_history sh
                        LEFT JOIN users.users u ON u.id = sh.user_id
                        WHERE sh.board_code = :code
                        ORDER BY sh.created_at DESC, sh.id DESC
                        LIMIT 50');
    $h->execute([':code' => $code]);
    $history = $h->fetchAll();
    return ['status' => $status, 'history' => $history];
}

function set_status(PDO $pdo, int $userId, string $code, string $status, ?string $note): array {
    $valid = ['pending','in_progress','done','issue'];
    if (!in_array($status, $valid, true)) {
        http_response_code(400);
        return ['error' => '不正なステータスです'];
    }
    $pdo->beginTransaction();
    try {
        // task_status を更新または挿入
        $stmt = $pdo->prepare('INSERT INTO task_status(board_code, status, updated_by, last_comment)
                               VALUES(:code, :status, :uid, :note)
                               ON CONFLICT(board_code) DO UPDATE SET
                                 status = excluded.status,
                                 updated_by = excluded.updated_by,
                                 last_comment = excluded.last_comment,
                                 updated_at = CURRENT_TIMESTAMP');
        $stmt->execute([':code' => $code, ':status' => $status, ':uid' => $userId, ':note' => $note]);
        $pdo->commit();
    } catch (Throwable $e) {
        if ($pdo->inTransaction()) $pdo->rollBack();
        http_response_code(500);
        return ['error' => 'ステータスの設定に失敗しました'];
    }
    return get_status($pdo, $code);
}

function add_comment(PDO $pdo, int $userId, string $code, string $note): array {
    $pdo->beginTransaction();
    try {
        // 現在のステータスを取得
        $stmt = $pdo->prepare('SELECT status FROM task_status WHERE board_code = :code');
        $stmt->execute([':code' => $code]);
        $row = $stmt->fetch();
        $cur = $row ? (string)$row['status'] : 'pending';

        // 履歴レコードを挿入（コメントのみ）
        $ins = $pdo->prepare('INSERT INTO status_history(board_code, user_id, old_status, new_status, note) VALUES(:code, :uid, :old, :new, :note)');
        $ins->execute([':code' => $code, ':uid' => $userId, ':old' => $cur, ':new' => $cur, ':note' => $note]);

        // last_comment を更新し、updated_at/by を更新
        $up = $pdo->prepare('INSERT INTO task_status(board_code, status, updated_by, last_comment)
                             VALUES(:code, :status, :uid, :note)
                             ON CONFLICT(board_code) DO UPDATE SET
                               last_comment = excluded.last_comment,
                               updated_by = excluded.updated_by,
                               updated_at = CURRENT_TIMESTAMP');
        $up->execute([':code' => $code, ':status' => $cur, ':uid' => $userId, ':note' => $note]);

        $pdo->commit();
    } catch (Throwable $e) {
        if ($pdo->inTransaction()) $pdo->rollBack();
        http_response_code(500);
        return ['error' => 'コメントの追加に失敗しました'];
    }
    return get_status($pdo, $code);
}

// ルーター
$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';

if ($method === 'GET') {
    $slug = get_slug();
    $code = isset($_GET['code']) ? trim((string)$_GET['code']) : '';
    if ($code === '') {
        http_response_code(400);
        echo json_encode(['error' => 'code が必要です'], JSON_UNESCAPED_UNICODE);
        exit;
    }
    $pdo = open_tasks_pdo($slug);
    echo json_encode(get_status($pdo, $code), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

// POSTはログイン必須
if ($method === 'POST') {
    $user = current_user();
    if (!$user) { require_login(); /* リダイレクトされる */ }

    $data = json_input();
    $slug = get_slug($data['slug'] ?? null);
    $action = isset($data['action']) ? (string)$data['action'] : '';
    $code = isset($data['board_code']) ? trim((string)$data['board_code']) : '';
    if ($code === '') {
        http_response_code(400);
        echo json_encode(['error' => 'board_code が必要です'], JSON_UNESCAPED_UNICODE);
        exit;
    }
    $pdo = open_tasks_pdo($slug);
    try {
        $uid = upsert_user($pdo, $user);
    } catch (Throwable $e) {
        http_response_code(500);
        echo json_encode(['error' => 'ユーザーの登録/更新に失敗しました'], JSON_UNESCAPED_UNICODE);
        exit;
    }

    if ($action === 'set_status') {
        $status = isset($data['status']) ? (string)$data['status'] : '';
        $note = isset($data['note']) ? trim((string)$data['note']) : null;
        echo json_encode(set_status($pdo, $uid, $code, $status, $note), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        exit;
    } elseif ($action === 'comment') {
        // 空のノートを許可してコメントをクリアできるようにする
        $note = isset($data['note']) ? trim((string)$data['note']) : '';
        echo json_encode(add_comment($pdo, (int)$uid, $code, $note), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        exit;
    } elseif ($action === 'move' || $action === 'set_coords') {
        // 権限チェック
        $config = load_config();
        $municipalities = $config['MUNICIPALITIES'] ?? [];
        $allowOffset = $municipalities[$slug]['allow_offset'] ?? false;
        if (!$allowOffset) {
            http_response_code(403);
            echo json_encode(['error' => 'この自治体では位置調整が許可されていません'], JSON_UNESCAPED_UNICODE);
            exit;
        }

        $lat = isset($data['lat']) && is_numeric($data['lat']) ? (float)$data['lat'] : null;
        $lon = isset($data['lon']) && is_numeric($data['lon']) ? (float)$data['lon'] : null;
        if ($lat === null || $lon === null) {
            http_response_code(400);
            echo json_encode(['error' => 'lat/lon が必要です'], JSON_UNESCAPED_UNICODE);
            exit;
        }
        $boardsPdo = open_boards_pdo($slug);
        echo json_encode(set_board_coords($boardsPdo, $code, $lat, $lon), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        exit;
    }

    http_response_code(400);
    echo json_encode(['error' => '無効なアクションです'], JSON_UNESCAPED_UNICODE);
    exit;
}

http_response_code(405);
echo json_encode(['error' => 'メソッドが許可されていません'], JSON_UNESCAPED_UNICODE);
exit;
?>
