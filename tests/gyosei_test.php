<?php
declare(strict_types=1);

// 行政文書の画面の部品（lib/gyosei.php）。`php tests/gyosei_test.php` で走る。

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'gyosei.php';

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

// 文字起こしの表示
$html = gyosei_render_ocr("### --右\n8川区田第441号\n令和8年10月29日\n### --\n\n# 開示請求承諾通知書\n\n| 名称 | 池上<br>文書 |\n| :--- | :--- |\n| 方法 | **電磁的記録** |\n\n<script>alert(1)</script>");
check('寄せの印は出さない', !str_contains($html, '###') && !str_contains($html, '--右'));
check('同じ段落の行は改行でつなぐ', str_contains($html, '<p>8川区田第441号<br>令和8年10月29日</p>'));
check('見出しは太字の段落', str_contains($html, '<p class="ocr-heading">開示請求承諾通知書</p>'));
check('表は表にし、区切りの行は出さない', substr_count($html, '<tr>') === 2 && !str_contains($html, ':---'));
check('表の中の <br> は改行に戻す', str_contains($html, '<td>池上<br>文書</td>'));
check('太字', str_contains($html, '<strong>電磁的記録</strong>'));
check('ほかの HTML はエスケープする', str_contains($html, '&lt;script&gt;') && !str_contains($html, '<script>'));

// 名前と日付
check('補足の 2 つ目からは括弧', gyosei_document_label(['kind' => '決定通知', 'detail' => '部分公開_田島支所']) === '部分公開（田島支所）');
check('補足が無ければ種類', gyosei_document_label(['kind' => '請求書', 'detail' => '']) === '請求書');
check('日付', gyosei_date_label('2026-10-29') === '2026年10月29日');
check('日の分からない日付', gyosei_date_label('2025-12-00') === '2025年12月' && gyosei_date_label('2025-00-00') === '2025年');
check('大きさ', gyosei_bytes_label(24472249) === '24.5MB' && gyosei_bytes_label(90727) === '91KB');

// 読み込み（id と文書の番号は決まった形だけ）
$dir = sys_get_temp_dir() . DIRECTORY_SEPARATOR . 'gyosei-test-' . getmypid();
mkdir($dir . DIRECTORY_SEPARATOR . 'a-1', 0777, true);
file_put_contents($dir . '/a-1/case.json', json_encode([
    'id' => 'a-1', 'agency' => '川崎市', 'title' => '池上', 'requested_on' => '2026-10-15',
    'documents' => [['file' => '01.pdf', 'text' => '01.json', 'kind' => '開示文書', 'detail' => '第1回', 'pages' => 2]],
]));
file_put_contents($dir . '/a-1/01.json', json_encode(['pages' => ['一頁', '']]));
putenv('MIYABE_GYOSEI_DIR=' . $dir);
$case = gyosei_case('a-1');
check('案件を読む', $case !== null && $case['agency'] === '川崎市');
check('決まった形でない id は読まない', gyosei_case('../a-1') === null && gyosei_case('A-1') === null);
check('一覧', count(gyosei_cases()) === 1);
$document = $case !== null ? gyosei_document($case, '01') : null;
check('文書を読む', $document !== null && gyosei_document_pages($case, $document) === ['一頁', '']);
check('無い文書・形の違う番号', $case !== null && gyosei_document($case, '02') === null && gyosei_document($case, '1') === null && gyosei_document($case, '../01') === null);
check('PDF の URL', $document !== null && gyosei_pdf_url($case, $document) === '/data/gyosei/a-1/01.pdf');
array_map('unlink', glob($dir . '/a-1/*') ?: []);
rmdir($dir . '/a-1');
rmdir($dir);

if ($failures > 0) {
    fwrite(STDERR, "{$failures} 件失敗\n");
    exit(1);
}
echo "すべて通過\n";
