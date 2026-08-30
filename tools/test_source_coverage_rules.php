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

// 例規の feature に work_dir があり、Python の work_root と同じ場所を指すこと。
// 引数の数だけを見ても、キーが消えたことは分からない。実際にこのキーが
// 無くて、公開画面の判定が丸ごと空振りしていた。
require_once __DIR__ . '/../lib/municipalities.php';

$sampleSlug = '13101-chiyoda-ku';
$expectedWorkDir = work_path('reiki/' . $sampleSlug);

// 走査記録の読み取りは、本番の work を触らずに一時ディレクトリで確かめる。
// テストが実データに書き込むと、走っているスクレイパと競合する。
$tempWork = sys_get_temp_dir() . DIRECTORY_SEPARATOR . 'miyabe_coverage_test_' . getmypid();
@mkdir($tempWork, 0777, true);
$coveragePath = $tempWork . DIRECTORY_SEPARATOR . 'source_coverage.json';
file_put_contents($coveragePath, json_encode([
    'version' => 2, 'complete' => false, 'unresolved' => [['kind' => '条例']],
    'observed_at' => '20260830_120000',
]));

// feature を手で作ると配線を見ていないことになる。実際の生成経路から取る。
$entry = normalize_municipality_entry($sampleSlug, []);
$feature = is_array($entry['reiki'] ?? null) ? $entry['reiki'] : [];
if (trim((string)($feature['work_dir'] ?? '')) === '') {
    $failures[] = '例規: feature に work_dir が無い。走査記録が丸ごと読めない';
} elseif ($feature['work_dir'] !== $expectedWorkDir) {
    $failures[] = sprintf(
        "例規: work_dir が Python の work_root と違う
  期待 %s
  実際 %s",
        $expectedWorkDir,
        (string)$feature['work_dir']
    );
}
// 読み取り自体は一時ディレクトリで確かめる（本番の work は触らない）。
$status = homepage_reiki_acquisition_status(10, 10, ['work_dir' => $tempWork]);
if (($status['state'] ?? '') !== 'coverage_incomplete') {
    $failures[] = sprintf(
        "例規: feature の work_dir から走査記録を読めていない
  期待 coverage_incomplete / 実際 %s
  見た場所 %s",
        (string)($status['state'] ?? '(空)'),
        $coveragePath
    );
}

@unlink($coveragePath);
@rmdir($tempWork);

$total = count($rules['minutes'] ?? []) + count($rules['reiki'] ?? []);
if ($failures !== []) {
    fwrite(STDERR, "NG: Python と答えが違います\n\n" . implode("\n\n", $failures) . "\n");
    exit(1);
}

echo "OK: 走査記録の読み方が Python と一致 ({$total} 件)\n";
