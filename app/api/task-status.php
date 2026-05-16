<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'homepage' . DIRECTORY_SEPARATOR . 'runtime.php';

header('Content-Type: application/json; charset=UTF-8');
header('Cache-Control: no-store, max-age=0');

ob_start();

try {
    $payload = homepage_build_task_status_payload();
    $etag = '"' . (string)($payload['version'] ?? sha1(json_encode($payload))) . '"';
    header('ETag: ' . $etag);

    $requestEtag = trim((string)($_SERVER['HTTP_IF_NONE_MATCH'] ?? ''));
    if ($requestEtag !== '' && $requestEtag === $etag) {
        ob_end_clean();
        http_response_code(304);
        return;
    }

    $bufferedOutput = (string)ob_get_clean();
    if (trim($bufferedOutput) !== '') {
        error_log('[task_status_api] discarded unexpected output while building payload');
    }

    $encoded = json_encode(
        $payload,
        JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE
    );
    if (!is_string($encoded)) {
        throw new RuntimeException('task status API JSON encode failed: ' . json_last_error_msg());
    }
    echo $encoded . "\n";
} catch (Throwable $error) {
    $bufferedOutput = (string)ob_get_clean();
    if (trim($bufferedOutput) !== '') {
        error_log('[task_status_api] discarded unexpected output while handling failure');
    }
    error_log('[task_status_api] ' . $error->getMessage());
    http_response_code(500);
    echo json_encode(
        ['error' => '実行状況の取得に失敗しました'],
        JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE
    ) . "\n";
}
