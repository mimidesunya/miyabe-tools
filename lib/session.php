<?php
// セッションの初期化とヘルパー関数
if (session_status() !== PHP_SESSION_ACTIVE) {
    session_start();
}

/**
 * 設定ファイルを読み込む
 */
function load_config(): array {
    $configFile = __DIR__ . '/config.json';
    if (!file_exists($configFile)) {
        $example = __DIR__ . '/config.example.json';
        if (file_exists($example)) {
            return json_decode(file_get_contents($example), true);
        }
        return [];
    }
    return json_decode(file_get_contents($configFile), true);
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
 * リクエストから自治体スラッグを取得する
 */
function get_slug(?string $input = null): string {
    $config = load_config();
    $default = $config['DEFAULT_SLUG'] ?? '';
    $municipalities = $config['MUNICIPALITIES'] ?? [];
    $allowed = array_keys($municipalities);
    
    $s = $input ?? $_GET['slug'] ?? $default;
    if ($s === '' && !empty($allowed)) $s = $allowed[0];

    if (!preg_match('/^[a-z0-9_-]+$/', $s)) return '';
    if (!empty($allowed) && !in_array($s, $allowed)) return '';
    return $s;
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
    return open_pdo('/var/www/data/users.sqlite');
}

/**
 * 掲示場データベースをオープンする
 */
function open_boards_pdo(string $slug): PDO {
    return open_pdo("/var/www/data/boards/{$slug}/boards.sqlite");
}

/**
 * タスクデータベースをオープンし、ユーザーDBをアタッチする
 */
function open_tasks_pdo(string $slug): PDO {
    $pdo = open_pdo("/var/www/data/boards/{$slug}/tasks.sqlite");
    $usersPath = '/var/www/data/users.sqlite';
    if (file_exists($usersPath)) {
        $quoted = $pdo->quote($usersPath);
        $pdo->exec("ATTACH DATABASE {$quoted} AS users");
    }
    return $pdo;
}

/**
 * 掲示場データベースをオープンし、タスクDBとユーザーDBをアタッチする
 */
function open_boards_with_tasks_pdo(string $slug): PDO {
    $pdo = open_boards_pdo($slug);
    
    $tasksPath = "/var/www/data/boards/{$slug}/tasks.sqlite";
    if (file_exists($tasksPath)) {
        $quoted = $pdo->quote($tasksPath);
        $pdo->exec("ATTACH DATABASE {$quoted} AS tasks");
    }
    
    $usersPath = '/var/www/data/users.sqlite';
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
