<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'homepage' . DIRECTORY_SEPARATOR . 'runtime.php';

// トップページ専用 API。
// 空の自治体や空の機能は PHP 側で除外し、JS は返ってきた配列だけを描画する。

header('Content-Type: application/json; charset=UTF-8');
header('Cache-Control: public, max-age=5, stale-while-revalidate=30');

ob_start();

try {
    $payload = homepage_build_api_payload_cached();
    $bufferedOutput = (string)ob_get_clean();
    if (trim($bufferedOutput) !== '') {
        error_log('[home_api] discarded unexpected output while building payload');
    }

    $encoded = json_encode(
        $payload,
        JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE
    );
    if (!is_string($encoded)) {
        throw new RuntimeException('homepage API JSON encode failed: ' . json_last_error_msg());
    }

    echo $encoded . "\n";
} catch (Throwable $error) {
    $bufferedOutput = (string)ob_get_clean();
    if (trim($bufferedOutput) !== '') {
        error_log('[home_api] discarded unexpected output while handling failure');
    }

    error_log('[home_api] ' . $error->getMessage());

    $stalePayload = read_json_cache_file(homepage_api_cache_path(), 0);
    if (is_array($stalePayload)) {
        $encoded = json_encode(
            $stalePayload,
            JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE
        );
        if (is_string($encoded)) {
            header('X-Homepage-Cache: stale');
            echo $encoded . "\n";
            return;
        }
    }

    http_response_code(500);
    echo json_encode(
        ['error' => '自治体一覧の生成に失敗しました'],
        JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE
    ) . "\n";
}
