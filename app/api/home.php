<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'homepage' . DIRECTORY_SEPARATOR . 'runtime.php';

// トップページ専用 API。
// 空の自治体や空の機能は PHP 側で除外し、JS は返ってきた配列だけを描画する。

header('Content-Type: application/json; charset=UTF-8');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');

echo json_encode(
    homepage_build_api_payload_cached(),
    JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES
) . "\n";
