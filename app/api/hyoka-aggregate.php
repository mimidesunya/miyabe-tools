<?php
declare(strict_types=1);

// 事務事業評価の事業の金額を集計する（事業ごとの索引の金額の明細）。自治体・都道府県・部署・方向性・年度ごとに分けられる。

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'hyoka_projects.php';

try {
    $payload = miyabe_hyoka_projects_execute_aggregate($_GET);
    miyabe_search_respond_json($payload, ($payload['status'] ?? '') === 'query_error' ? 422 : 200);
} catch (MiyabeOpenSearchException $error) {
    error_log('[api/hyoka-aggregate] ' . $error->getMessage());
    miyabe_search_respond_json([
        'status' => $error->errorCode,
        'error' => 'OpenSearch search is unavailable.',
    ], $error->httpStatus);
} catch (Throwable $error) {
    error_log('[api/hyoka-aggregate] ' . $error->getMessage());
    miyabe_search_respond_json([
        'status' => 'search_error',
        'error' => '金額の集計に失敗しました。',
    ], 500);
}
