<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'news.php';

// トップページの「各地のお知らせ」を都道府県で切り替えるときに読む。
// 並べた HTML を返し、JS は差し替えるだけにする（並べ方を PHP の 1 か所にまとめる）。

header('Content-Type: application/json; charset=UTF-8');
header('X-Content-Type-Options: nosniff');

$scope = news_latest_scope($_GET['pref'] ?? 'all');
if ($scope === null) {
    http_response_code(400);
    echo json_encode(['error' => 'pref は 01〜47 か all です。'], JSON_UNESCAPED_UNICODE);
    exit;
}
$payload = news_latest_load($scope);
if ($payload === null) {
    http_response_code(404);
    header('Cache-Control: no-store');
    echo json_encode(['error' => 'お知らせの一覧がまだありません。'], JSON_UNESCAPED_UNICODE);
    exit;
}
// 一覧は 3 時間おきに変わる。
header('Cache-Control: public, max-age=300');
echo json_encode([
    'scope' => $scope,
    'generated_at' => (string)($payload['generated_at'] ?? ''),
    'updated' => news_render_updated($payload),
    'html' => news_render_items($payload),
], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
