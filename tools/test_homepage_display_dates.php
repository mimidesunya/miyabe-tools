<?php
// 表示する「最新日付」が、検索できる範囲より古くならないこと。
//
// 鮮度は scrape_state.json の plan_summary から取る。あれは**その実行が
// 計画した分**の最大日でしかない。途中でエラーになって古い年しか計画
// しなかった実行が残ると、実際に持っている文書より古い日付を出す。
// 仙台市は 2026-02-17 の会議録を検索できるのに 1991-01-14 と出ていた。

declare(strict_types=1);

require_once __DIR__ . '/../lib/homepage/runtime.php';

$failures = 0;

function check(string $label, bool $condition): void
{
    global $failures;
    if ($condition) {
        return;
    }
    $failures += 1;
    fwrite(STDERR, "FAIL {$label}\n");
}

$coverage = ['from' => '1990-02-28', 'to' => '2026-02-17', 'document_count' => 1678];

// 実行が古い年しか計画しなかった場合。検索できる範囲の終わりまで引き上げる。
$display = homepage_display_with_search_coverage(
    ['freshness_date' => '1991-01-14', 'freshness_basis' => 'latest_document'],
    $coverage
);
check('検索範囲より古い鮮度は引き上げる', $display['freshness_date'] === '2026-02-17');
check('引き上げても意味は最新文書のまま', $display['freshness_basis'] === 'latest_document');

// 鮮度の方が新しいなら触らない。索引がまだ追いついていないだけ。
$newer = homepage_display_with_search_coverage(
    ['freshness_date' => '2026-08-01', 'freshness_basis' => 'latest_document'],
    $coverage
);
check('検索範囲より新しい鮮度はそのまま', $newer['freshness_date'] === '2026-08-01');

// 鮮度が空でも、検索できる範囲が分かっていれば出せる。
$empty = homepage_display_with_search_coverage(['freshness_date' => ''], $coverage);
check('鮮度が無ければ検索範囲から補う', $empty['freshness_date'] === '2026-02-17');

// 検索範囲が無い、または日付として読めないときは触らない。
$noCoverage = homepage_display_with_search_coverage(['freshness_date' => '1991-01-14'], null);
check('検索範囲が無ければそのまま', $noCoverage['freshness_date'] === '1991-01-14');
$badCoverage = homepage_display_with_search_coverage(
    ['freshness_date' => '1991-01-14'],
    ['from' => '', 'to' => '2026-02', 'document_count' => 3]
);
check('日付として読めない範囲は使わない', $badCoverage['freshness_date'] === '1991-01-14');

// 表示行そのものも確かめる。
check(
    '表示行は最新日付として出る',
    homepage_task_display_freshness_line($display) === '最新日付 2026-02-17'
);

if ($failures === 0) {
    echo "OK: 最新日付は検索できる範囲より古くならない\n";
    exit(0);
}
echo "FAILED ({$failures})\n";
exit(1);
