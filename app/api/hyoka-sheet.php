<?php
declare(strict_types=1);

// 事務事業評価の詳細画面で、シートの全部を開いたときに 1 頁の中身（HTML の断片）を返す。
// 読むのは data/hyoka の控えだけ。

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'hyoka_display.php';

header('Content-Type: text/html; charset=utf-8');
header('X-Content-Type-Options: nosniff');

// 値が配列で来ても Warning を出さないよう、文字列だけを受ける。
$id = is_scalar($_GET['id'] ?? null) ? trim((string)$_GET['id']) : '';
$page = is_scalar($_GET['page'] ?? null) ? (int)$_GET['page'] : 0;
if ($id === '' || strlen($id) > 200 || $page < 0 || $page > 99999) {
    http_response_code(422);
    echo '<p class="detail-note">文書を指定してください。</p>';
    exit;
}

$sheet = miyabe_hyoka_display_sheet_page($id, $page);
if ($sheet === null) {
    http_response_code(404);
    echo '<p class="detail-note">この頁の控えが見つかりませんでした。</p>';
    exit;
}

$sourceUrl = miyabe_hyoka_display_document($id)['source_url'];
header('Cache-Control: public, max-age=300');
echo miyabe_hyoka_display_sheet_html($sheet, $sourceUrl);
