<?php
declare(strict_types=1);

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'homepage' . DIRECTORY_SEPARATOR . 'runtime.php';

function assert_true(bool $condition, string $message): void
{
    if (!$condition) {
        fwrite(STDERR, "FAIL: {$message}\n");
        exit(1);
    }
}

$targetCodes = homepage_feature_target_code_set('gijiroku');
assert_true(isset($targetCodes['37000']), '香川県が会議録の取得予定対象に含まれる');
assert_true(isset($targetCodes['01217']), '独自システムの enabled 対象が取得予定に含まれる');

$feature = [
    'title' => '香川県議会 会議録 全文検索',
    'url' => '/gijiroku/?slug=37000-kagawa-ken',
];
$summary = homepage_collect_visible_features(
    [
        'code' => '37000',
        'name' => '香川県',
        'gijiroku' => $feature,
    ],
    '37000-kagawa-ken',
    ['gijiroku' => '会議録'],
    ['gijiroku' => '🏛️'],
    [],
    [],
    [
        'gijiroku' => [
            'feature' => $feature,
            'displays' => [
                'task' => null,
                'snapshot' => null,
                'fallback' => null,
                'primary' => null,
                'publish' => null,
            ],
            'has_data' => false,
            'search_indexed' => false,
        ],
    ]
);

$visible = $summary['visible_features'] ?? [];
assert_true(count($visible) === 1, '未取得でも enabled の会議録機能を表示する');
assert_true(($visible[0]['status_label'] ?? '') === '未公開', '未取得の表示状態が未公開になる');
assert_true(($visible[0]['display']['detail'] ?? '') === '取得待ち', '未取得の詳細が取得待ちになる');

fwrite(STDOUT, "OK: planned minutes targets remain visible before acquisition\n");
