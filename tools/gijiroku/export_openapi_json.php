<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'gijiroku_api.php';

$targetPath = gijiroku_api_openapi_disk_path();
$targetDir = dirname($targetPath);
if (!is_dir($targetDir) && !mkdir($targetDir, 0777, true) && !is_dir($targetDir)) {
    fwrite(STDERR, "Failed to create directory: {$targetDir}\n");
    exit(1);
}

$encoded = gijiroku_api_encode_json(gijiroku_api_openapi_spec(), JSON_PRETTY_PRINT) . PHP_EOL;
if (file_put_contents($targetPath, $encoded) === false) {
    fwrite(STDERR, "Failed to write OpenAPI JSON: {$targetPath}\n");
    exit(1);
}

fwrite(STDOUT, "Wrote {$targetPath}\n");
