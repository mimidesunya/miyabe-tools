<?php
declare(strict_types=1);

/**
 * YouTube アップロードツールの JSON / チャンクアップロード API（管理者専用）。
 *
 *   POST ?action=create   … メタデータでジョブを作る。{job_id, chunk_size} を返す
 *   POST ?action=chunk     … 生バイトを source.mp4 へ追記（?job=&offset=、ヘッダ X-YouTube-CSRF）
 *   POST ?action=finalize  … アップロード完了。ワーカーを起動する
 *   GET  ?action=status    … ジョブの進捗を返す（?job=）
 */

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib'
    . DIRECTORY_SEPARATOR . 'youtube' . DIRECTORY_SEPARATOR . 'runtime.php';

// 1本あたりの上限（バイト）。work は大容量ディスクだが、暴走防止に上限を設ける。
const YOUTUBE_MAX_SOURCE_BYTES = 5 * 1024 * 1024 * 1024; // 5 GiB
const YOUTUBE_CHUNK_SIZE = 8 * 1024 * 1024;              // 8 MiB

$action = (string) ($_GET['action'] ?? '');
$isJsonAction = in_array($action, ['create', 'finalize'], true);
$isStatus = $action === 'status';

$user = youtube_require_admin(true);

if ($isStatus) {
    if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'GET') {
        youtube_json_response(405, ['error' => 'GET only']);
    }
    $jobId = (string) ($_GET['job'] ?? '');
    if (!youtube_valid_job_id($jobId)) {
        youtube_json_response(400, ['error' => '不正なジョブIDです']);
    }
    $status = youtube_read_status($jobId);
    $job = youtube_read_job($jobId);
    if ($status === null && $job === null) {
        youtube_json_response(404, ['error' => 'ジョブが見つかりません']);
    }
    youtube_json_response(200, [
        'job_id' => $jobId,
        'title' => (string) ($job['title'] ?? ''),
        'status' => $status ?? ['state' => 'queued', 'progress' => 0, 'message' => '待機中…'],
    ]);
}

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    youtube_json_response(405, ['error' => 'POST only']);
}

if (!youtube_is_configured()) {
    youtube_json_response(500, ['error' => 'YouTube のトークンが未設定です（data/youtube/oauth-token.json）']);
}

