<?php
declare(strict_types=1);

require_once dirname(__DIR__, 3) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'gijiroku_api.php';

$slug = gijiroku_api_requested_slug();
if ($slug === '') {
    gijiroku_api_respond_json(['error' => 'slug を指定してください。'], 422);
}

$municipality = gijiroku_api_ready_municipality($slug);
if (!is_array($municipality)) {
    gijiroku_api_respond_json(['error' => '検索対象の自治体が見つかりません。'], 404);
}

$id = gijiroku_api_requested_document_id();
$payload = gijiroku_api_document_payload(
    $municipality,
    $id,
    gijiroku_api_request_string('q')
);

$status = (string)($payload['status'] ?? '');
if ($status === 'invalid_id') {
    gijiroku_api_respond_json(['error' => (string)($payload['error'] ?? 'id が不正です。')], 422);
}
if ($status === 'not_found') {
    gijiroku_api_respond_json(['error' => (string)($payload['error'] ?? '会議録が見つかりません。')], 404);
}

gijiroku_api_respond_json($payload);
