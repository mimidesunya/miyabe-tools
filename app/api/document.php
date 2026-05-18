<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'opensearch_search.php';

try {
    $id = miyabe_search_request_string('id');
    $docType = miyabe_search_request_string('doc_type', miyabe_search_request_string('type', 'minutes'));

    if ($id === '') {
        miyabe_search_respond_json([
            'status' => 'query_error',
            'error' => 'id を指定してください。',
        ], 422);
    }

    $document = miyabe_search_fetch_detail_document($id, $docType);
    if ($document === null) {
        miyabe_search_respond_json([
            'status' => 'not_found',
            'error' => '文書が見つかりませんでした。',
        ], 404);
    }

    miyabe_search_respond_json([
        'status' => 'ok',
        'document' => $document,
    ]);
} catch (MiyabeOpenSearchException $error) {
    miyabe_search_respond_json([
        'status' => $error->errorCode,
        'error' => 'OpenSearch search is unavailable.',
        'detail' => $error->getMessage(),
    ], $error->httpStatus);
} catch (Throwable $error) {
    error_log('[api/document] ' . $error->getMessage());
    miyabe_search_respond_json([
        'status' => 'document_error',
        'error' => '文書の取得に失敗しました。',
    ], 500);
}
