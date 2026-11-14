<?php
// 応答のあとの控えの作り直し（状況 API・実行状況・トップ API）の部品の試験。
//
// 状況 API は、長く誰も見ていないと控えが古いまま残り、そのあとの 1 回目の作り直しに
// 40 秒〜2 分ほどかかる。その間は何度取っても古い控えが返るので、止まったように見えた
// （2026-11-12・11-14）。作り直しを始めたか（started）、ほかの要求が作り直している最中か
// （running）を知らせ、遅い作り直しと失敗はログに出す。
// `php tools/test_homepage_background_refresh.php`

declare(strict_types=1);

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'homepage' . DIRECTORY_SEPARATOR . 'runtime.php';

$failures = [];

function background_refresh_check(bool $ok, string $label): void
{
    global $failures;
    if (!$ok) {
        $failures[] = $label;
    }
}

$workDir = sys_get_temp_dir() . DIRECTORY_SEPARATOR . 'miyabe-background-refresh-' . bin2hex(random_bytes(4));
@mkdir($workDir, 0777, true);
$errorLogPath = $workDir . DIRECTORY_SEPARATOR . 'error.log';
ini_set('error_log', $errorLogPath);

function background_refresh_log(): string
{
    global $errorLogPath;
    clearstatcache();
    return is_file($errorLogPath) ? (string)file_get_contents($errorLogPath) : '';
}

// 1. 誰も作り直していなければロックを取れる（置き場が無ければ作る）。
$lockPath = $workDir . DIRECTORY_SEPARATOR . 'nested' . DIRECTORY_SEPARATOR . 'status_api_payload.lock';
$lock = homepage_background_refresh_try_lock($lockPath);
background_refresh_check(is_resource($lock), '空いていればロックを取れる');

// 2. 作り直しの最中は、ほかの要求は重ねない（running と知らせる元）。
background_refresh_check(
    homepage_background_refresh_try_lock($lockPath) === null,
    '作り直しの最中は、ほかの要求がロックを取れない'
);

// 3. 作り直しが例外で失敗しても、ロックを外してファイルを消し、理由をログに出す。
$ok = homepage_run_background_cache_refresh('status_api', $lock, $lockPath, static function (): void {
    throw new RuntimeException('opensearch down');
});
background_refresh_check($ok === false, '失敗した作り直しは false を返す');
background_refresh_check(
    str_contains(background_refresh_log(), '[status_api] background cache refresh failed: opensearch down'),
    '失敗の理由をログに出す'
);
clearstatcache();
background_refresh_check(!is_file($lockPath), '失敗してもロックのファイルを消す');
$lock = homepage_background_refresh_try_lock($lockPath);
background_refresh_check(is_resource($lock), '失敗のあとは、すぐ次の作り直しを始められる');

// 4. 作り直しが遅ければ、かかった時間をログに出す（止まったのか遅いのかを見分ける）。
$built = false;
$ok = homepage_run_background_cache_refresh('status_api', $lock, $lockPath, static function () use (&$built): void {
    $built = true;
}, 0.0);
background_refresh_check($ok === true && $built, '作り直しは最後まで走って true を返す');
background_refresh_check(
    preg_match('/\[status_api\] background cache refresh took [0-9]+\.[0-9]s/', background_refresh_log()) === 1,
    '遅い作り直しは、かかった時間をログに出す'
);
clearstatcache();
background_refresh_check(!is_file($lockPath), '終わればロックのファイルを消す');

// 5. 速い作り直しでは時間のログを出さない。
$before = background_refresh_log();
$lock = homepage_background_refresh_try_lock($lockPath);
homepage_run_background_cache_refresh('task_status_api', $lock, $lockPath, static function (): void {
});
background_refresh_check(
    !str_contains(substr(background_refresh_log(), strlen($before)), 'took'),
    '速い作り直しでは時間のログを出さない'
);

// 6. ロックのファイルを開けないときは false（作り直しを頼めない）。
background_refresh_check(
    homepage_background_refresh_try_lock($workDir) === false,
    'ロックのファイルを開けなければ false'
);

// 7. CLI（prewarm など）では、応答のあとの作り直しを頼まず、ロックも作らない。
background_refresh_check(
    homepage_schedule_background_cache_refresh('status_api', $lockPath, static function (): void {
    }) === '',
    'CLI では応答のあとの作り直しを頼まない'
);
clearstatcache();
background_refresh_check(!is_file($lockPath), 'CLI ではロックのファイルを作らない');

foreach (glob($workDir . DIRECTORY_SEPARATOR . 'nested' . DIRECTORY_SEPARATOR . '*') ?: [] as $path) {
    @unlink($path);
}
@rmdir($workDir . DIRECTORY_SEPARATOR . 'nested');
foreach (glob($workDir . DIRECTORY_SEPARATOR . '*') ?: [] as $path) {
    @unlink($path);
}
@rmdir($workDir);

if ($failures !== []) {
    foreach ($failures as $failure) {
        fwrite(STDERR, "FAIL: {$failure}\n");
    }
    exit(1);
}
fwrite(STDOUT, "OK: background cache refresh reports running refreshes, logs slow and failed ones, and always releases its lock\n");
