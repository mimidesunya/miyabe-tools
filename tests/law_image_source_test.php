<?php
declare(strict_types=1);

// 例規本文の相対の画像を、取得元の画像へ向けることを見る。
// `php tests/law_image_source_test.php` で走る。
//
// taikei・g-reiki は本文から `word/…` を相対で指し、画像を手元に持たない。相対のまま
// 返すと /reiki/word/… になり 404 が続いていた（6 時間で 278 件）。

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'reiki' . DIRECTORY_SEPARATOR . 'view_helpers.php';

$failures = 0;

function check(string $label, bool $ok): void
{
    global $failures;
    if (!$ok) {
        $failures++;
        fwrite(STDERR, "FAIL: {$label}\n");
        return;
    }
    echo "ok: {$label}\n";
}

$detail = 'https://example.test/reiki_int/reiki_honbun/q633RG00000001.html';

check('a directory-relative image resolves next to the document',
    resolve_relative_url($detail, 'word/q633IG00000026.jpg') === 'https://example.test/reiki_int/reiki_honbun/word/q633IG00000026.jpg');
check('dot segments are removed',
    resolve_relative_url($detail, './../reiki_honbun/./word/a.png') === 'https://example.test/reiki_int/reiki_honbun/word/a.png');
check('a root-relative path keeps only the origin',
    resolve_relative_url('https://example.test:8443/a/b/c.html', '/img/x.gif?v=1') === 'https://example.test:8443/img/x.gif?v=1');
check('too many parent segments stop at the root',
    resolve_relative_url($detail, '../../../../x.png') === 'https://example.test/x.png');
check('a scheme in the relative part is refused', resolve_relative_url($detail, 'data:image/png;base64,AAAA') === '');
check('a non-http base is refused', resolve_relative_url('file:///etc/passwd', 'word/a.jpg') === '');
check('an empty base is refused', resolve_relative_url('', 'word/a.jpg') === '');

check('word/ images need the source URL', law_html_has_external_relative_images('<p><img src="word/q633IG00000026.jpg"></p>'));
check('unquoted src is seen', law_html_has_external_relative_images('<img alt=x src=word/a.jpg>'));
check('images kept locally do not', !law_html_has_external_relative_images('<img src="../12345_images/a.png"><img src="a.png">'));
check('absolute images do not', !law_html_has_external_relative_images('<img src="https://example.test/a.png"><img src="/data/reiki/x/images/a.png">'));
check('the default download icon does not', !law_html_has_external_relative_images('<img src="word/download_default.gif">'));
check('data-src alone does not', !law_html_has_external_relative_images('<img data-src="word/a.jpg">'));

$html = '<div><img src="word/q633IG00000026.jpg"><img src="a.png"><img src="https://other.test/b.png"></div>';
$out = sanitize_law_html($html, '/data/reiki/45421-kadogawa-cho/images', $detail);
check('the relative image points at the source',
    str_contains($out, 'src="https://example.test/reiki_int/reiki_honbun/word/q633IG00000026.jpg"'));
check('the source image does not receive our URL', str_contains($out, 'referrerpolicy="no-referrer"'));
check('bare file names still go to the local image directory',
    str_contains($out, 'src="/data/reiki/45421-kadogawa-cho/images/a.png"'));
check('absolute images are left alone', str_contains($out, 'src="https://other.test/b.png"'));
$plain = sanitize_law_html($html, '/data/reiki/45421-kadogawa-cho/images');
check('without the source URL the image stays as it was', str_contains($plain, 'src="word/q633IG00000026.jpg"'));

$work = sys_get_temp_dir() . DIRECTORY_SEPARATOR . 'law_image_source_' . bin2hex(random_bytes(4));
mkdir($work, 0777, true);
file_put_contents($work . DIRECTORY_SEPARATOR . 'source_manifest.json.gz', gzencode(json_encode([
    ['source_file' => 'q633RG00000002_j.html', 'detail_url' => 'https://example.test/reiki_int/reiki_honbun/q633RG00000002.html'],
    ['source_file' => 'q633RG00000001_j.html', 'detail_url' => $detail],
    ['source_file' => 'q633RG00000003_j.html', 'detail_url' => 'javascript:alert(1)'],
], JSON_UNESCAPED_SLASHES)));
check('the detail URL is found by file name', reiki_source_document_url($work, 'q633RG00000001_j.html') === $detail);
check('an unknown file finds nothing', reiki_source_document_url($work, 'q633RG99999999_j.html') === '');
check('a non-http detail URL is not used', reiki_source_document_url($work, 'q633RG00000003_j.html') === '');
check('a missing manifest finds nothing', reiki_source_document_url($work . DIRECTORY_SEPARATOR . 'none', 'q633RG00000001_j.html') === '');
unlink($work . DIRECTORY_SEPARATOR . 'source_manifest.json.gz');
rmdir($work);

if ($failures > 0) {
    fwrite(STDERR, "{$failures} failure(s)\n");
    exit(1);
}
echo "all passed\n";
