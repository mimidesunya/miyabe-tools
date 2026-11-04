<?php
declare(strict_types=1);

// 事務事業評価の事業を条件で探す（事業ごとの索引）。費用・方向性・評価・指標の目標割れで絞り、費用の順にも並べる。

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'hyoka_projects.php';

try {
    $payload = miyabe_hyoka_projects_execute_search($_GET);
    miyabe_search_respond_json($payload, ($payload['status'] ?? '') === 'query_error' ? 422 : 200);
} catch (MiyabeOpenSearchException $error) {
    // OpenSearch の生のエラーには索引の世代名やノードの ID が入るので、公開の応答には出さずログへ。
    error_log('[api/hyoka-projects] ' . $error->getMessage());
    miyabe_search_respond_json([
        'status' => $error->errorCode,
        'error' => 'OpenSearch search is unavailable.',
    ], $error->httpStatus);
} catch (Throwable $error) {
    error_log('[api/hyoka-projects] ' . $error->getMessage());
    miyabe_search_respond_json([
        'status' => 'search_error',
        'error' => '事業の検索に失敗しました。',
    ], 500);
}
