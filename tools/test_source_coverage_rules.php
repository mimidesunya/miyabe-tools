<?php
// 走査記録の読み方を、Python と同じ入力・同じ期待値で確かめる。
//
// 同じ規則が Python 2 箇所・PHP 2 箇所・監査 1 箇所に散っている。会議録と
// 例規で形が違う（rule_version 対 version、state 対 complete、
// updated_at 対 observed_at）ので 1 関数には畳めない。畳む代わりに、
// 同じ入力に対して同じ答えを出すことをここで固定する。
//
// Python 側は tools/gijiroku/test_source_coverage.py の SharedRuleFixtureTest。

declare(strict_types=1);

require_once __DIR__ . '/../lib/homepage/runtime.php';

$fixturePath = __DIR__ . '/../tests/fixtures/source_coverage_rules.json';
$rules = json_decode((string)file_get_contents($fixturePath), true);
if (!is_array($rules)) {
    fwrite(STDERR, "NG: フィクスチャを読めません: {$fixturePath}\n");
    exit(1);
}

$failures = [];

foreach ($rules['minutes'] ?? [] as $case) {
    $actual = homepage_minutes_walk_state($case['payload'] ?? []);
    $expected = (string)($case['expect'] ?? '');
    if ($actual !== $expected) {
        $failures[] = sprintf(
            "会議録: %s\n  期待 %s / 実際 %s\n  入力 %s",
            (string)($case['why'] ?? ''),
            $expected,
            $actual,
            json_encode($case['payload'] ?? [], JSON_UNESCAPED_UNICODE)
        );
    }
}

foreach ($rules['reiki'] ?? [] as $case) {
    $actual = homepage_reiki_coverage_complete($case['payload'] ?? []);
    $expected = (bool)($case['expect'] ?? false);
    if ($actual !== $expected) {
        $failures[] = sprintf(
            "例規: %s\n  期待 %s / 実際 %s\n  入力 %s",
            (string)($case['why'] ?? ''),
            $expected ? 'true' : 'false',
            $actual ? 'true' : 'false',
            json_encode($case['payload'] ?? [], JSON_UNESCAPED_UNICODE)
        );
    }
}

// 例規の feature に work_dir がある（無いと走査記録が丸ごと読めない）。
// 実際にこのキーが無くて、公開画面の判定が空振りしていた。
require_once __DIR__ . '/../lib/municipalities.php';
$reflection = new ReflectionFunction('homepage_reiki_source_coverage');
if ($reflection->getNumberOfParameters() < 1) {
    $failures[] = '例規: homepage_reiki_source_coverage が feature を受け取らない';
}

$total = count($rules['minutes'] ?? []) + count($rules['reiki'] ?? []);
if ($failures !== []) {
    fwrite(STDERR, "NG: Python と答えが違います\n\n" . implode("\n\n", $failures) . "\n");
    exit(1);
}

echo "OK: 走査記録の読み方が Python と一致 ({$total} 件)\n";
