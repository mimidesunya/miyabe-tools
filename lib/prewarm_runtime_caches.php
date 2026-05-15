<?php
declare(strict_types=1);

require_once __DIR__ . DIRECTORY_SEPARATOR . 'homepage' . DIRECTORY_SEPARATOR . 'runtime.php';

// deploy 直後の初回アクセスを軽くするため、重い ready 一覧とトップ payload を先に固めておく。

function prewarm_forget_runtime_cache(string $path): void
{
    if (is_file($path)) {
        @unlink($path);
    }
}

// deploy 後は古い slug 判定や ready 一覧を引きずらないよう、関連 cache を毎回作り直す。
prewarm_forget_runtime_cache(municipality_catalog_cache_path());
prewarm_forget_runtime_cache(homepage_api_cache_path());

function prewarm_step(string $label, callable $builder): void
{
    $startedAt = microtime(true);
    $result = $builder();
    $elapsed = microtime(true) - $startedAt;
    $count = is_array($result) ? count($result) : 0;
    fwrite(STDOUT, sprintf("[PREWARM] %s count=%d time=%.3fs\n", $label, $count, $elapsed));
}

prewarm_step('municipality_catalog', static fn (): array => municipality_catalog());
// homepage_build_api_payload() は配列を返すだけなので、prewarm では cache ファイルまで書き切る helper を使う。
prewarm_step('homepage_api_payload', static fn (): array => homepage_rebuild_api_payload_cache());
