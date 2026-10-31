<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'opensearch_search.php';
require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'hyoka_display.php';

/**
 * 事務事業評価の結果に、事業ごとの欄に分けてある文書なら、その頁（頁区切りが無ければ文書全体）の事業を添える。
 * 検索語を含む事業を先に最大 5 件（records）と、事業の数（records_total）。画面の一覧と MCP の両方で使う。
 */
function search_api_attach_hyoka_records(array $payload): array
{
    if (($payload['status'] ?? '') !== 'ok' || ($payload['doc_type'] ?? '') !== 'hyoka' || !is_array($payload['items'] ?? null)) {
        return $payload;
    }
    $query = (string)($payload['query'] ?? '');
    $terms = $query !== '' ? (japanese_search_prepare_query($query)['highlight_terms'] ?? []) : [];
    foreach ($payload['items'] as $index => $item) {
        $found = miyabe_hyoka_display_search_records((string)($item['id'] ?? ''), is_array($terms) ? $terms : []);
        $payload['items'][$index]['records'] = $found['records'];
        $payload['items'][$index]['records_total'] = $found['total'];
    }
    return $payload;
}

try {
    $payload = miyabe_search_execute_request([
        'q' => miyabe_search_request_string('q'),
        'doc_type' => miyabe_search_request_string('doc_type', miyabe_search_request_string('type', 'minutes')),
        'slug' => miyabe_search_request_string('slug'),
        'municipality_code' => miyabe_search_request_string('municipality_code', miyabe_search_request_string('code')),
        'pref_code' => miyabe_search_request_string('pref_code', miyabe_search_request_string('pref')),
        'start_date' => miyabe_search_request_string('start_date'),
        'end_date' => miyabe_search_request_string('end_date'),
        'start_year' => miyabe_search_request_string('start_year'),
        'end_year' => miyabe_search_request_string('end_year'),
        'fiscal_year' => miyabe_search_request_string('fiscal_year'),
        'kind' => miyabe_search_request_string('kind'),
        'page' => miyabe_search_request_int('page', 1, 1, 100000),
        'per_page' => miyabe_search_request_int('per_page', 20, 1, 100),
        'sort' => miyabe_search_request_string('sort', 'date'),
        'include_facets' => miyabe_search_request_string('include_facets'),
        'include_body_highlight' => miyabe_search_request_string('include_body_highlight'),
        'track_total_hits_limit' => miyabe_search_request_int('track_total_hits_limit', 10000, 1, 100000),
    ]);
    $payload = search_api_attach_hyoka_records($payload);
    $status = ($payload['status'] ?? '') === 'query_error' ? 422 : 200;
    miyabe_search_respond_json($payload, $status);
} catch (MiyabeOpenSearchException $error) {
    // OpenSearch の生のエラーには索引の世代名やノードの ID が入るので、公開の応答には出さずログへ。
    error_log('[api/search] ' . $error->getMessage());
    miyabe_search_respond_json([
        'status' => $error->errorCode,
        'error' => 'OpenSearch search is unavailable.',
    ], $error->httpStatus);
} catch (Throwable $error) {
    error_log('[api/search] ' . $error->getMessage());
    miyabe_search_respond_json([
        'status' => 'search_error',
        'error' => '検索に失敗しました。',
    ], 500);
}
