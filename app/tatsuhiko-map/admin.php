<?php
declare(strict_types=1);

// 宮部たつひこマップ 管理ページ。本人専用のためユーザー名なしのパスワード認証。
// ログイン後は GPS による位置提供の ON/OFF と現在地の送信を行う。

require_once __DIR__ . DIRECTORY_SEPARATOR . 'lib.php';

tmap_session_start();

$loginError = '';
$passwordConfigured = tmap_password_hash() !== '';

if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'POST') {
    $formAction = is_string($_POST['form_action'] ?? null) ? (string)$_POST['form_action'] : '';
    if ($formAction === 'logout') {
        if (tmap_verify_csrf(is_string($_POST['csrf'] ?? null) ? (string)$_POST['csrf'] : null)) {
            tmap_logout();
        }
        header('Location: /tatsuhiko-map/admin.php');
        exit;
    }
    if ($formAction === 'login') {
        if (!tmap_verify_csrf(is_string($_POST['csrf'] ?? null) ? (string)$_POST['csrf'] : null)) {
            $loginError = '画面の有効期限が切れました。もう一度お試しください。';
        } elseif (!$passwordConfigured) {
            $loginError = 'パスワードが未設定のためログインできません。';
        } elseif (tmap_login_throttled()) {
            $loginError = '失敗が続いたため一時的にロックしています。しばらく待ってからお試しください。';
        } elseif (tmap_attempt_login((string)($_POST['password'] ?? ''))) {
            header('Location: /tatsuhiko-map/admin.php');
            exit;
        } else {
            $loginError = 'パスワードが違います。';
        }
    }
}

$isAdmin = tmap_is_admin();
$csrf = tmap_csrf_token();
$state = tmap_load_state();
?><!doctype html>
<html lang="ja">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="robots" content="noindex, nofollow">
    <title>管理｜宮部たつひこマップ</title>
    <?php echo site_render_favicon_links(); ?>
    <link rel="stylesheet" href="<?php echo tmap_h(site_asset_url('tatsuhiko-map/assets/style.css')); ?>">
</head>
<body>
<div class="tmap-shell tmap-shell-narrow">
    <header class="tmap-header">
        <?php echo site_render_brand('/'); ?>
        <nav aria-label="関連ページ">
            <a href="/tatsuhiko-map/">公開ページ</a>
        </nav>
    </header>

    <main>
<?php if (!$isAdmin): ?>
        <section class="tmap-panel">
            <h1>管理ログイン</h1>
            <p>宮部たつひこ本人専用のページです。パスワードを入力してください。</p>
<?php if (!$passwordConfigured): ?>
            <div class="tmap-alert">
                パスワードが未設定です。サーバーの <code>data/config.json</code> に
                <code>TATSUHIKO_MAP_PASSWORD_HASH</code>
                （<code>php -r "echo password_hash('パスワード', PASSWORD_DEFAULT);"</code> の出力）を設定してください。
            </div>
<?php endif; ?>
<?php if ($loginError !== ''): ?>
            <div class="tmap-alert" role="alert"><?php echo tmap_h($loginError); ?></div>
<?php endif; ?>
            <form method="post" action="/tatsuhiko-map/admin.php" class="tmap-login-form">
                <input type="hidden" name="form_action" value="login">
                <input type="hidden" name="csrf" value="<?php echo tmap_h($csrf); ?>">
                <label for="tmap-password">パスワード</label>
                <input id="tmap-password" type="password" name="password" required
                       autocomplete="current-password" <?php echo $passwordConfigured ? '' : 'disabled'; ?>>
                <button type="submit" <?php echo $passwordConfigured ? '' : 'disabled'; ?>>ログイン</button>
            </form>
        </section>
<?php else: ?>
        <section class="tmap-panel">
            <h1>位置提供の管理</h1>
            <p>
                ON にすると、このページを開いている間 GPS で現在地を取得し、
                公開ページの地図に表示します。OFF にすると座標は公開されません。
            </p>
            <div class="tmap-control-row">
                <span class="tmap-control-label">GPS による位置提供</span>
                <button type="button" class="tmap-toggle" data-tmap-toggle aria-pressed="false">…</button>
            </div>
            <dl class="tmap-status-list">
                <dt>公開状態</dt><dd data-tmap-sharing-text>確認中</dd>
                <dt>GPS</dt><dd data-tmap-gps-text>停止中</dd>
                <dt>最終送信</dt><dd data-tmap-last-sent>—</dd>
            </dl>
            <div class="tmap-alert" data-tmap-error hidden></div>
            <div class="tmap-actions">
                <button type="button" class="tmap-secondary" data-tmap-clear>保存済みの位置を消去</button>
                <a href="/tatsuhiko-map/" target="_blank" rel="noopener">公開ページを確認</a>
            </div>
        </section>
        <form method="post" action="/tatsuhiko-map/admin.php" class="tmap-logout-form">
            <input type="hidden" name="form_action" value="logout">
            <input type="hidden" name="csrf" value="<?php echo tmap_h($csrf); ?>">
            <button type="submit" class="tmap-secondary">ログアウト</button>
        </form>
        <script>
        window.TMAP_API_URL = '/tatsuhiko-map/api.php';
        window.TMAP_CSRF = <?php echo json_encode($csrf); ?>;
        window.TMAP_INITIAL_STATE = <?php echo json_encode($state, JSON_UNESCAPED_UNICODE); ?>;
        </script>
        <script src="<?php echo tmap_h(site_asset_url('tatsuhiko-map/assets/admin.js')); ?>" defer></script>
<?php endif; ?>
    </main>
</div>
</body>
</html>
