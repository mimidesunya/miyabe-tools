<?php
declare(strict_types=1);

require_once __DIR__ . DIRECTORY_SEPARATOR . 'homepage' . DIRECTORY_SEPARATOR . 'runtime.php';
require_once __DIR__ . DIRECTORY_SEPARATOR . 'gijiroku_search.php';
require_once __DIR__ . DIRECTORY_SEPARATOR . 'reiki_search.php';

// deploy 直後の初回アクセスを軽くするため、重い ready 一覧とトップ payload を先に固めておく。

function prewarm_step(string $label, callable $builder): void
{
    $startedAt = microtime(true);
    $result = $builder();
    $elapsed = microtime(true) - $startedAt;
    $count = is_array($result) ? count($result) : 0;
    fwrite(STDOUT, sprintf("[PREWARM] %s count=%d time=%.3fs\n", $label, $count, $elapsed));
}

prewarm_step('municipality_catalog', static fn (): array => municipality_catalog());
prewarm_step('homepage_api_payload', static fn (): array => homepage_build_api_payload());
prewarm_step('gijiroku_ready', static fn (): array => gijiroku_search_ready_summaries());
prewarm_step('reiki_ready', static fn (): array => reiki_search_ready_summaries());
