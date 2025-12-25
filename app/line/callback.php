<?php
require '/var/www/lib/session.php';

$config = load_config();

$clientId = $config['LINE_CHANNEL_ID'] ?? '';
$clientSecret = $config['LINE_CHANNEL_SECRET'] ?? '';
$redirectUri = $config['REDIRECT_URI'] ?? '';

if (!$clientId || !$clientSecret || !$redirectUri) {
    http_response_code(500);
    echo 'LINE の設定が見つかりません。';
    exit;
}

// state の検証
if (!isset($_GET['state'], $_GET['code']) || ($_GET['state'] !== ($_SESSION['line_oauth_state'] ?? ''))) {
    http_response_code(400);
    echo '無効な state です。';
    exit;
}
$code = $_GET['code'];
unset($_SESSION['line_oauth_state']);

// コードをトークンに交換
$ch = curl_init('https://api.line.me/oauth2/v2.1/token');
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_POST => true,
    CURLOPT_POSTFIELDS => http_build_query([
        'grant_type' => 'authorization_code',
        'code' => $code,
        'redirect_uri' => $redirectUri,
        'client_id' => $clientId,
        'client_secret' => $clientSecret,
    ]),
]);
$resp = curl_exec($ch);
if ($resp === false) {
    http_response_code(502);
    echo 'トークンリクエストに失敗しました。';
    exit;
}
$token = json_decode($resp, true);
if (!is_array($token) || empty($token['id_token'])) {
    http_response_code(400);
    echo '無効なトークンレスポンスです。';
    exit;
}

$idToken = $token['id_token'];
// ID トークンの検証
$ch = curl_init('https://api.line.me/oauth2/v2.1/verify');
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_POST => true,
    CURLOPT_POSTFIELDS => http_build_query([
        'id_token' => $idToken,
        'client_id' => $clientId,
        'nonce' => $_SESSION['line_oauth_nonce'] ?? '',
    ]),
]);
$verifyResp = curl_exec($ch);
$verify = json_decode($verifyResp, true);
unset($_SESSION['line_oauth_nonce']);

if (!is_array($verify) || empty($verify['sub'])) {
    http_response_code(400);
    echo 'ID トークンの検証に失敗しました。';
    exit;
}

$userId = $verify['sub'];
$displayName = $verify['name'] ?? '';
$picture = $verify['picture'] ?? '';

// プロフィールの取得（オプション）
$accessToken = $token['access_token'] ?? '';
if ($accessToken) {
    $ch = curl_init('https://api.line.me/v2/profile');
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HTTPHEADER => [
            'Authorization: Bearer ' . $accessToken,
        ],
    ]);
    $profileResp = curl_exec($ch);
    $profile = json_decode($profileResp, true);
    if (is_array($profile) && !empty($profile['userId'])) {
        $userId = $profile['userId'];
        $displayName = $profile['displayName'] ?? $displayName;
        $picture = $profile['pictureUrl'] ?? $picture;
    }
}

// ログイン時にユーザーをデータベースに自動追加
try {
    $pdo = open_users_pdo();
    upsert_user($pdo, [
        'id' => $userId,
        'name' => $displayName,
        'avatar' => $picture
    ], false);
} catch (Throwable $e) {
    error_log('ユーザーの登録/更新に失敗しました: ' . $e->getMessage());
}

// セッションにユーザーを保存（session.php の current_user() の期待値に合わせる）
$_SESSION['user'] = [
    'provider' => 'line',
    'id' => $userId,
    'name' => $displayName,
    'avatar' => $picture,
];

// セッションから戻り先のスラッグを取得
$defaultSlug = $config['DEFAULT_SLUG'] ?? '';
$municipalities = $config['MUNICIPALITIES'] ?? [];
$allowed = array_keys($municipalities);
$returnSlug = $_SESSION['login_return_slug'] ?? $defaultSlug;
if ($returnSlug === '' && !empty($allowed)) $returnSlug = $allowed[0];
unset($_SESSION['login_return_slug']);

// リダイレクト前にスラッグを検証
if (!preg_match('/^[a-z0-9_-]+$/', $returnSlug) || (!empty($allowed) && !in_array($returnSlug, $allowed))) {
    $returnSlug = !empty($allowed) ? $allowed[0] : '';
}

if ($returnSlug === '') {
    die('リダイレクトに失敗しました: 有効なスラッグが見つかりません。');
}

header('Location: /boards/' . $returnSlug . '/');
exit;
?>