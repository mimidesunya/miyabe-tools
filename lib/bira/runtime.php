<?php
declare(strict_types=1);

// 政治活動ビラ（表面）の差し替えとPDF・単一HTML生成。
//
// 表面は全国共通で、差し替えるのは地域・紙名・号数・年月の4項目だけ。
// 指示書のとおりここにAIは使わない。単純な置換で足りる。
// 裏面は市ごとに事実が違うため、このファイルでは扱わない。

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'municipalities.php';

const BIRA_FIELDS = ['地域', '紙名', '号数', '年月'];

// 1項目あたりの上限。紙名が長いと版面からあふれるので、通る値だけ受ける。
const BIRA_FIELD_MAX_LENGTH = 20;

/**
 * このページを使ってよいアカウントか。
 *
 * LINEログインは掲示場マップと共用で、誰でもログインできてしまう。
 * URLを載せないだけでは、ログイン済みの人がパスを見つければ入れる。
 * そこで config.json の BIRA_LINE_IDS に挙げた党員と管理者だけに限る。
 * 既定は管理者のみ。空欄だから全員に開く、という作りにはしない。
 */
function bira_is_allowed(?array $user): bool
{
    if (!is_array($user) || !isset($user['id'])) {
        return false;
    }
    if (is_admin($user)) {
        return true;
    }
    $config = load_config();
    $allowed = $config['BIRA_LINE_IDS'] ?? [];
    return is_array($allowed) && in_array((string)$user['id'], $allowed, true);
}

function bira_dir(): string
{
    return __DIR__;
}

function bira_template_path(): string
{
    return bira_dir() . DIRECTORY_SEPARATOR . '表_テンプレート.html';
}

function bira_python_bin(): string
{
    // 既存機能（YouTube・検索）と同じ探し方にそろえる。
    $env = trim((string)getenv('MIYABE_PYTHON_BIN'));
    if ($env !== '') {
        return $env;
    }
    foreach (['/opt/miyabe-python/bin/python', '/usr/local/bin/python3', '/usr/bin/python3'] as $candidate) {
        if (is_file($candidate)) {
            return $candidate;
        }
    }
    return 'python3';
}

/**
 * 入力の検証。エラーは項目名 => メッセージ で返す。
 * 空欄と長すぎる値のほか、体裁を壊す制御文字とタグを弾く。
 */
function bira_validate(array $input): array
{
    $errors = [];
    foreach (BIRA_FIELDS as $field) {
        $value = trim((string)($input[$field] ?? ''));
        if ($value === '') {
            $errors[$field] = '入力してください。';
            continue;
        }
        if (mb_strlen($value, 'UTF-8') > BIRA_FIELD_MAX_LENGTH) {
            $errors[$field] = BIRA_FIELD_MAX_LENGTH . '文字以内にしてください。';
            continue;
        }
        if (preg_match('/[\x00-\x1F\x7F]/u', $value) === 1) {
            $errors[$field] = '使えない文字が含まれています。';
            continue;
        }
        if (str_contains($value, '<') || str_contains($value, '>')) {
            $errors[$field] = '< と > は使えません。';
        }
    }
    return $errors;
}

/**
 * テンプレートの {{項目}} を差し替える。
 * 値はHTMLとして解釈させない。テンプレート側に未知の項目が増えたら例外にする。
 */
function bira_render_html(array $input): string
{
    $template = @file_get_contents(bira_template_path());
    if (!is_string($template) || $template === '') {
        throw new RuntimeException('表面のテンプレートを読み込めませんでした。');
    }

    $replaced = preg_replace_callback(
        '/\{\{(\w+)\}\}/u',
        static function (array $matches) use ($input): string {
            $key = (string)$matches[1];
            if (!in_array($key, BIRA_FIELDS, true)) {
                throw new RuntimeException('テンプレートに未知の項目があります: ' . $key);
            }
            return htmlspecialchars(
                trim((string)($input[$key] ?? '')),
                ENT_QUOTES | ENT_SUBSTITUTE,
                'UTF-8'
            );
        },
        $template
    );
    if (!is_string($replaced)) {
        throw new RuntimeException('テンプレートの差し替えに失敗しました。');
    }
    return $replaced;
}

/** ローカル画像を data: URI に畳む。Copper 側に素材を置かなくて済む。 */
function bira_inline_images(string $html): string
{
    $root = bira_dir();
    $result = preg_replace_callback(
        '/(src=")([^"]+)(")/u',
        static function (array $matches) use ($root): string {
            $url = (string)$matches[2];
            if (preg_match('#^(data:|https?:)#i', $url) === 1) {
                return $matches[0];
            }
            $path = $root . DIRECTORY_SEPARATOR . str_replace('/', DIRECTORY_SEPARATOR, rawurldecode($url));
            $real = realpath($path);
            // 素材フォルダの外を読ませない。
            if ($real === false || !str_starts_with($real, $root . DIRECTORY_SEPARATOR) || !is_file($real)) {
                return $matches[0];
            }
            $mime = str_ends_with(strtolower($real), '.svg')
                ? 'image/svg+xml'
                : (string)(mime_content_type($real) ?: 'application/octet-stream');
            return $matches[1] . 'data:' . $mime . ';base64,' . base64_encode((string)file_get_contents($real)) . $matches[3];
        },
        $html
    );
    return is_string($result) ? $result : $html;
}

