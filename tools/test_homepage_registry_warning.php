<?php
// 会議録の公開表示で、警告の扱いが台帳の判断と食い違わないこと。
//
// 1. 台帳で取得しないと決めた取得元（取得対象外・取得元未特定）に残った警告は、
//    エラーと同じく過去の実行の名残である。直島町・御宿町・留寿都村・長沼町は
//    これを先に出していたので、台帳の判断が見えず、中身の無い「警告あり」になっていた。
// 2. データが無い間は反映の表示を出すが、「警告あり」の判定は取得の表示の警告も
//    見ている。取得の表示の警告を載せないと、初山別村（PDF 29 件がすべて文字情報
//    なし）のように理由の書かれていない「警告あり」だけが出る。

declare(strict_types=1);

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'homepage' . DIRECTORY_SEPARATOR . 'runtime.php';

$failures = [];

function registry_warning_summary(string $code, string $slug, string $name, array $primaryWarnings): array
{
    $feature = [
        'title' => $name . '議会 会議録 全文検索',
        'url' => '/gijiroku/?slug=' . $slug,
    ];
    return homepage_collect_visible_features(
        ['code' => $code, 'name' => $name, 'gijiroku' => $feature],
        $slug,
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
                    'primary' => [
                        'label' => '完了',
                        'class' => 'task-done',
                        'detail' => '',
                        'warning_lines' => $primaryWarnings,
                    ],
                    'publish' => [
                        'label' => '前回反映成功',
                        'class' => 'task-done',
                        'detail' => '',
                        'warning_lines' => [],
                    ],
                ],
                'has_data' => false,
                'search_indexed' => false,
            ],
        ],
        true
    );
}

// 直島町は台帳で取得対象外（not_published）。
$summary = registry_warning_summary('37364', '37364-naoshima-cho', '直島町', ['会議録本体ではない候補を除外 21件']);
$visible = $summary['visible_features'][0] ?? [];
if (($visible['status_label'] ?? '') !== '取得対象外') {
    $failures[] = '取得対象外の取得元が、残った警告で「' . ($visible['status_label'] ?? '(空)') . '」と出た';
}

// 初山別村は取得対象のまま。警告は理由つきで出す。
$line = '取得元の PDF 29件はすべて文字情報を持たず、本文を取り出せません';
$summary = registry_warning_summary('01485', '01485-shosanbetsu-mura', '初山別村', [$line]);
$visible = $summary['visible_features'][0] ?? [];
if (($visible['status_label'] ?? '') !== '警告あり') {
    $failures[] = '取得対象の警告が「警告あり」にならない: ' . ($visible['status_label'] ?? '(空)');
}
if (!in_array($line, $visible['display']['warning_lines'] ?? [], true)) {
    $failures[] = '「警告あり」の理由（取得の表示の警告）が表示に載っていない';
}

if ($failures !== []) {
    foreach ($failures as $failure) {
        fwrite(STDERR, "FAIL: {$failure}\n");
    }
    exit(1);
}
fwrite(STDOUT, "OK: registry decisions override stale warnings, and warnings keep their reasons\n");
