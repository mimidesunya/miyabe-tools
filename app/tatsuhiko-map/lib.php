<?php
declare(strict_types=1);

// 宮部たつひこマップ共通ヘルパー。位置情報の保存と管理ページの認証を担う。
// 認証は宮部たつひこ本人しか使わないため、ユーザー名なしのパスワードのみ。

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'municipalities.php';
require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'site_assets.php';

function tmap_h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function tmap_session_start(): void
{
    if (session_status() === PHP_SESSION_ACTIVE) {
        return;
    }
    session_start([
        'cookie_httponly' => true,
        'cookie_samesite' => 'Lax',
    ]);
}

function tmap_state_path(): string
{
    return data_path('tatsuhiko_map/state.json');
}

function tmap_login_failures_path(): string
{
    return data_path('tatsuhiko_map/login_failures.json');
}

function tmap_load_state(): array
{
    $state = read_json_cache_file(tmap_state_path(), 0) ?? [];
    return [
        'sharing' => (bool)($state['sharing'] ?? false),
        'lat' => is_numeric($state['lat'] ?? null) ? (float)$state['lat'] : null,
        'lng' => is_numeric($state['lng'] ?? null) ? (float)$state['lng'] : null,
        'accuracy' => is_numeric($state['accuracy'] ?? null) ? (float)$state['accuracy'] : null,
        'updated_at' => is_string($state['updated_at'] ?? null) ? (string)$state['updated_at'] : null,
    ];
}

function tmap_save_state(array $state): void
{
    write_json_cache_file(tmap_state_path(), $state);
}

// 位置提供が ON のときだけ座標を含める。OFF のときは保存済みの座標を一切出さない。
function tmap_public_payload(array $state): array
{
    if (!$state['sharing'] || $state['lat'] === null || $state['lng'] === null) {
        return ['sharing' => (bool)$state['sharing'], 'location' => null];
    }
    return [
        'sharing' => true,
        'location' => [
            'lat' => $state['lat'],
            'lng' => $state['lng'],
            'accuracy' => $state['accuracy'],
            'updated_at' => $state['updated_at'],
        ],
    ];
}

// パスワードハッシュは data/config.json の TATSUHIKO_MAP_PASSWORD_HASH か
// 同名の環境変数から読む。平文パスワードはどこにも置かない。
function tmap_password_hash(): string
{
    $config = load_config();
    $hash = trim((string)($config['TATSUHIKO_MAP_PASSWORD_HASH'] ?? ''));
    if ($hash === '') {
        $hash = trim((string)getenv('TATSUHIKO_MAP_PASSWORD_HASH'));
    }
    return $hash;
}

function tmap_is_admin(): bool
{
    tmap_session_start();
    return ($_SESSION['tatsuhiko_map_admin'] ?? false) === true;
}

function tmap_csrf_token(): string
{
    tmap_session_start();
    if (!is_string($_SESSION['tatsuhiko_map_csrf'] ?? null) || $_SESSION['tatsuhiko_map_csrf'] === '') {
        $_SESSION['tatsuhiko_map_csrf'] = bin2hex(random_bytes(16));
    }
    return $_SESSION['tatsuhiko_map_csrf'];
}

function tmap_verify_csrf(?string $token): bool
{
    tmap_session_start();
    $expected = $_SESSION['tatsuhiko_map_csrf'] ?? '';
    return is_string($token) && is_string($expected) && $expected !== ''
        && hash_equals($expected, $token);
}

const TMAP_LOGIN_WINDOW_SECONDS = 600;
const TMAP_LOGIN_MAX_FAILURES = 8;

function tmap_login_throttled(): bool
{
    $data = read_json_cache_file(tmap_login_failures_path(), 0) ?? [];
    $threshold = time() - TMAP_LOGIN_WINDOW_SECONDS;
    $recent = array_filter(
        is_array($data['times'] ?? null) ? $data['times'] : [],
        static fn($t) => is_numeric($t) && (int)$t > $threshold
    );
    return count($recent) >= TMAP_LOGIN_MAX_FAILURES;
}

function tmap_record_login_failure(): void
{
    $data = read_json_cache_file(tmap_login_failures_path(), 0) ?? [];
    $threshold = time() - TMAP_LOGIN_WINDOW_SECONDS;
    $recent = array_values(array_filter(
        is_array($data['times'] ?? null) ? $data['times'] : [],
        static fn($t) => is_numeric($t) && (int)$t > $threshold
    ));
    $recent[] = time();
    write_json_cache_file(tmap_login_failures_path(), ['times' => $recent]);
}

function tmap_clear_login_failures(): void
{
    @unlink(tmap_login_failures_path());
}

function tmap_attempt_login(string $password): bool
{
    $hash = tmap_password_hash();
    if ($hash === '' || tmap_login_throttled()) {
        return false;
    }
    if (!password_verify($password, $hash)) {
        tmap_record_login_failure();
        return false;
    }
    tmap_clear_login_failures();
    tmap_session_start();
    session_regenerate_id(true);
    $_SESSION['tatsuhiko_map_admin'] = true;
    return true;
}

function tmap_logout(): void
{
    tmap_session_start();
    unset($_SESSION['tatsuhiko_map_admin'], $_SESSION['tatsuhiko_map_csrf']);
    session_regenerate_id(true);
}
