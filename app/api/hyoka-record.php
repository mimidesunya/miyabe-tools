<?php
declare(strict_types=1);

// 事務事業評価の詳細画面で、事業の多い文書の 1 件を開いたときに中身（HTML の断片）を返す。
// 中身は詳細画面と同じ関数で組み立てる。読むのは data/hyoka の控えだけ。

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'hyoka_display.php';

header('Content-Type: text/html; charset=utf-8');
header('X-Content-Type-Options: nosniff');

// 値が配列で来ても Warning を出さないよう、文字列だけを受ける。
$id = is_scalar($_GET['id'] ?? null) ? trim((string)$_GET['id']) : '';
$recordId = is_scalar($_GET['record'] ?? null) ? trim((string)$_GET['record']) : '';
if ($id === '' || $recordId === '' || strlen($id) > 200 || strlen($recordId) > 200) {
    http_response_code(422);
    echo '<p class="detail-note">事業を指定してください。</p>';
    exit;
}

$found = miyabe_hyoka_display_find_record($id, $recordId);
if ($found === null) {
    http_response_code(404);
    echo '<p class="detail-note">この事業の記録が見つかりませんでした。</p>';
    exit;
}

header('Cache-Control: public, max-age=300');
echo miyabe_hyoka_display_record_body_html($found['record'], $found['source_url']);
