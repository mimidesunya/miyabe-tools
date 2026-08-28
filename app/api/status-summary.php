<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'homepage' . DIRECTORY_SEPARATOR . 'runtime.php';

// 収集状況ページ専用の集計 API。
// 自治体カードは返さない。一覧はトップページにあり、このページは件数しか使わない。
// 全件を返すと 3.5MB になるので、意図的に落としている。

header('Content-Type: application/json; charset=UTF-8');
header('Cache-Control: public, max-age=30, stale-while-revalidate=120');

ob_start();

function status_summary_shrink(array $payload): array
{
    unset($payload['municipalities'], $payload['prefectures']);
    return $payload;
}

try {
    $payload = homepage_build_status_api_payload_cached();
    $payload = status_summary_shrink($payload);
    $bufferedOutput = (string)ob_get_clean();
    if (trim($bufferedOutput) !== '') {
        error_log('[status_summary_api] discarded unexpected output while building payload');
    }

    $encoded = json_encode(
        $payload,
        JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE
    );
    if (!is_string($encoded)) {
        throw new RuntimeException('status summary API JSON encode failed: ' . json_last_error_msg());
    }
    echo $encoded . "\n";
} catch (Throwable $error) {
    $bufferedOutput = (string)ob_get_clean();
    if (trim($bufferedOutput) !== '') {
        error_log('[status_summary_api] discarded unexpected output while handling failure');
    }
    error_log('[status_summary_api] ' . $error->getMessage());

    $stalePayload = read_json_cache_file(homepage_status_api_cache_path(), 0);
    if (is_array($stalePayload)) {
        $encoded = json_encode(
            status_summary_shrink($stalePayload),
            JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE
        );
        if (is_string($encoded)) {
            header('X-Status-Cache: stale');
            echo $encoded . "\n";
            return;
        }
    }

    http_response_code(500);
    echo json_encode(
        ['error' => '収集状況の集計に失敗しました'],
        JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE
    ) . "\n";
}
