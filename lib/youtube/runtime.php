<?php
declare(strict_types=1);

/**
 * YouTube アップロードツール（管理者専用）のサーバー側ヘルパー。
 *
 * ジョブは work/youtube/jobs/<id>/ の下に置く（work は公開されない）。
 *   job.json    … アップロード指示（メタデータ・正規化設定・パス）
 *   source.mp4  … アップロードされた元動画
 *   status.json … ワーカーが書く進捗
 *   worker.log  … ワーカーのログ
 *
 * 認証は掲示板と同じ LINE 管理者判定（poster_boards_is_admin）を流用する。
 */

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'session.php';

const YOUTUBE_JOB_ID_PATTERN = '/^[0-9a-f]{32}$/';

function youtube_h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function youtube_jobs_root(): string
{
    return work_path('youtube/jobs');
}

function youtube_job_dir(string $jobId): string
{
    return youtube_jobs_root() . DIRECTORY_SEPARATOR . $jobId;
}

function youtube_token_path(): string
{
    $env = trim((string) getenv('YOUTUBE_TOKEN_PATH'));
    if ($env !== '') {
        return $env;
    }
    return data_path('youtube/oauth-token.json');
}

function youtube_is_configured(): bool
{
    return is_file(youtube_token_path());
}

function youtube_python_bin(): string
{
    $env = trim((string) getenv('MIYABE_PYTHON_BIN'));
    if ($env !== '') {
        return $env;
    }
    foreach (['/opt/miyabe-python/bin/python', '/usr/local/bin/python3', '/usr/bin/python3', 'python3'] as $candidate) {
        if (str_starts_with($candidate, '/')) {
            if (is_file($candidate)) {
                return $candidate;
            }
        } else {
            return $candidate;
        }
    }
    return 'python3';
}

function youtube_worker_script(): string
{
    return __DIR__ . DIRECTORY_SEPARATOR . 'worker.py';
}

function youtube_stream_script(): string
{
    return __DIR__ . DIRECTORY_SEPARATOR . 'youtube_stream.py';
}

/**
 * Python ヘルパーを同期実行し、最後の JSON 行をデコードして返す。
 * @param array<int,string> $args
 * @return array<string,mixed>|null
 */
function youtube_run_python_json(array $args): ?array
{
    $cmd = escapeshellarg(youtube_python_bin());
    foreach ($args as $arg) {
        $cmd .= ' ' . escapeshellarg((string) $arg);
    }
    $descriptors = [1 => ['pipe', 'w'], 2 => ['pipe', 'w']];
    $process = @proc_open($cmd, $descriptors, $pipes);
    if (!is_resource($process)) {
        return null;
    }
    $stdout = stream_get_contents($pipes[1]);
    fclose($pipes[1]);
    $stderr = stream_get_contents($pipes[2]);
    fclose($pipes[2]);
    $exit = proc_close($process);

    $line = '';
    foreach (preg_split('/\r?\n/', (string) $stdout) as $candidate) {
        $candidate = trim($candidate);
        if ($candidate !== '' && $candidate[0] === '{') {
            $line = $candidate;
        }
    }
    if ($line === '') {
        error_log('youtube python helper: no json output (exit=' . $exit . ') ' . substr((string) $stderr, 0, 400));
        return null;
    }
    $decoded = json_decode($line, true);
    return is_array($decoded) ? $decoded : null;
}

function youtube_new_job_id(): string
{
    return bin2hex(random_bytes(16));
}

function youtube_valid_job_id(string $jobId): bool
{
    return (bool) preg_match(YOUTUBE_JOB_ID_PATTERN, $jobId);
}

/**
 * 管理者セッションを要求する。API では JSON、ページでは 403 HTML を返して終了する。
 */
