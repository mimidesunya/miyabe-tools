<?php
require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'php' . DIRECTORY_SEPARATOR . 'runtime.php';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-cache, no-store, must-revalidate');
header('Pragma: no-cache');
header('Expires: 0');

$user = poster_boards_current_user();

// ログインしているユーザーをデータベースに自動追加（存在しない場合）
if ($user && isset($user['id'])) {
    try {
        $pdo = poster_boards_open_users_pdo();
        poster_boards_upsert_user($pdo, $user, false);
    } catch (Throwable $e) {
        // エラーは無視
    }
}

// スラッグごとの設定を確認
$slug = get_slug($_GET['slug'] ?? null);
if ($slug === '') {
    $slug = get_default_slug();
}
$allowOffset = poster_boards_is_admin($user);

echo json_encode([
    'loggedIn' => (bool)$user,
    'allowOffset' => $allowOffset,
    'user' => $user ? [
        'id' => $user['id'] ?? '',
        'name' => $user['name'] ?? '',
        'avatar' => $user['avatar'] ?? '',
    ] : null,
]);
