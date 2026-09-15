<?php
// 自動探索の結果を登録簿へ重ねる規則が、PHP の 2 か所で Python と同じであることを確かめる。
//
// Python（tools/discovered_sources.py の apply_to_row）は探索結果で巡回するのに、
// 画面（lib/municipalities.php）と例規の taikei.php は登録簿だけを見ていた。
// 北方町は取得が 1 秒で落ち続け、村田町は検索できるのに画面で「取得元未特定」だった。
// Python 側は tools/test_discovered_sources.py の ApplyTest。

declare(strict_types=1);

require_once __DIR__ . '/../lib/municipalities.php';
require_once __DIR__ . '/reiki/scrapers/taikei.php';

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

$empty = ['url' => '', 'system_type' => '', 'crawl_status' => 'unresolved'];
$found = ['url' => 'https://example.test/reiki_menu.html', 'system_type' => 'taikei', 'confidence' => 'high'];

$cases = [
    '空の行へ high を重ねる' => [$found, $empty, ['url' => $found['url'], 'system_type' => 'taikei', 'crawl_status' => 'enabled']],
    'medium も使う' => [['confidence' => 'medium'] + $found, $empty, ['url' => $found['url'], 'system_type' => 'taikei', 'crawl_status' => 'enabled']],
    'low は使わない' => [['confidence' => 'low'] + $found, $empty, $empty],
    '系統が決まっていなければ使わない' => [['system_type' => ''] + $found, $empty, $empty],
    '探索結果が無ければそのまま' => [null, $empty, $empty],
    '登録簿に URL があれば人の値を優先' => [
        $found,
        ['url' => 'https://registry.test/', 'system_type' => 'g-reiki', 'crawl_status' => 'enabled'],
        ['url' => 'https://registry.test/', 'system_type' => 'g-reiki', 'crawl_status' => 'enabled'],
    ],
];

foreach ($cases as $label => [$entry, $row, $expected]) {
    check("画面: {$label}", apply_discovered_source($entry, $row) === $expected);
    check("taikei.php: {$label}", taikei_apply_discovered_source($entry, $row) === $expected);
}

// 判定を直す前の探索の記録は、見直されるまで重ねない（村田町の会期日程ページ）。
$stored = [
    'old' => $found,
    'new' => $found + ['discoverer_version' => '2'],
];
check('古い版の探索の記録は使わない', array_keys(discovered_sources_of_version($stored, 2)) === ['new']);
check('版の定数が Python と同じ', DISCOVERED_SOURCE_VERSIONS['reiki'] === TAIKEI_DISCOVERER_VERSION);
$pythonVersions = (string)file_get_contents(__DIR__ . '/discovered_sources.py');
foreach (DISCOVERED_SOURCE_VERSIONS as $task => $version) {
    check("DISCOVERER_VERSIONS['{$task}'] が Python と同じ", preg_match('/"' . $task . '":\s*' . $version . '\b/', $pythonVersions) === 1);
}

echo $failures === 0 ? "OK\n" : "FAILED ({$failures})\n";
exit($failures === 0 ? 0 : 1);
