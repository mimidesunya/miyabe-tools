<?php
declare(strict_types=1);

// 宮部たつひこマップ API。
// GET  : 公開用の現在地。位置提供 OFF のときは座標を返さない。
// POST : 管理用（要ログイン + CSRF）。位置提供の ON/OFF と現在地の更新。

require_once __DIR__ . DIRECTORY_SEPARATOR . 'lib.php';

header('Content-Type: application/json; charset=UTF-8');

function tmap_api_respond(int $status, array $payload): void
{
    http_response_code($status);
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . "\n";
    exit;
}

$method = (string)($_SERVER['REQUEST_METHOD'] ?? 'GET');

if ($method === 'GET') {
    header('Cache-Control: no-store');
    $state = tmap_load_state();
    if (isset($_GET['admin']) && tmap_is_admin()) {
        // 管理画面は OFF 中でも最後の位置と状態を確認できる。
        tmap_api_respond(200, ['state' => $state]);
    }
    tmap_api_respond(200, tmap_public_payload($state));
}

if ($method !== 'POST') {
    tmap_api_respond(405, ['error' => 'GET / POST のみ対応しています']);
}

if (!tmap_is_admin()) {
    tmap_api_respond(401, ['error' => 'ログインが必要です']);
}

$raw = (string)file_get_contents('php://input');
$body = json_decode($raw, true);
if (!is_array($body)) {
    $body = $_POST;
}

$csrf = $_SERVER['HTTP_X_TMAP_CSRF'] ?? ($body['csrf'] ?? null);
if (!tmap_verify_csrf(is_string($csrf) ? $csrf : null)) {
    tmap_api_respond(403, ['error' => 'CSRF トークンが一致しません']);
}

$action = is_string($body['action'] ?? null) ? (string)$body['action'] : '';
$state = tmap_load_state();

if ($action === 'set_sharing') {
    $state['sharing'] = filter_var($body['sharing'] ?? null, FILTER_VALIDATE_BOOLEAN);
    tmap_save_state($state);
    tmap_api_respond(200, ['ok' => true, 'state' => $state]);
}

if ($action === 'update_location') {
    $lat = filter_var($body['lat'] ?? null, FILTER_VALIDATE_FLOAT);
    $lng = filter_var($body['lng'] ?? null, FILTER_VALIDATE_FLOAT);
    $accuracy = filter_var($body['accuracy'] ?? null, FILTER_VALIDATE_FLOAT);
    if ($lat === false || $lng === false || $lat < -90 || $lat > 90 || $lng < -180 || $lng > 180) {
        tmap_api_respond(400, ['error' => '座標が不正です']);
    }
    $state['lat'] = (float)$lat;
    $state['lng'] = (float)$lng;
    $state['accuracy'] = ($accuracy !== false && $accuracy >= 0 && $accuracy <= 100000)
        ? round((float)$accuracy, 1) : null;
    $state['updated_at'] = gmdate('c');
    tmap_save_state($state);
    tmap_api_respond(200, ['ok' => true, 'state' => $state]);
}

if ($action === 'clear_location') {
    $state['lat'] = null;
    $state['lng'] = null;
    $state['accuracy'] = null;
    $state['updated_at'] = null;
    tmap_save_state($state);
    tmap_api_respond(200, ['ok' => true, 'state' => $state]);
}

tmap_api_respond(400, ['error' => '不明な action です']);
