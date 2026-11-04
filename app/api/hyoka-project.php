<?php
declare(strict_types=1);

// 事務事業評価の事業 1 件の全部（円にそろえた金額の明細、原典の欄と表、その事業の頁の表の文字）を返す。

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'hyoka_projects.php';

try {
    $payload = miyabe_hyoka_projects_execute_get($_GET);
    $status = match ($payload['status'] ?? '') {
        'query_error' => 422,
        'not_found' => 404,
        default => 200,
    };
    miyabe_search_respond_json($payload, $status);
} catch (MiyabeOpenSearchException $error) {
    error_log('[api/hyoka-project] ' . $error->getMessage());
    miyabe_search_respond_json([
        'status' => $error->errorCode,
        'error' => 'OpenSearch search is unavailable.',
    ], $error->httpStatus);
} catch (Throwable $error) {
    error_log('[api/hyoka-project] ' . $error->getMessage());
    miyabe_search_respond_json([
        'status' => 'document_error',
        'error' => '事業の取得に失敗しました。',
    ], 500);
}
