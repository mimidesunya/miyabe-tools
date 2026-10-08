<?php
// 会議録の公開表示に、最後に取得元を歩いた時刻（最終巡回）を添えること。
//
// 確認日は成功した回しか更新しないので、失敗が続く自治体は「更新」の時刻が出ず、
// 表示が 6 月のまま止まって見えた（2026-10-08 に 52 自治体。実際は 10 月にも歩いていた）。
// 走査記録（source_coverage.json）の updated_at は失敗した回でも残る。

declare(strict_types=1);

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'homepage' . DIRECTORY_SEPARATOR . 'runtime.php';

$failures = [];

function walk_time_check(array &$failures, string $label, bool $condition): void
{
    if (!$condition) {
        $failures[] = $label;
    }
}

walk_time_check($failures, '走査記録の時刻を画面の書き方にする', homepage_coverage_time_label('20261007_175100') === '2026-10-07 17:51');
walk_time_check($failures, '読めない時刻は出さない', homepage_coverage_time_label('') === '' && homepage_coverage_time_label('2026-10-07') === '');

$stale = ['label' => '一部検索可', 'detail' => "735/736件\n最新日付 2026-06-15"];
$coverage = ['state' => 'complete', 'updated_at' => '20261007_175100'];
walk_time_check(
    $failures,
    '「更新」が無い詳細に最終巡回を足す',
    homepage_display_with_walk_time($stale, $coverage)['detail'] === "735/736件\n最新日付 2026-06-15\n最終巡回 2026-10-07 17:51"
);

$current = ['label' => '完了', 'detail' => "10652件\n更新 2026-10-05 08:00:43"];
walk_time_check(
    $failures,
    '「更新」があれば足さない',
    homepage_display_with_walk_time($current, $coverage) === $current
);
walk_time_check(
    $failures,
    '走査記録が無ければそのまま',
    homepage_display_with_walk_time($stale, null) === $stale && homepage_display_with_walk_time(null, $coverage) === null
);
$once = homepage_display_with_walk_time($stale, $coverage);
walk_time_check($failures, '二度足さない', homepage_display_with_walk_time($once, $coverage) === $once);

if ($failures !== []) {
    fwrite(STDERR, "FAILED:\n- " . implode("\n- ", $failures) . "\n");
    exit(1);
}
echo "OK: the last walk time is shown when the last success is old\n";
