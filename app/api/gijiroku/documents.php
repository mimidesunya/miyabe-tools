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

$heldOn = gijiroku_api_request_string('held_on');
$payload = gijiroku_api_documents_payload(
    $municipality,
    $heldOn,
    gijiroku_api_request_int('page', 1, 1, 9999),
    gijiroku_api_request_int('per_page', 20, 1, 100)
);

if (($payload['status'] ?? '') === 'invalid_date') {
    gijiroku_api_respond_json(['error' => (string)($payload['error'] ?? 'held_on が不正です。')], 422);
}

gijiroku_api_respond_json($payload);
