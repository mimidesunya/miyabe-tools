<?php
declare(strict_types=1);

$posterBoardsSharedLib = dirname(__DIR__, 3) . DIRECTORY_SEPARATOR . 'lib';
require_once $posterBoardsSharedLib . DIRECTORY_SEPARATOR . 'municipalities.php';
unset($posterBoardsSharedLib);
require_once __DIR__ . DIRECTORY_SEPARATOR . 'municipality_feature.php';

if (session_status() !== PHP_SESSION_ACTIVE) {
    session_start();
}

function poster_boards_current_user(): ?array
{
    return $_SESSION['user'] ?? null;
}

function poster_boards_require_login(): void
{
    if (!poster_boards_current_user()) {
        header('Location: /line/login.php');
        exit;
    }
}

function poster_boards_is_admin(?array $user = null): bool
{
    $user ??= poster_boards_current_user();
    if (!$user || !isset($user['id'])) {
        return false;
    }

    $config = load_config();
    $admins = $config['ADMIN_LINE_IDS'] ?? [];
    return is_array($admins) && in_array((string)$user['id'], $admins, true);
}

function poster_boards_open_pdo(string $path): PDO
{
    if (!is_file($path)) {
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
    } catch (Throwable $exception) {
        error_log('Election poster boards database open failed: ' . $exception->getMessage());
        header('Content-Type: application/json; charset=UTF-8');
        http_response_code(500);
        echo json_encode(['error' => 'データベースのオープンに失敗しました: ' . basename($path)], JSON_UNESCAPED_UNICODE);
        exit;
    }
}

function poster_boards_users_db_path(): string
{
    $domainPath = data_path('boards/users.sqlite');
    if (is_file($domainPath)) {
        return $domainPath;
    }

    // Compatibility for deployments created before the domain split.
    return data_path('users.sqlite');
}

function poster_boards_open_users_pdo(): PDO
{
    return poster_boards_open_pdo(poster_boards_users_db_path());
}

function poster_boards_open_boards_pdo(string $slug): PDO
{
    $slug = get_slug($slug);
    $feature = poster_boards_municipality_feature($slug) ?? [];
    $path = trim((string)($feature['db_path'] ?? ''));
    if ($path === '') {
        $path = data_path("boards/{$slug}/boards.sqlite");
    }
    return poster_boards_open_pdo($path);
}

function poster_boards_open_tasks_pdo(string $slug): PDO
{
    $slug = get_slug($slug);
    $feature = poster_boards_municipality_feature($slug) ?? [];
    $tasksPath = trim((string)($feature['tasks_db_path'] ?? ''));
    if ($tasksPath === '') {
        $tasksPath = data_path("boards/{$slug}/tasks.sqlite");
    }
    $pdo = poster_boards_open_pdo($tasksPath);
    $usersPath = poster_boards_users_db_path();
    if (is_file($usersPath)) {
        $quoted = $pdo->quote($usersPath);
        $pdo->exec("ATTACH DATABASE {$quoted} AS users");
    }
    return $pdo;
}

function poster_boards_open_boards_with_tasks_pdo(string $slug): PDO
{
    $slug = get_slug($slug);
    $pdo = poster_boards_open_boards_pdo($slug);

    $feature = poster_boards_municipality_feature($slug) ?? [];
    $tasksPath = trim((string)($feature['tasks_db_path'] ?? ''));
    if ($tasksPath === '') {
        $tasksPath = data_path("boards/{$slug}/tasks.sqlite");
    }
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

    $usersPath = poster_boards_users_db_path();
    if (is_file($usersPath)) {
        $quoted = $pdo->quote($usersPath);
        $pdo->exec("ATTACH DATABASE {$quoted} AS users");
    }

    return $pdo;
}

function poster_boards_upsert_user(PDO $pdo, array $sessionUser, bool $isAttached = true): int
{
    $lineId = (string)($sessionUser['id'] ?? '');
    if ($lineId === '') {
        throw new RuntimeException('無効なセッションユーザーです');
    }
    $name = (string)($sessionUser['name'] ?? '');
    $avatar = (string)($sessionUser['avatar'] ?? '');
    $table = $isAttached ? 'users.users' : 'users';

    $pdo->beginTransaction();
    try {
        $stmt = $pdo->prepare("UPDATE {$table} SET name = COALESCE(:name, name), avatar = COALESCE(:avatar, avatar), updated_at = CURRENT_TIMESTAMP WHERE line_user_id = :lid");
        $stmt->execute([':name' => $name !== '' ? $name : null, ':avatar' => $avatar !== '' ? $avatar : null, ':lid' => $lineId]);

        $stmt = $pdo->prepare("INSERT OR IGNORE INTO {$table}(line_user_id, name, avatar) VALUES(:lid, :name, :avatar)");
        $stmt->execute([':lid' => $lineId, ':name' => $name, ':avatar' => $avatar]);

        $stmt = $pdo->prepare("SELECT id FROM {$table} WHERE line_user_id = :lid");
        $stmt->execute([':lid' => $lineId]);
        $row = $stmt->fetch();
        $pdo->commit();
        if (!$row) {
            throw new RuntimeException('ユーザーの登録/更新に失敗しました');
        }
        return (int)$row['id'];
    } catch (Throwable $exception) {
        if ($pdo->inTransaction()) {
            $pdo->rollBack();
        }
        throw $exception;
    }
}
