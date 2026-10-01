#!/usr/bin/env php
<?php
declare(strict_types=1);

// 目録から消えた例規のファイルを _archive へ移すことを確かめる。
//
// g-reiki が 2026-08 に URL の形式を変えたとき（H… から …RG… へ）、旧形式の
// ファイルが html/ に残り、索引が同じ例規を新旧 2 件ずつ載せていた。

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

$root = sys_get_temp_dir() . DIRECTORY_SEPARATOR . 'taikei_orphan_' . bin2hex(random_bytes(4))
    . DIRECTORY_SEPARATOR . 'reiki' . DIRECTORY_SEPARATOR . '15100-niigata-shi';
$dirs = [];
foreach (['source', 'html', 'markdown'] as $name) {
    $dirs[$name] = $root . DIRECTORY_SEPARATOR . $name;
    mkdir($dirs[$name], 0777, true);
}
file_put_contents($dirs['source'] . '/e402RG00000001_j.html.gz', 'new');
file_put_contents($dirs['html'] . '/e402RG00000001_j.html', 'new');
file_put_contents($dirs['markdown'] . '/e402RG00000001_j.md', 'new');
file_put_contents($dirs['source'] . '/H504901010051.html.gz', 'old');
file_put_contents($dirs['html'] . '/H504901010051.html', 'old');
file_put_contents($dirs['markdown'] . '/H504901010051.md', 'old');

$manifests = [['source_file' => 'e402RG00000001_j.html', 'title' => '新潟市役所の位置を定める条例']];
$archived = archive_orphan_files($manifests, array_values($dirs));

check('three old-form files are moved', $archived === 3);
check('the listed document stays in html/', is_file($dirs['html'] . '/e402RG00000001_j.html'));
check('the listed document stays in source/', is_file($dirs['source'] . '/e402RG00000001_j.html.gz'));
check('the listed document stays in markdown/', is_file($dirs['markdown'] . '/e402RG00000001_j.md'));
check('the old form is gone from html/', !is_file($dirs['html'] . '/H504901010051.html'));
$copies = glob($root . '/_archive/*_orphan/html/H504901010051.html') ?: [];
check('the old form is kept under _archive', count($copies) === 1);
$batches = glob($root . '/_archive/*_orphan', GLOB_ONLYDIR) ?: [];
check('one run uses one archive folder', count($batches) === 1);
check('an empty manifest moves nothing', archive_orphan_files([], array_values($dirs)) === 0);
check('stems drop .gz and the document extension', orphan_file_stem('a_j.html.gz') === 'a_j' && orphan_file_stem('a_j.md') === 'a_j');

if ($failures > 0) {
    echo "{$failures} failure(s)\n";
    exit(1);
}
echo "all passed\n";
