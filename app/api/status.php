<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'homepage' . DIRECTORY_SEPARATOR . 'runtime.php';

// 収集状況ページ専用 API。
// 公開データがない自治体も、取得元台帳の状態（未取得・対象外・未実装など）を返す。

header('Content-Type: application/json; charset=UTF-8');
header('Cache-Control: public, max-age=15, stale-while-revalidate=60');

ob_start();

try {
    $prefectureFilter = is_string($_GET['prefecture'] ?? null) ? (string)$_GET['prefecture'] : '';
    $payload = homepage_build_status_api_payload_cached();
    $payload = homepage_filter_api_payload_by_prefecture($payload, $prefectureFilter);
    $payload = homepage_sanitize_api_payload_displays($payload);
    $bufferedOutput = (string)ob_get_clean();
    if (trim($bufferedOutput) !== '') {
        error_log('[status_api] discarded unexpected output while building payload');
    }

    $encoded = json_encode(
        $payload,
        JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE
    );
    if (!is_string($encoded)) {
        throw new RuntimeException('status API JSON encode failed: ' . json_last_error_msg());
    }
    echo $encoded . "\n";
} catch (Throwable $error) {
    $bufferedOutput = (string)ob_get_clean();
    if (trim($bufferedOutput) !== '') {
        error_log('[status_api] discarded unexpected output while handling failure');
    }
    error_log('[status_api] ' . $error->getMessage());

    $stalePayload = read_json_cache_file(homepage_status_api_cache_path(), 0);
    if (is_array($stalePayload)) {
        $prefectureFilter = is_string($_GET['prefecture'] ?? null) ? (string)$_GET['prefecture'] : '';
        $stalePayload = homepage_filter_api_payload_by_prefecture($stalePayload, $prefectureFilter);
        $encoded = json_encode(
            homepage_sanitize_api_payload_displays($stalePayload),
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
        ['error' => '収集状況一覧の生成に失敗しました'],
        JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE
    ) . "\n";
}
