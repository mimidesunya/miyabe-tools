<?php
declare(strict_types=1);

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'municipalities.php';

// 台帳 TSV の先頭に BOM が付くと、以前は 1 列目の見出しが "\u{FEFF}jis_code" に
// なり、jis_code が 1 件も読めずトップページの件数が全部 0 になっていた。
// 落とす処理はあったが `'/^\xEF\xBB\xBF/u'` と書かれていて、単引用符では
// PCRE が U+00EF U+00BB U+00BF の 3 文字として読むため一致しなかった。

function assert_true(bool $condition, string $message): void
{
    if (!$condition) {
        fwrite(STDERR, "FAIL: {$message}\n");
        exit(1);
    }
}

$directory = sys_get_temp_dir() . DIRECTORY_SEPARATOR . 'miyabe-bom-test-' . getmypid();
if (!is_dir($directory) && !mkdir($directory, 0777, true) && !is_dir($directory)) {
    fwrite(STDERR, "FAIL: 一時ディレクトリを作れません\n");
    exit(1);
}

$header = "jis_code\turl\tsystem_type\n";
$row = "01100\thttps://example.jp/\tdbsr\n";

$withBom = $directory . DIRECTORY_SEPARATOR . 'with_bom.tsv';
file_put_contents($withBom, "\xEF\xBB\xBF" . $header . $row);
$withoutBom = $directory . DIRECTORY_SEPARATOR . 'without_bom.tsv';
file_put_contents($withoutBom, $header . $row);

foreach (['BOM あり' => $withBom, 'BOM なし' => $withoutBom] as $label => $path) {
    $rows = load_delimited_rows($path);
    assert_true(count($rows) === 1, "{$label}: 1 行読める");
    assert_true(
        ($rows[0]['jis_code'] ?? '') === '01100',
        "{$label}: jis_code を見出しから引ける"
    );
    assert_true(
        ($rows[0]['system_type'] ?? '') === 'dbsr',
        "{$label}: 2 列目以降も引ける"
    );
}

foreach ([$withBom, $withoutBom] as $path) {
    @unlink($path);
}
@rmdir($directory);

echo "OK: BOM 付きの台帳でも見出しを読める\n";