function youtube_require_admin(bool $json = false): array
{
    $user = poster_boards_current_user();
    if (!$user) {
        if ($json) {
            youtube_json_response(401, ['error' => 'ログインが必要です']);
        }
        poster_boards_require_login();
        exit;
    }
    if (!poster_boards_is_admin($user)) {
        if ($json) {
            youtube_json_response(403, ['error' => 'このツールは管理者専用です']);
        }
        http_response_code(403);
        header('Content-Type: text/html; charset=UTF-8');
        echo '<!DOCTYPE html><html lang="ja"><meta charset="UTF-8"><title>403 Forbidden</title>'
            . '<body><h1>403 Forbidden</h1><p>このツールは管理者専用です。</p>'
            . '<p><a href="/line/profile.php">アカウント情報へ戻る</a></p></body></html>';
        exit;
    }
    return $user;
}

function youtube_json_response(int $status, array $payload): void
{
    http_response_code($status);
    header('Content-Type: application/json; charset=UTF-8');
    header('Cache-Control: no-store');
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . "\n";
    exit;
}

function youtube_csrf_token(): string
{
    if (empty($_SESSION['youtube_csrf'])) {
        $_SESSION['youtube_csrf'] = bin2hex(random_bytes(16));
    }
    return (string) $_SESSION['youtube_csrf'];
}

function youtube_verify_csrf(?string $token): bool
{
    $expected = (string) ($_SESSION['youtube_csrf'] ?? '');
    return is_string($token) && $expected !== '' && hash_equals($expected, $token);
}

function youtube_read_status(string $jobId): ?array
{
    $path = youtube_job_dir($jobId) . DIRECTORY_SEPARATOR . 'status.json';
    if (!is_file($path)) {
        return null;
    }
    $decoded = json_decode((string) @file_get_contents($path), true);
    return is_array($decoded) ? $decoded : null;
}

function youtube_read_job(string $jobId): ?array
{
    $path = youtube_job_dir($jobId) . DIRECTORY_SEPARATOR . 'job.json';
    if (!is_file($path)) {
        return null;
    }
    $decoded = json_decode((string) @file_get_contents($path), true);
    return is_array($decoded) ? $decoded : null;
}

/**
 * 直近のジョブを新しい順に返す（表示用の要約のみ）。
 */
function youtube_list_jobs(int $limit = 20): array
{
    $root = youtube_jobs_root();
    if (!is_dir($root)) {
        return [];
    }
    $entries = [];
    foreach ((array) scandir($root) as $name) {
        if (!youtube_valid_job_id((string) $name)) {
            continue;
        }
        $dir = $root . DIRECTORY_SEPARATOR . $name;
        $job = youtube_read_job($name) ?? [];
        $status = youtube_read_status($name) ?? [];
        $entries[] = [
            'id' => $name,
            'title' => (string) ($job['title'] ?? ''),
            'privacy' => (string) ($job['privacy_status'] ?? ''),
            'state' => (string) ($status['state'] ?? 'unknown'),
            'progress' => (int) ($status['progress'] ?? 0),
            'message' => (string) ($status['message'] ?? ''),
            'video_id' => (string) ($status['video_id'] ?? ''),
            'watch_url' => (string) ($status['watch_url'] ?? ''),
            'created_at' => (int) ($job['created_at'] ?? @filemtime($dir) ?: 0),
        ];
    }
    usort($entries, static fn(array $a, array $b): int => $b['created_at'] <=> $a['created_at']);
    return array_slice($entries, 0, $limit);
}

/**
 * ワーカーを切り離しで起動する。PHP リクエストは待たずに戻る。
 */
function youtube_spawn_worker(string $jobId): void
{
    $jobDir = youtube_job_dir($jobId);
    $python = youtube_python_bin();
    $script = youtube_worker_script();
    $log = $jobDir . DIRECTORY_SEPARATOR . 'spawn.log';

    $cmd = 'nohup ' . escapeshellarg($python) . ' ' . escapeshellarg($script)
        . ' --job-dir ' . escapeshellarg($jobDir)
        . ' >> ' . escapeshellarg($log) . ' 2>&1 &';

    // & で切り離す。exec は完了を待たずに戻る。
    @exec($cmd);
}
