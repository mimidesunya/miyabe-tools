<?php
require_once __DIR__ . '/municipalities.php';

// セッションの初期化とヘルパー関数
if (session_status() !== PHP_SESSION_ACTIVE) {
    session_start();
}

/**
 * 現在ログイン中のユーザー情報を取得する
 */
function current_user(): ?array {
    return $_SESSION['user'] ?? null;
}

/**
 * ログインを必須にする（未ログインならログイン画面へリダイレクト）
 */
function require_login(): void {
    if (!current_user()) {
        header('Location: /line/login.php');
        exit;
    }
}

/**
 * 管理者かどうかを判定する
 */
function is_admin(?array $user = null): bool {
    if (!$user) $user = current_user();
    if (!$user || !isset($user['id'])) return false;
    
    $config = load_config();
    $admins = $config['ADMIN_LINE_IDS'] ?? [];
    return in_array((string)$user['id'], $admins, true);
}

/**
 * データベース接続をオープンする
 */
function open_pdo(string $path): PDO {
    if (!file_exists($path)) {
        header('Content-Type: application/json; charset=UTF-8');
        http_response_code(500);
        echo json_encode(['error' => basename($path) . ' が見つかりません'], JSON_UNESCAPED_UNICODE);
        exit;
    }
    try {
        $pdo = new PDO('sqlite:' . $path, null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_PERSISTENT => false,
        ]);
        $pdo->exec('PRAGMA foreign_keys = ON;');
        return $pdo;
    } catch (Throwable $e) {
        header('Content-Type: application/json; charset=UTF-8');
        http_response_code(500);
        echo json_encode(['error' => 'データベースのオープンに失敗しました: ' . basename($path)], JSON_UNESCAPED_UNICODE);
        exit;
    }
}

/**
 * ユーザーデータベースをオープンする
 */
function open_users_pdo(): PDO {
    return open_pdo(data_path('users.sqlite'));
}

/**
 * 掲示場データベースをオープンする
 */
function open_boards_pdo(string $slug): PDO {
    // 保存先の slug はデータ側で正規化済みなので、そのまま公開カタログで検証する。
    $slug = get_slug($slug);
    return open_pdo(data_path("boards/{$slug}/boards.sqlite"));
}

/**
 * タスクデータベースをオープンし、ユーザーDBをアタッチする
 */
function open_tasks_pdo(string $slug): PDO {
    $slug = get_slug($slug);
    $pdo = open_pdo(data_path("boards/{$slug}/tasks.sqlite"));
    $usersPath = data_path('users.sqlite');
    if (file_exists($usersPath)) {
        $quoted = $pdo->quote($usersPath);
        $pdo->exec("ATTACH DATABASE {$quoted} AS users");
    }
    return $pdo;
}

/**
 * 掲示場データベースをオープンし、タスクDBとユーザーDBをアタッチする
 * tasks.sqlite が存在しない場合は自動的に作成する
 */
function open_boards_with_tasks_pdo(string $slug): PDO {
    $slug = get_slug($slug);
    $pdo = open_boards_pdo($slug);

    $tasksPath = data_path("boards/{$slug}/tasks.sqlite");
    $quoted = $pdo->quote($tasksPath);
    $pdo->exec("ATTACH DATABASE {$quoted} AS tasks");
    $pdo->exec("CREATE TABLE IF NOT EXISTS tasks.task_status (
        board_code TEXT PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'pending',
        updated_by INTEGER NOT NULL,
        last_comment TEXT,
        updated_at TEXT DEFAULT (datetime('now')),
        CHECK (status IN ('pending', 'in_progress', 'done', 'issue'))
    )");
    $pdo->exec("CREATE TABLE IF NOT EXISTS tasks.status_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        board_code TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        old_status TEXT,
        new_status TEXT,
        note TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )");
    $pdo->exec("CREATE TRIGGER IF NOT EXISTS tasks.trg_task_status_hist
        AFTER UPDATE ON task_status
        WHEN OLD.status IS NOT NEW.status
        BEGIN
            INSERT INTO status_history (board_code, user_id, old_status, new_status, note)
            VALUES (NEW.board_code, NEW.updated_by, OLD.status, NEW.status, NEW.last_comment);
        END");

    $usersPath = data_path('users.sqlite');
    if (file_exists($usersPath)) {
        $quoted = $pdo->quote($usersPath);
        $pdo->exec("ATTACH DATABASE {$quoted} AS users");
    }

    return $pdo;
}

/**
 * ユーザー情報を更新または挿入する
 */
function upsert_user(PDO $pdo, array $sessUser, bool $isAttached = true): int {
    $lineId = (string)($sessUser['id'] ?? '');
    if ($lineId === '') {
        throw new RuntimeException('無効なセッションユーザーです');
    }
    $name = (string)($sessUser['name'] ?? '');
    $avatar = (string)($sessUser['avatar'] ?? '');
    $table = $isAttached ? 'users.users' : 'users';

    $pdo->beginTransaction();
    try {
        // まず更新を試みる
        $stmt = $pdo->prepare("UPDATE {$table} SET name = COALESCE(:name, name), avatar = COALESCE(:avatar, avatar), updated_at = CURRENT_TIMESTAMP WHERE line_user_id = :lid");
        $stmt->execute([':name' => $name !== '' ? $name : null, ':avatar' => $avatar !== '' ? $avatar : null, ':lid' => $lineId]);

        // 存在しなければ挿入
        $stmt = $pdo->prepare("INSERT OR IGNORE INTO {$table}(line_user_id, name, avatar) VALUES(:lid, :name, :avatar)");
        $stmt->execute([':lid' => $lineId, ':name' => $name, ':avatar' => $avatar]);

        // IDを取得
        $stmt = $pdo->prepare("SELECT id FROM {$table} WHERE line_user_id = :lid");
        $stmt->execute([':lid' => $lineId]);
        $row = $stmt->fetch();
        $pdo->commit();
        if (!$row) throw new RuntimeException('ユーザーの登録/更新に失敗しました');
        return (int)$row['id'];
    } catch (Throwable $e) {
        if ($pdo->inTransaction()) $pdo->rollBack();
        throw $e;
    }
}
