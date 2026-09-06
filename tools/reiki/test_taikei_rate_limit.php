#!/usr/bin/env php
<?php
declare(strict_types=1);

// 429 の記憶がホスト単位で、プロセスをまたいで効くことを確かめる。
//
// 自治体 1 件ごとに別プロセスなので、プロセス内だけで覚えていると
// 次の自治体がまた全速で始めて 429 を受け直す。g-reiki は 531 自治体で
// 1 ホストを共有しており、上限もホスト単位なので、記憶もホスト単位にする。

require_once __DIR__ . DIRECTORY_SEPARATOR . 'scrapers' . DIRECTORY_SEPARATOR . 'taikei.php';

$failures = 0;

function check(string $label, bool $condition): void
{
    global $failures;
    if ($condition) {
        echo "ok   {$label}\n";
        return;
    }
    $failures += 1;
    echo "FAIL {$label}\n";
}

$path = host_rate_limit_path();
$backup = is_file($path) ? file_get_contents($path) : null;
if (is_file($path)) {
    unlink($path);
}

try {
    check('印が無ければ制限されていない', host_rate_limited_recently('www1.g-reiki.net') === false);
    check('ホスト名が空なら判定しない', host_rate_limited_recently('') === false);

    remember_host_rate_limit('www1.g-reiki.net');
    check('印を付けたら覚えている', host_rate_limited_recently('www1.g-reiki.net') === true);
    check('別のホストには波及しない', host_rate_limited_recently('en3-jg.d1-law.com') === false);

    // 別プロセスから読んでも同じ答えになる（ここが記憶をファイルへ出す理由）。
    // 引用符の扱いが OS で違うので、-r ではなく一時ファイルにして渡す。
    $probePath = sys_get_temp_dir() . DIRECTORY_SEPARATOR . 'taikei_rate_limit_probe.php';
    file_put_contents(
        $probePath,
        "<?php
require " . var_export(
            __DIR__ . DIRECTORY_SEPARATOR . 'scrapers' . DIRECTORY_SEPARATOR . 'taikei.php',
            true
        ) . ";
echo host_rate_limited_recently('www1.g-reiki.net') ? 'yes' : 'no';
"
    );
    $output = shell_exec(escapeshellarg(PHP_BINARY) . ' ' . escapeshellarg($probePath));
    @unlink($probePath);
    check('別プロセスからも見える', trim((string)$output) === 'yes');

    // 古い印は忘れる。取得元が制限を緩めても遅いままにしない。
    file_put_contents(
        $path,
        json_encode(['old.example' => time() - 90000], JSON_UNESCAPED_SLASHES)
    );
    check('期限切れの印は使わない', host_rate_limited_recently('old.example') === false);
    remember_host_rate_limit('www1.g-reiki.net');
    $payload = json_decode((string)file_get_contents($path), true);
    check('期限切れの印は書き戻しで落とす', is_array($payload) && !isset($payload['old.example']));
} finally {
    if ($backup === null) {
        if (is_file($path)) {
            unlink($path);
        }
    } else {
        file_put_contents($path, $backup);
    }
}

echo $failures === 0 ? "OK\n" : "FAILED ({$failures})\n";
exit($failures === 0 ? 0 : 1);
