<?php
require '/var/www/lib/session.php';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-cache, no-store, must-revalidate');
header('Pragma: no-cache');
header('Expires: 0');

$user = current_user();

// ログインしているユーザーをデータベースに自動追加（存在しない場合）
if ($user && isset($user['id'])) {
    try {
        $pdo = open_users_pdo();
        upsert_user($pdo, $user, false);
    } catch (Throwable $e) {
        // エラーは無視
    }
}

// スラッグごとの設定を確認
$slug = $_GET['slug'] ?? '';
$config = load_config();
$municipalities = $config['MUNICIPALITIES'] ?? [];
$allowOffset = false;

if (isset($municipalities[$slug])) {
    $allowOffset = $municipalities[$slug]['allow_offset'] ?? false;
}

echo json_encode([
    'loggedIn' => (bool)$user,
    'allowOffset' => $allowOffset,
    'user' => $user ? [
        'id' => $user['id'] ?? '',
        'name' => $user['name'] ?? '',
        'avatar' => $user['avatar'] ?? '',
    ] : null,
]);
