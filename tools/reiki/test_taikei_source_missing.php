#!/usr/bin/env php
<?php
declare(strict_types=1);

// 目録に載っているのに取得元が本文を返さない個票を、取り損ねと分けることを確かめる。
//
// 松茂町・生駒市は個票が 404、上富田町は空の応答で、毎回「直近の取得に失敗」と
// 出ていた。取得元が直すまで治らないので、失敗には数えない。

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

check('404 is missing at the source', taikei_source_side_missing(new RuntimeException('Failed to fetch x: HTTP 404', 404)));
check('410 is missing at the source', taikei_source_side_missing(new RuntimeException('Failed to fetch x: HTTP 410', 410)));
check('an empty body is missing at the source', taikei_source_side_missing(new TaikeiEmptySourceException('Received an empty ordinance response.')));
check('500 is a failure', !taikei_source_side_missing(new RuntimeException('Failed to fetch x: HTTP 500', 500)));
check('a network error is a failure', !taikei_source_side_missing(new RuntimeException('Failed to fetch x: timeout')));

$threw = false;
try {
    taikei_source_changed('', "  \n");
} catch (TaikeiEmptySourceException) {
    $threw = true;
}
check('an empty response raises the dedicated exception', $threw);

$record = ['detail_url' => 'https://example.test/reiki_honbun/k541RG00000234.html', 'title' => '上富田町手数料条例'];
$previous = ['source_file' => 'k541RG00000234.html', 'title' => '上富田町手数料条例', 'source_sha256' => 'abc'];
$kept = taikei_keep_previous_entry(
    new TaikeiEmptySourceException('Received an empty ordinance response.'),
    $record,
    $previous,
    '/data/reiki/30404-kamitonda-cho/source/k541RG00000234.html.gz',
    'k541RG00000234.html',
    '令和8年9月1日',
    true
);
check('the saved text is kept in the manifest', is_array($kept) && ($kept['source_sha256'] ?? '') === 'abc');
check('the kept entry is marked', is_array($kept) && isset($kept['source_missing_at']));
check('the stored file name is the saved one', is_array($kept) && ($kept['stored_source_file'] ?? '') === 'k541RG00000234.html.gz');
$none = taikei_keep_previous_entry(new RuntimeException('HTTP 404', 404), $record, null, null, 'k541RG00000234.html', '', false);
check('without a saved text nothing is kept', $none === null);

// 与那国町はメニューのリンクが令和3年の複製を指していた（前回は令和6年の版）。
check('an old copy of the catalog is a regression', taikei_catalog_regressed('令和3年3月31日', '令和6年9月30日'));
check('a newer catalog is not', !taikei_catalog_regressed('令和6年9月30日', '令和3年3月31日'));
check('a corrected date within weeks is not', !taikei_catalog_regressed('令和8年8月1日', '令和8年9月1日'));
check('an unreadable date is not judged', !taikei_catalog_regressed('', '令和6年9月30日') && !taikei_catalog_regressed('令和3年3月31日', ''));
check('the era change is handled', taikei_catalog_regressed('平成30年3月31日', '令和元年12月1日'));

if ($failures > 0) {
    echo "{$failures} failure(s)\n";
    exit(1);
}
echo "all passed\n";