// ─── create ───────────────────────────────────
if ($action === 'create') {
    $body = youtube_read_json_body();
    if (!youtube_verify_csrf((string) ($body['csrf'] ?? ''))) {
        youtube_json_response(403, ['error' => 'CSRF トークンが一致しません']);
    }

    $title = trim((string) ($body['title'] ?? ''));
    if ($title === '') {
        youtube_json_response(400, ['error' => 'タイトルを入力してください']);
    }
    if (mb_strlen($title) > 100) {
        youtube_json_response(400, ['error' => 'タイトルは100文字以内にしてください']);
    }

    $privacy = (string) ($body['privacy_status'] ?? 'private');
    if (!in_array($privacy, ['private', 'unlisted', 'public'], true)) {
        youtube_json_response(400, ['error' => '公開設定が不正です']);
    }

    $categoryId = (string) ($body['category_id'] ?? '22');
    if (!preg_match('/^\d{1,3}$/', $categoryId)) {
        youtube_json_response(400, ['error' => 'カテゴリIDが不正です']);
    }

    $tags = [];
    foreach ((array) ($body['tags'] ?? []) as $tag) {
        $tag = trim((string) $tag);
        if ($tag !== '') {
            $tags[] = $tag;
        }
    }

    $publishAt = null;
    $rawPublishAt = trim((string) ($body['publish_at'] ?? ''));
    if ($rawPublishAt !== '') {
        if ($privacy !== 'private') {
            youtube_json_response(400, ['error' => '公開予約は「限定公開」ではなく「非公開」で指定してください']);
        }
        $ts = strtotime($rawPublishAt);
        if ($ts === false) {
            youtube_json_response(400, ['error' => '公開予約日時が不正です']);
        }
        $publishAt = gmdate('Y-m-d\TH:i:s\Z', $ts);
    }

    $normalize = ($body['normalize'] ?? true) ? true : false;
    $size = (int) ($body['size'] ?? 0);
    if ($size < 0 || $size > YOUTUBE_MAX_SOURCE_BYTES) {
        youtube_json_response(413, ['error' => 'ファイルが大きすぎます（上限5GiB）']);
    }
    // 正規化OFFのときだけ中継ストリーム。ONは解析パスに全ファイルが要るためステージする。
    $mode = (!$normalize && $size > 0) ? 'stream' : 'staged';

    $jobId = youtube_new_job_id();
    $jobDir = youtube_job_dir($jobId);
    if (!@mkdir($jobDir, 0770, true) && !is_dir($jobDir)) {
        youtube_json_response(500, ['error' => 'ジョブ領域を作成できません']);
    }

    // 元動画到着前の下書きメタ。finalize で確定する。
    $meta = [
        'id' => $jobId,
        'title' => $title,
        'description' => (string) ($body['description'] ?? ''),
        'tags' => $tags,
        'category_id' => $categoryId,
        'privacy_status' => $privacy,
        'publish_at' => $publishAt,
        'made_for_kids' => youtube_optional_bool($body['made_for_kids'] ?? null),
        'synthetic_media' => youtube_optional_bool($body['synthetic_media'] ?? null),
        'notify_subscribers' => (bool) ($body['notify_subscribers'] ?? false),
        'normalize' => $normalize,
        'loudness' => youtube_clamp_number($body['loudness'] ?? -14, -70, -5, -14),
        'true_peak' => youtube_clamp_number($body['true_peak'] ?? -1, -9, 0, -1),
        'lra' => youtube_clamp_number($body['lra'] ?? 11, 1, 20, 11),
        'created_at' => time(),
        'created_by' => (string) ($user['id'] ?? ''),
        'source_path' => $jobDir . DIRECTORY_SEPARATOR . 'source.mp4',
        'source_size' => $size,
        'token_path' => youtube_token_path(),
        'mode' => $mode,
        'thumbnail_path' => null,
        'state' => 'receiving',
    ];
    youtube_write_job($jobId, $meta);
    youtube_write_status_file($jobId, ['state' => 'receiving', 'progress' => 0, 'message' => '動画を受信しています…']);

    if ($mode === 'stream') {
        // YouTube のレジューム対応セッションを開始し、session_uri を job.json へ書く。
        $init = youtube_run_python_json([youtube_stream_script(), 'init', '--job-dir', $jobDir]);
        if (!is_array($init) || empty($init['ok']) || empty($init['session_uri'])) {
            // 開始に失敗したらステージ方式へ退避（アップロードは継続できる）。
            $meta['mode'] = $mode = 'staged';
            youtube_write_job($jobId, $meta);
            youtube_write_status_file($jobId, ['state' => 'receiving', 'progress' => 0, 'message' => '動画を受信しています…']);
        }
    }

    youtube_json_response(200, [
        'job_id' => $jobId,
        'chunk_size' => YOUTUBE_CHUNK_SIZE,
        'max_bytes' => YOUTUBE_MAX_SOURCE_BYTES,
        'mode' => $mode,
    ]);
}

// ─── thumbnail（任意・両モード共通。動画作成後に設定する）──────
if ($action === 'thumbnail') {
    if (!youtube_verify_csrf((string) ($_SERVER['HTTP_X_YOUTUBE_CSRF'] ?? ''))) {
        youtube_json_response(403, ['error' => 'CSRF トークンが一致しません']);
    }
    $jobId = (string) ($_GET['job'] ?? '');
    if (!youtube_valid_job_id($jobId) || !is_dir(youtube_job_dir($jobId))) {
        youtube_json_response(404, ['error' => 'ジョブが見つかりません']);
    }
    $ext = strtolower((string) ($_GET['ext'] ?? 'jpg'));
    if (!in_array($ext, ['jpg', 'jpeg', 'png'], true)) {
        youtube_json_response(400, ['error' => 'サムネイルは jpg / png のみ']);
    }
    $data = (string) file_get_contents('php://input');
    if ($data === '' || strlen($data) > 2 * 1024 * 1024) {
        youtube_json_response(400, ['error' => 'サムネイルは1件・2MiB以内にしてください']);
    }
    $thumbPath = youtube_job_dir($jobId) . DIRECTORY_SEPARATOR . 'thumbnail.' . ($ext === 'jpeg' ? 'jpg' : $ext);
    if (@file_put_contents($thumbPath, $data, LOCK_EX) === false) {
        youtube_json_response(500, ['error' => 'サムネイルの保存に失敗しました']);
    }
    $job = youtube_read_job($jobId) ?? [];
    $job['thumbnail_path'] = $thumbPath;
    youtube_write_job($jobId, $job);
    youtube_json_response(200, ['ok' => true]);
}

