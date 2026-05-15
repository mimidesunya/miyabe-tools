<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'opensearch_search.php';

try {
    $payload = miyabe_search_execute_request([
        'q' => miyabe_search_request_string('q'),
        'doc_type' => miyabe_search_request_string('doc_type', miyabe_search_request_string('type', 'all')),
        'slug' => miyabe_search_request_string('slug'),
        'municipality_code' => miyabe_search_request_string('municipality_code', miyabe_search_request_string('code')),
        'pref_code' => miyabe_search_request_string('pref_code', miyabe_search_request_string('pref')),
        'start_year' => miyabe_search_request_string('start_year'),
        'end_year' => miyabe_search_request_string('end_year'),
        'page' => miyabe_search_request_int('page', 1, 1, 100000),
        'per_page' => miyabe_search_request_int('per_page', 20, 1, 100),
        'sort' => miyabe_search_request_string('sort', 'date'),
    ]);
    $status = ($payload['status'] ?? '') === 'query_error' ? 422 : 200;
    miyabe_search_respond_json($payload, $status);
} catch (MiyabeOpenSearchException $error) {
    miyabe_search_respond_json([
        'status' => $error->errorCode,
        'error' => 'OpenSearch search is unavailable.',
        'detail' => $error->getMessage(),
    ], $error->httpStatus);
} catch (Throwable $error) {
    error_log('[api/search] ' . $error->getMessage());
    miyabe_search_respond_json([
        'status' => 'search_error',
        'error' => '検索に失敗しました。',
    ], 500);
}
