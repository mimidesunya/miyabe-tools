<?php
require '/var/www/lib/session.php';
$config = load_config();

$clientId = $config['LINE_CHANNEL_ID'] ?? '';
$redirectUri = $config['REDIRECT_URI'] ?? '';

if (!$clientId || !$redirectUri) {
    http_response_code(500);
    echo 'LINE の設定が見つかりません。';
    exit;
}

// クエリパラメータまたは Referer ヘッダーからスラッグを抽出
$slug = $_GET['slug'] ?? '';
if (!$slug && isset($_SERVER['HTTP_REFERER'])) {
    // /boards/kawasaki/ のようなリファラー URL からスラッグの抽出を試みる
    if (preg_match('#/boards/([a-z0-9_-]+)/?#', $_SERVER['HTTP_REFERER'], $m)) {
        $slug = $m[1];
    }
}
// スラッグのバリデーション
if ($slug && preg_match('/^[a-z0-9_-]+$/', $slug)) {
    $municipalities = $config['MUNICIPALITIES'] ?? [];
    $allowed = array_keys($municipalities);
    if (in_array($slug, $allowed)) {
        $_SESSION['login_return_slug'] = $slug;
    }
}

$state = bin2hex(random_bytes(16));
$nonce = bin2hex(random_bytes(16));
$_SESSION['line_oauth_state'] = $state;
$_SESSION['line_oauth_nonce'] = $nonce;

$params = [
    'response_type' => 'code',
    'client_id' => $clientId,
    'redirect_uri' => $redirectUri,
    'state' => $state,
    'scope' => 'openid profile',
    'nonce' => $nonce,
];

$authUrl = 'https://access.line.me/oauth2/v2.1/authorize?' . http_build_query($params);
header('Location: ' . $authUrl);
exit;
?>