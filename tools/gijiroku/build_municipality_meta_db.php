<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'gijiroku_search.php';

// 会議録本文は自治体ごとの minutes.sqlite に分離したまま、
// API の自治体一覧・slug 解決だけを軽くする小さなメタ DB を作る。

$targetPath = $argv[1] ?? gijiroku_search_meta_db_path();
$startedAt = microtime(true);
$records = gijiroku_search_rebuild_meta_db($targetPath);
$elapsed = microtime(true) - $startedAt;

fwrite(
    STDOUT,
    sprintf(
        "[DONE] gijiroku municipality meta db path=%s count=%d time=%.3fs\n",
        $targetPath,
        count($records),
        $elapsed
    )
);
