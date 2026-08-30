<?php
declare(strict_types=1);

// ボタン1つで表裏そろったPDFを作り、別タブでそのまま表示する。
// 生成物はサーバーに残さない。保存しなければ、URLを推測されて拾われることも、
// 消し忘れて残ることもない。

require_once dirname(__DIR__, 3) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'session.php';
require_once dirname(__DIR__, 3) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'bira' . DIRECTORY_SEPARATOR . 'back.php';
require_once dirname(__DIR__, 3) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'bira' . DIRECTORY_SEPARATOR . 'quota.php';

$me = current_user();
if (!$me) {
    require_login();
    exit;
}
// ログインできる人と、このページを使える人は別。存在も伏せるため404を返す。
if (!bira_is_allowed($me)) {
    http_response_code(404);
    header('Content-Type: text/plain; charset=UTF-8');
    echo 'Not Found';
    exit;
}

/** 入力画面へ戻す。別タブで開いた先で失敗した場合もここへ戻る。 */
function bira_back_to_form(string $failure = '', string $notice = ''): never
{
    if ($failure !== '') {
        $_SESSION['bira_failure'] = $failure;
    }
    if ($notice !== '') {
        $_SESSION['bira_notice'] = $notice;
    }
    header('Location: /tools/bira/');
    exit;
}

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    bira_back_to_form();
}

// 他サイトのフォームから叩かれないようにする。
$token = (string)($_SESSION['bira_token'] ?? '');
if ($token === '' || !hash_equals($token, (string)($_POST['token'] ?? ''))) {
    bira_back_to_form('入力画面を開き直してから、もう一度お試しください。');
}

// 入力を丸ごと預かる。作り直しのたびに打ち直させない。
$input = [];
foreach (BIRA_FIELDS as $field) {
    $input[$field] = trim((string)($_POST[$field] ?? ''));
}
foreach (array_keys(BIRA_BACK_TEXT_FIELDS) as $field) {
    $input[$field] = trim((string)($_POST[$field] ?? ''));
}
$_SESSION['bira_input'] = $input;

$errors = array_merge(bira_validate($input), bira_back_validate($input));
if ($errors !== []) {
    $_SESSION['bira_errors'] = $errors;
    bira_back_to_form('入力を確認してください。');
}
if (empty($_POST['confirmed'])) {
    bira_back_to_form('発行者の確認にチェックを入れてください。');
}

$lineUserId = (string)($me['id'] ?? '');

// 裏面の本文をAIに書かせる。文章が前回と同じなら書き直す必要がないので、
// 作り置きを使って回数を消費しない。連絡先だけ直して作り直す場合に効く。
$aiInputs = [
    $input['地域'] ?? '',
    $input['大見出し'] ?? '',
    $input['主張したいこと'] ?? '',
    $input['根拠資料'] ?? '',
    $input['レイアウトの希望'] ?? '',
];
// 形式の版も混ぜる。作りを変えたときに古い作り置きが生き残らないようにする。
$aiInputs[] = 'v' . BIRA_BACK_FORMAT_VERSION;
$signature = hash('sha256', implode("\x1f", $aiInputs));
$content = '';
if (($_SESSION['bira_signature'] ?? '') === $signature) {
    $content = (string)($_SESSION['bira_content'] ?? '');
    // 形式が変わる前の作り置きが残っていることがある。使わない。
    if (!bira_back_is_full_page($content)) {
        $content = '';
    }
}

if (trim($content) === '') {
    if (!bira_quota_consume($lineUserId)) {
        bira_back_to_form('きょうのAI利用回数（' . BIRA_DAILY_LIMIT . '回）を使い切りました。日本時間0時に戻ります。');
    }
    try {
        $content = bira_back_generate_content($input);
    } catch (Throwable $error) {
        error_log('[bira] ' . $error->getMessage());
        bira_back_to_form($error->getMessage());
    }
    $_SESSION['bira_content'] = $content;
    $_SESSION['bira_signature'] = $signature;
}

try {
    $pdf = bira_both_build_pdf($input, $content);
} catch (Throwable $error) {
    error_log('[bira] ' . $error->getMessage());
    bira_back_to_form($error->getMessage());
}

$name = bira_download_name($input, '.pdf');

// 別タブでそのまま見せる。保存は閲覧側の操作に任せる。
header('Content-Type: application/pdf');
header(sprintf(
    "Content-Disposition: inline; filename=\"bira.pdf\"; filename*=UTF-8''%s",
    rawurlencode($name)
));
header('Content-Length: ' . strlen($pdf));
header('Cache-Control: private, no-store');
header('X-Robots-Tag: noindex, nofollow');
echo $pdf;