function bira_copper_config(): array
{
    $config = load_config();
    $copper = is_array($config['copper'] ?? null) ? $config['copper'] : [];
    $uri = trim((string)($copper['serverUri'] ?? ''));
    if ($uri === '') {
        throw new RuntimeException('Copper PDF の接続先が設定されていません（config.json の copper.serverUri）。');
    }
    return [
        'uri' => $uri,
        'user' => (string)($copper['user'] ?? ''),
        'password' => (string)($copper['password'] ?? ''),
    ];
}

/**
 * CTIP2 でPDFにする。$documents に複数渡すと continuous モードで1つのPDFに綴じる。
 * REST には綴じる手段がないので、こちらを使う。
 */
function bira_transcode_documents(array $documents): string
{
    require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'cti' . DIRECTORY_SEPARATOR . 'autoload.php';

    $documents = array_values(array_filter($documents, static fn($html) => trim((string)$html) !== ''));
    if ($documents === []) {
        throw new RuntimeException('変換する文書がありません。');
    }

    $copper = bira_copper_config();
    $pdf = '';
    $messages = [];

    try {
        $session = cti_get_session($copper['uri'], [
            'user' => $copper['user'],
            'password' => $copper['password'],
        ]);
    } catch (Throwable $error) {
        error_log('[bira] copper connect failed: ' . $error->getMessage());
        throw new RuntimeException('Copper PDF に接続できませんでした。');
    }

    try {
        $session->set_output_as_variable($pdf);
        $session->set_message_func(static function ($code, $message) use (&$messages): void {
            $messages[] = $code . ': ' . $message;
        });
        // 2文書以上をまとめるときだけ continuous にする。
        if (count($documents) > 1) {
            $session->set_continuous(true);
        }
        foreach ($documents as $html) {
            $session->start_main('.', ['mimeType' => 'text/html; charset=UTF-8']);
            echo $html;
            $session->end_main();
        }
        if (count($documents) > 1) {
            $session->join();
        }
    } finally {
        try {
            $session->close();
        } catch (Throwable $ignored) {
            error_log('[bira] failed to close copper session');
        }
    }

    if (substr($pdf, 0, 4) !== '%PDF') {
        error_log('[bira] transcode failed: ' . implode(' / ', $messages));
        throw new RuntimeException('PDFの生成に失敗しました。');
    }
    return $pdf;
}

function bira_transcode(string $html): string
{
    return bira_transcode_documents([$html]);
}

/** 差し替え済みHTMLからPDFを作る。 */
function bira_build_pdf(array $input): string
{
    return bira_transcode(bira_inline_images(bira_render_html($input)));
}

/**
 * 単一HTMLを作る。画像の埋め込みはPHP側で済ませ、Python にはフォントの
 * サブセット化だけ任せる。こうすると素材の相対参照が残らないので、
 * 一時ファイルをどこに置いてもよい（lib/ は rsync --delete の対象なので置かない）。
 */
function bira_build_single_html(array $input): string
{
    return bira_build_single_html_from_html(bira_inline_images(bira_render_html($input)));
}

/** 画像を埋め込み済みのHTMLから単一HTMLを作る。表面・裏面で共通。 */
function bira_build_single_html_from_html(string $html): string
{
    $html = bira_inline_images($html);
    $token = bin2hex(random_bytes(8));
    $tmpDir = sys_get_temp_dir();
    $src = $tmpDir . DIRECTORY_SEPARATOR . 'bira_' . $token . '.html';
    $dst = $tmpDir . DIRECTORY_SEPARATOR . 'bira_' . $token . '_single.html';

    if (@file_put_contents($src, $html) === false) {
        throw new RuntimeException('作業ファイルを作成できませんでした。');
    }
    try {
        $command = escapeshellcmd(bira_python_bin())
            . ' ' . escapeshellarg(bira_dir() . DIRECTORY_SEPARATOR . 'single_html.py')
            . ' ' . escapeshellarg($src)
            . ' ' . escapeshellarg($dst)
            . ' 2>&1';
        $output = [];
        $status = 0;
        exec($command, $output, $status);
        if ($status !== 0 || !is_file($dst)) {
            error_log('[bira] single_html.py failed: ' . implode("\n", $output));
            throw new RuntimeException('単一HTMLの生成に失敗しました。');
        }
        $result = (string)file_get_contents($dst);
        if ($result === '') {
            throw new RuntimeException('単一HTMLの生成に失敗しました。');
        }
        return $result;
    } finally {
        @unlink($src);
        @unlink($dst);
    }
}

/** ダウンロード名。記号を落として、地域名＋固定語にとどめる。 */
function bira_download_name(array $input, string $extension): string
{
    $region = trim((string)($input['地域'] ?? ''));
    $region = preg_replace('#[\\\\/:*?"<>|]#u', '', $region) ?? '';
    if ($region === '') {
        $region = 'ビラ';
    }
    return $region . '_表' . $extension;
}