// ─── chunk ────────────────────────────────────
if ($action === 'chunk') {
    if (!youtube_verify_csrf((string) ($_SERVER['HTTP_X_YOUTUBE_CSRF'] ?? ''))) {
        youtube_json_response(403, ['error' => 'CSRF トークンが一致しません']);
    }
    $jobId = (string) ($_GET['job'] ?? '');
    if (!youtube_valid_job_id($jobId) || !is_dir(youtube_job_dir($jobId))) {
        youtube_json_response(404, ['error' => 'ジョブが見つかりません']);
    }
    $offset = (int) ($_GET['offset'] ?? -1);
    if ($offset < 0) {
        youtube_json_response(400, ['error' => 'offset が不正です']);
    }

    $job = youtube_read_job($jobId) ?? [];

    // ── 中継ストリーム（正規化OFF）: ディスクに貯めず YouTube セッションへ PUT ──
    if (($job['mode'] ?? 'staged') === 'stream' && !empty($job['session_uri'])) {
        $data = (string) file_get_contents('php://input');
        if ($data === '') {
            youtube_json_response(400, ['error' => '空のチャンクです']);
        }
        $total = (int) ($job['source_size'] ?? 0);
        if ($total <= 0) {
            youtube_json_response(400, ['error' => 'サイズ未確定のストリームです']);
        }
        [$status, $respBody] = youtube_relay_chunk((string) $job['session_uri'], $data, $offset, $total);
        if ($status === 308) {
            $pct = $total > 0 ? (int) floor(($offset + strlen($data)) / $total * 100) : 0;
            youtube_write_status_file($jobId, ['state' => 'uploading', 'progress' => min(99, $pct), 'message' => "アップロード中… {$pct}%"]);
            youtube_json_response(200, ['received' => strlen($data), 'size' => $offset + strlen($data)]);
        }
        if ($status === 200 || $status === 201) {
            // 最終チャンク。応答に動画リソースが入る。
            $video = json_decode($respBody, true);
            $videoId = is_array($video) ? (string) ($video['id'] ?? '') : '';
            $job['video_id'] = $videoId;
            youtube_write_job($jobId, $job);
            youtube_write_status_file($jobId, ['state' => 'processing', 'progress' => 100, 'message' => 'アップロード完了。仕上げています…', 'video_id' => $videoId]);
            youtube_json_response(200, ['done' => true, 'video_id' => $videoId, 'size' => $offset + strlen($data)]);
        }
        error_log('youtube stream relay unexpected status=' . $status);
        youtube_write_status_file($jobId, ['state' => 'error', 'message' => 'アップロード中に失敗しました。', 'error' => "relay status={$status}"]);
        youtube_json_response(502, ['error' => 'YouTube への送信に失敗しました', 'status' => $status]);
    }

    // ── ステージ（正規化ON）: source.mp4 へ追記 ──
    $sourcePath = youtube_job_dir($jobId) . DIRECTORY_SEPARATOR . 'source.mp4';
    $currentSize = is_file($sourcePath) ? (int) filesize($sourcePath) : 0;
    if ($offset !== $currentSize) {
        // 順序ずれ。クライアントに現在位置を返して再送させる。
        youtube_json_response(409, ['error' => 'チャンク順序が不整合です', 'expected_offset' => $currentSize]);
    }

    $data = (string) file_get_contents('php://input');
    if ($data === '') {
        youtube_json_response(400, ['error' => '空のチャンクです']);
    }
    if ($currentSize + strlen($data) > YOUTUBE_MAX_SOURCE_BYTES) {
        youtube_json_response(413, ['error' => 'ファイルが大きすぎます（上限5GiB）']);
    }

    $handle = @fopen($sourcePath, 'ab');
    if ($handle === false) {
        youtube_json_response(500, ['error' => '書き込みに失敗しました']);
    }
    if (!flock($handle, LOCK_EX)) {
        fclose($handle);
        youtube_json_response(500, ['error' => 'ロックに失敗しました']);
    }
    $written = fwrite($handle, $data);
    fflush($handle);
    flock($handle, LOCK_UN);
    fclose($handle);
    if ($written === false) {
        youtube_json_response(500, ['error' => '書き込みに失敗しました']);
    }

    youtube_json_response(200, ['received' => $written, 'size' => $currentSize + $written]);
}

