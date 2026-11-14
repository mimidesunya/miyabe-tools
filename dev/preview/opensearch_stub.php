<?php
declare(strict_types=1);

// 手元の詳細画面（/search/detail/）のための、OpenSearch の代わり。
//
//     php -S 127.0.0.1:18092 dev/preview/opensearch_stub.php
//
// 詳細画面は「ID で 1 件」を OpenSearch に聞く（lib/opensearch_search.php の
// miyabe_search_fetch_detail_document）。その問い合わせだけを受け、本番の公開 API
// /api/document から同じ文書をもらって、OpenSearch の答えの形にして返す。
// ほかの問い合わせ（検索そのもの）は扱わない。検索は router.php が /api/search を本番へ取りに行く。

const STUB_UPSTREAM = 'https://tools.miya.be';

header('Content-Type: application/json; charset=utf-8');
$request = json_decode((string)file_get_contents('php://input'), true);
$filters = is_array($request['query']['bool']['filter'] ?? null) ? $request['query']['bool']['filter'] : [];
$id = '';
$docType = 'minutes';
foreach ($filters as $filter) {
    if (isset($filter['ids']['values'][0])) {
        $id = (string)$filter['ids']['values'][0];
    }
    if (isset($filter['term']['doc_type'])) {
        $docType = (string)$filter['term']['doc_type'];
    }
}
if ($id === '') {
    echo json_encode(['hits' => ['hits' => []]]);
    return;
}

$url = STUB_UPSTREAM . '/api/document?' . http_build_query(['id' => $id, 'doc_type' => $docType]);
$context = stream_context_create(['http' => [
    'timeout' => 30,
    'ignore_errors' => true,
    'header' => "User-Agent: miyabe-tools-preview\r\nAccept: application/json\r\n",
]]);
$payload = json_decode((string)@file_get_contents($url, false, $context), true);
$document = is_array($payload['document'] ?? null) ? $payload['document'] : (is_array($payload['item'] ?? null) ? $payload['item'] : null);
if ($document === null) {
    echo json_encode(['hits' => ['hits' => []]]);
    return;
}
echo json_encode(['hits' => ['hits' => [['_id' => $id, '_source' => $document]]]], JSON_UNESCAPED_UNICODE);
