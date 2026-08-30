<?php
// 会議録の公開表示。走査記録どおりに出ること。
//
// 走査記録を全系統に入れたとき、分類済み validation を書かない系統
// （kensakusystem / amivoice / msearch、141 自治体）が
// 「一部検索可（追加取得中・予定）」から抜けられなくなっていた。
// 発見数と検証数の一致を必須にしていたが、その検証数を書かない系統がある。

declare(strict_types=1);

require_once __DIR__ . '/../lib/homepage/runtime.php';

/** 走査記録と scrape_state を置いた作業ディレクトリを作る。 */
function make_feature(string $dir, array $coverage, ?array $validation): array
{
    @mkdir($dir, 0777, true);
    file_put_contents($dir . '/source_coverage.json', json_encode($coverage));
    file_put_contents(
        $dir . '/scrape_state.json',
        json_encode($validation === null ? [] : ['validation' => $validation])
    );
    return ['work_dir' => $dir, 'downloads_dir' => $dir . '/downloads'];
}

$base = sys_get_temp_dir() . DIRECTORY_SEPARATOR . 'miyabe_minutes_status_' . getmypid();
$coverage = [
    'mode' => 'source_discovery_coverage',
    'rule_version' => 2,
    'state' => 'complete',
    'discovered_count' => 50,
    'updated_at' => '20260830_120000',
];

$failures = [];

// 分類済み validation を書かない系統。走査が完了していれば完了と出る。
$feature = make_feature($base . '/no_validation', $coverage, null);
$result = homepage_gijiroku_acquisition_status($feature, 'kensakusystem', true, false, 50, null);
if (($result['state'] ?? '') !== 'complete') {
    $failures[] = 'validation を書かない系統が完了表示にならない: ' . ($result['state'] ?? '(空)');
}

// 書く系統で、発見数と検証数が食い違う（本当に取得の途中）。
$feature = make_feature($base . '/mismatch', $coverage, [
    'mode' => 'classified_scrape_result',
    'discovered_count' => 80,
    'progress_current' => 10,
    'progress_total' => 80,
]);
$result = homepage_gijiroku_acquisition_status($feature, 'dbsr', true, false, 10, null);
if (($result['state'] ?? '') === 'complete') {
    $failures[] = '発見数と検証数が食い違うのに完了と出た';
}

// 書く系統で一致している（完了）。
$feature = make_feature($base . '/match', $coverage, [
    'mode' => 'classified_scrape_result',
    'discovered_count' => 50,
    'progress_current' => 50,
    'progress_total' => 50,
]);
$result = homepage_gijiroku_acquisition_status($feature, 'dbsr', true, false, 50, null);
if (($result['state'] ?? '') !== 'complete') {
    $failures[] = '一致しているのに完了と出ない: ' . ($result['state'] ?? '(空)');
}

// 走査が未完了なら、完了とは出ない。
$feature = make_feature(
    $base . '/partial',
    ['mode' => 'source_discovery_coverage', 'rule_version' => 2, 'state' => 'partial_error',
     'discovered_count' => 50, 'missed_pages' => 3, 'updated_at' => '20260830_120000'],
    null
);
$result = homepage_gijiroku_acquisition_status($feature, 'kensakusystem', true, false, 50, null);
if (($result['state'] ?? '') === 'complete') {
    $failures[] = '走査が未完了なのに完了と出た';
}

foreach (['no_validation', 'mismatch', 'match', 'partial'] as $name) {
    @unlink($base . '/' . $name . '/source_coverage.json');
    @unlink($base . '/' . $name . '/scrape_state.json');
    @rmdir($base . '/' . $name);
}
@rmdir($base);

if ($failures !== []) {
    fwrite(STDERR, "NG: " . implode("\n    ", $failures) . "\n");
    exit(1);
}
echo "OK: 会議録の公開表示が走査記録どおり (4 件)\n";
