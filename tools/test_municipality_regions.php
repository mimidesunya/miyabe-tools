<?php
// 地方の区分。自治体一覧の並びがここで決まるので、境界を固定しておく。
// 都道府県コードの並びをそのまま使うため、境目を間違えると
// 1 つの県がまるごと別の地方へ移る。

declare(strict_types=1);

require_once __DIR__ . '/../lib/municipalities.php';

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

$cases = [
    '01' => 'hokkaido',   // 北海道
    '02' => 'tohoku',     // 青森
    '07' => 'tohoku',     // 福島
    '08' => 'kanto',      // 茨城
    '14' => 'kanto',      // 神奈川
    '15' => 'chubu',      // 新潟
    '23' => 'chubu',      // 愛知
    '24' => 'kinki',      // 三重
    '30' => 'kinki',      // 和歌山
    '31' => 'chugoku',    // 鳥取
    '35' => 'chugoku',    // 山口
    '36' => 'shikoku',    // 徳島
    '39' => 'shikoku',    // 高知
    '40' => 'kyushu',     // 福岡
    '47' => 'kyushu',     // 沖縄
];
foreach ($cases as $prefCode => $expected) {
    check(
        "{$prefCode} は {$expected}",
        municipality_region_for_prefecture($prefCode) === $expected
    );
}

// 範囲の外は空。存在しないコードで地方を作らない。
foreach (['', '00', '48', '99', 'ab'] as $prefCode) {
    check("『{$prefCode}』は地方を持たない", municipality_region_for_prefecture($prefCode) === '');
}

// 47 都道府県がすべて、8 つの地方のどれかに入る。
$regions = municipality_region_names();
check('地方は 8 区分', count($regions) === 8);
$covered = [];
foreach (array_keys(municipality_prefecture_names()) as $prefCode) {
    $region = municipality_region_for_prefecture((string)$prefCode);
    check("{$prefCode} が地方に入る", $region !== '' && isset($regions[$region]));
    $covered[$region] = true;
}
check('どの地方にも都道府県がある', count($covered) === count($regions));

// 自治体コードからも同じ地方になる。一覧はこの経路で並べる。
check(
    '13101 は関東',
    municipality_region_for_prefecture(municipality_prefecture_code_from_code('13101')) === 'kanto'
);
check(
    '01100 は北海道',
    municipality_region_for_prefecture(municipality_prefecture_code_from_code('01100')) === 'hokkaido'
);

if ($failures === 0) {
    echo "OK: 47 都道府県が 8 地方に過不足なく入る\n";
    exit(0);
}
echo "FAILED ({$failures})\n";
exit(1);