// ─── finalize ─────────────────────────────────
if ($action === 'finalize') {
    $body = youtube_read_json_body();
    if (!youtube_verify_csrf((string) ($body['csrf'] ?? ''))) {
        youtube_json_response(403, ['error' => 'CSRF トークンが一致しません']);
    }
    $jobId = (string) ($body['job'] ?? '');
    if (!youtube_valid_job_id($jobId)) {
        youtube_json_response(400, ['error' => '不正なジョブIDです']);
    }
    $job = youtube_read_job($jobId);
    if ($job === null) {
        youtube_json_response(404, ['error' => 'ジョブが見つかりません']);
    }

    // ── 中継ストリーム: 動画は最終チャンクで作成済み。サムネ設定と完了処理だけ ──
    if (($job['mode'] ?? 'staged') === 'stream') {
        $videoId = (string) ($job['video_id'] ?? '');
        if ($videoId === '') {
            youtube_json_response(400, ['error' => 'アップロードが完了していません']);
        }
        youtube_run_python_json([youtube_stream_script(), 'postprocess', '--job-dir', youtube_job_dir($jobId), '--video-id', $videoId]);
        youtube_json_response(200, ['ok' => true, 'job_id' => $jobId, 'video_id' => $videoId]);
    }

    // ── ステージ: 受信済みの source.mp4 を検証してワーカー起動 ──
    $sourcePath = (string) $job['source_path'];
    if (!is_file($sourcePath) || filesize($sourcePath) <= 0) {
        youtube_json_response(400, ['error' => '動画が受信できていません']);
    }
    $expectedSize = (int) ($body['size'] ?? 0);
    if ($expectedSize > 0 && (int) filesize($sourcePath) !== $expectedSize) {
        youtube_json_response(400, [
            'error' => '受信サイズが一致しません',
            'expected' => $expectedSize,
            'actual' => (int) filesize($sourcePath),
        ]);
    }

    $job['state'] = 'queued';
    youtube_write_job($jobId, $job);
    youtube_write_status_file($jobId, ['state' => 'queued', 'progress' => 0, 'message' => '処理を開始します…']);
    youtube_spawn_worker($jobId);

    youtube_json_response(200, ['ok' => true, 'job_id' => $jobId]);
}

youtube_json_response(400, ['error' => '不明なアクションです']);


// ─── helpers ──────────────────────────────────

/**
 * レジューム対応セッションURIへ1チャンクを PUT 中継する。
 * @return array{0:int,1:string} [HTTPステータス, レスポンス本文]
 */
function youtube_relay_chunk(string $sessionUri, string $data, int $offset, int $total): array
{
    $end = $offset + strlen($data) - 1;
    $ch = curl_init($sessionUri);
    curl_setopt_array($ch, [
        CURLOPT_CUSTOMREQUEST => 'PUT',
        CURLOPT_POSTFIELDS => $data,
        CURLOPT_HTTPHEADER => [
            'Content-Length: ' . strlen($data),
            'Content-Range: bytes ' . $offset . '-' . $end . '/' . $total,
        ],
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 110,
        CURLOPT_CONNECTTIMEOUT => 15,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
    ]);
    $body = curl_exec($ch);
    $status = (int) curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    if ($body === false) {
        error_log('youtube relay curl error: ' . curl_error($ch));
        $status = 0;
        $body = '';
    }
    curl_close($ch);
    return [$status, (string) $body];
}

function youtube_read_json_body(): array
{
    $raw = (string) file_get_contents('php://input');
    $decoded = json_decode($raw, true);
    return is_array($decoded) ? $decoded : [];
}

function youtube_optional_bool($value): ?bool
{
    if ($value === null || $value === '') {
        return null;
    }
    if (is_bool($value)) {
        return $value;
    }
    $normalized = strtolower(trim((string) $value));
    if (in_array($normalized, ['true', '1', 'yes'], true)) {
        return true;
    }
    if (in_array($normalized, ['false', '0', 'no'], true)) {
        return false;
    }
    return null;
}

function youtube_clamp_number($value, float $min, float $max, float $default): float
{
    if (!is_numeric($value)) {
        return $default;
    }
    return max($min, min($max, (float) $value));
}

function youtube_write_job(string $jobId, array $job): void
{
    $path = youtube_job_dir($jobId) . DIRECTORY_SEPARATOR . 'job.json';
    file_put_contents($path, json_encode($job, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES), LOCK_EX);
}

function youtube_write_status_file(string $jobId, array $status): void
{
    $status['updated_at'] = time();
    $path = youtube_job_dir($jobId) . DIRECTORY_SEPARATOR . 'status.json';
    file_put_contents($path, json_encode($status, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES), LOCK_EX);
}
