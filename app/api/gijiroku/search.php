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

$query = gijiroku_api_request_string('q');
$validationError = gijiroku_search_validate_query($query, [$slug => $municipality]);
if ($validationError !== null) {
    gijiroku_api_respond_json(['error' => $validationError], 422);
}

$payload = gijiroku_api_search_payload(
    $municipality,
    $query,
    gijiroku_api_request_int('page', 1, 1, 9999),
    gijiroku_api_request_int('per_page', 12, 1, 20)
);

gijiroku_api_respond_json($payload);
