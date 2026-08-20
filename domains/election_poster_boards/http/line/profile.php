<?php
// LINEログイン直後に表示するアカウント情報ページ
// ログインが必要。ここからポスター掲示場マップへ進む。

declare(strict_types=1);
require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'php' . DIRECTORY_SEPARATOR . 'runtime.php';
require_once dirname(__DIR__, 4) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'site_assets.php';

// HTML エスケープ用ヘルパー（users.php と同等）
function h(?string $s): string {
    return htmlspecialchars($s ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

$me = poster_boards_current_user();
if (!$me) {
    poster_boards_require_login();
    exit;
}

$slug = get_slug();
$requestSlug = $slug !== '' ? municipality_public_slug($slug) : '';
$municipality = $slug !== '' ? poster_boards_municipality_entry($slug) : null;
$municipalityName = (string)($municipality['name'] ?? '');
$feature = is_array($municipality['boards'] ?? null) ? $municipality['boards'] : [];
$mapUrl = (string)($feature['url'] ?? '');
$listUrl = (string)($feature['list_url'] ?? '');
$usersUrl = (string)($feature['users_url'] ?? '');
$mapEnabled = !empty($feature['enabled']) && !empty($feature['has_data']);

$isAdmin = poster_boards_is_admin($me);
$kmlUrl = $requestSlug !== '' ? '/boards/api/kml.php?slug=' . rawurlencode($requestSlug) : '';

// 管理者メニュー用: ユーザー一覧を開ける自治体だけ集める
$adminSwitcherItems = [];
if ($isAdmin) {
    foreach (poster_boards_municipality_switcher_items() as $item) {
        if (empty($item['enabled'])) {
            continue;
        }
        $itemUsersUrl = (string)($item['boards']['users_url'] ?? '');
        if ($itemUsersUrl === '') {
            continue;
        }
        $adminSwitcherItems[] = [
            'slug' => (string)$item['slug'],
            'name' => (string)$item['name'],
            'users_url' => $itemUsersUrl,
        ];
    }
}
$lineUserId = (string)($me['id'] ?? '');
$displayName = (string)($me['name'] ?? '');
$avatar = (string)($me['avatar'] ?? '');

// 共有ユーザーDBの登録情報（無くてもページは表示する）
$account = null;
$usersDbPath = poster_boards_users_db_path();
if (is_file($usersDbPath)) {
    try {
        $pdo = new PDO('sqlite:' . $usersDbPath, null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
        $stmt = $pdo->prepare('SELECT id, name, avatar, created_at, updated_at FROM users WHERE line_user_id = ?');
        $stmt->execute([$lineUserId]);
        $row = $stmt->fetch();
        if ($row) {
            $account = $row;
        }
    } catch (Throwable $exception) {
        error_log('アカウント情報の取得に失敗しました: ' . $exception->getMessage());
    }
}

if ($displayName === '' && $account) {
    $displayName = (string)($account['name'] ?? '');
}
if ($avatar === '' && $account) {
    $avatar = (string)($account['avatar'] ?? '');
}

$createdAt = $account ? app_format_tokyo_datetime((string)($account['created_at'] ?? ''), 'Y-m-d H:i') : '';
$updatedAt = $account ? app_format_tokyo_datetime((string)($account['updated_at'] ?? ''), 'Y-m-d H:i') : '';

?><!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="robots" content="noindex, nofollow" />
  <title>アカウント情報 - 選挙ポスター掲示場</title>
  <?php echo site_render_favicon_links(); ?>
  <style>
    :root { --bg:#f6f8fb; --card:#fff; --text:#222; --muted:#667788; --accent:#275ea3; --line:#06c755; }
    body { margin:0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans JP', 'Hiragino Kaku Gothic ProN', Meiryo, Arial, sans-serif; background: var(--bg); color: var(--text); }
    header { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:12px 16px; background:#fff; box-shadow:0 1px 4px rgba(0,0,0,0.06); }
    header .title-wrap { display:grid; gap:2px; }
    header .title { font-weight:700; }
    header .subtitle { font-size:12px; color: var(--muted); }
    header .links { display:flex; flex-wrap:wrap; align-items:center; gap:10px; }
    header .links a { color: var(--accent); text-decoration:none; }

    .container { max-width: 720px; margin: 18px auto; padding: 0 12px; }
    .card { background: var(--card); border-radius:10px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); padding:16px; margin-bottom:16px; }
    .identity { display:flex; gap:14px; align-items:center; }
    .avatar { width:64px; height:64px; border-radius:50%; background:#eee; flex:0 0 auto; overflow:hidden; display:flex; align-items:center; justify-content:center; font-size:24px; color:#999; }
    .avatar img { width:100%; height:100%; object-fit:cover; }
    .identity .name { font-weight:700; font-size:18px; }
    .badge { display:inline-block; margin-left:8px; padding:2px 8px; border-radius:999px; background:#eef4fb; color: var(--accent); font-size:12px; font-weight:600; vertical-align:middle; }
    .muted { color: var(--muted); font-size: 13px; }

    dl.rows { display:grid; grid-template-columns: max-content 1fr; gap:8px 16px; margin:16px 0 0 0; }
    dl.rows dt { color: var(--muted); font-size:13px; }
    dl.rows dd { margin:0; word-break:break-all; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size:14px; }
    @media (max-width:520px) { dl.rows { grid-template-columns: 1fr; gap:2px 0; } dl.rows dd { margin-bottom:8px; } }

    .actions { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-top:4px; }
    .btn { display:inline-block; padding:10px 18px; border-radius:8px; text-decoration:none; font-weight:700; }
    .btn.primary { background: var(--accent); color:#fff; }
    .btn.primary:hover { background:#1e40af; }
    .btn.ghost { background:#f2f5f9; color: var(--accent); }
    .btn.ghost:hover { background:#e6edf5; }
    .card.admin { border-left:4px solid var(--accent); }
    .card.admin h2 { margin:0 0 4px 0; font-size:16px; }
    .admin-item { padding:12px 0; border-top:1px solid #eef2f7; }
    .admin-item:first-of-type { border-top:0; }
    .admin-title { font-weight:700; font-size:14px; margin-bottom:2px; }
    .admin-item p.muted { margin:0 0 10px 0; }
    .admin-item .actions { margin-top:0; }
    .admin-item select { padding:8px 12px; border:1px solid #d1d5db; border-radius:8px; font-size:13px; background:#fff; }
    .logout { margin-top:8px; }
    .logout a { color: var(--muted); font-size:13px; }
  </style>
</head>
<body>
  <script>if(/Line\//i.test(navigator.userAgent)&&!location.search.includes('openExternalBrowser=1')){var u=new URL(location.href);u.searchParams.set('openExternalBrowser','1');location.replace(u.toString());}</script>
  <header>
    <div class="title-wrap">
      <div class="title">アカウント情報</div>
      <div class="subtitle">LINEでログイン中</div>
    </div>
    <div class="links">
      <a href="/">トップ</a>
      <?php if ($mapUrl !== ''): ?><a href="<?php echo h($mapUrl); ?>">マップ</a><?php endif; ?>
      <?php if ($listUrl !== ''): ?><a href="<?php echo h($listUrl); ?>">一覧</a><?php endif; ?>
    </div>
  </header>

  <div class="container">
    <div class="card">
      <div class="identity">
        <div class="avatar">
          <?php if ($avatar !== ''): ?>
            <img src="<?php echo h($avatar); ?>" alt="" referrerpolicy="no-referrer">
          <?php else: ?>
            <span aria-hidden="true">👤</span>
          <?php endif; ?>
        </div>
        <div>
          <div class="name">
            <?php echo h($displayName !== '' ? $displayName : '名前未設定'); ?>
            <?php if ($isAdmin): ?><span class="badge">管理者</span><?php endif; ?>
          </div>
          <div class="muted">ログイン方法: LINE</div>
        </div>
      </div>

      <dl class="rows">
        <dt>LINEユーザーID</dt>
        <dd><?php echo h($lineUserId); ?></dd>
        <?php if ($account): ?>
          <dt>ユーザー番号</dt>
          <dd><?php echo h((string)$account['id']); ?></dd>
          <dt>登録日時</dt>
          <dd><?php echo h($createdAt !== '' ? $createdAt : '-'); ?></dd>
          <dt>最終更新</dt>
          <dd><?php echo h($updatedAt !== '' ? $updatedAt : '-'); ?></dd>
        <?php endif; ?>
      </dl>
    </div>

    <div class="card">
      <div class="actions">
        <?php if ($mapUrl !== '' && $mapEnabled): ?>
          <a class="btn primary" href="<?php echo h($mapUrl); ?>">
            <?php echo h($municipalityName !== '' ? $municipalityName . 'のポスター掲示場マップへ' : 'ポスター掲示場マップへ'); ?>
          </a>
        <?php endif; ?>
        <?php if ($listUrl !== '' && $mapEnabled): ?>
          <a class="btn ghost" href="<?php echo h($listUrl); ?>">掲示場の一覧</a>
        <?php endif; ?>
      </div>
      <?php if (!$mapEnabled): ?>
        <p class="muted">この自治体のポスター掲示場データはまだ準備中です。<a href="/">トップ</a>から対象の自治体を選んでください。</p>
      <?php endif; ?>
      <p class="logout"><a href="/line/logout.php">ログアウト</a></p>
    </div>

    <?php if ($isAdmin): ?>
    <div class="card admin">
      <h2>管理者メニュー</h2>
      <p class="muted">このアカウントは管理者として登録されています。一般の利用者には表示されません。</p>

      <div class="admin-item">
        <div class="admin-title">ユーザー一覧・タスクの割り振り</div>
        <p class="muted">参加者ごとの作業件数を確認し、着手中のタスクを別の人へ一括で移せます。</p>
        <div class="actions">
          <?php if ($usersUrl !== ''): ?>
            <a class="btn primary" href="<?php echo h($usersUrl); ?>">
              <?php echo h($municipalityName !== '' ? $municipalityName . 'のユーザー一覧' : 'ユーザー一覧'); ?>
            </a>
          <?php endif; ?>
          <?php if (!empty($adminSwitcherItems)): ?>
            <select aria-label="ユーザー一覧の自治体切り替え" onchange="if (this.value) { window.location.href = this.value; }">
              <option value="">他の自治体のユーザー一覧…</option>
              <?php foreach ($adminSwitcherItems as $item): ?>
                <option value="<?php echo h($item['users_url']); ?>"<?php echo $item['slug'] === $slug ? ' selected' : ''; ?>>
                  <?php echo h($item['name']); ?>
                </option>
              <?php endforeach; ?>
            </select>
          <?php endif; ?>
        </div>
      </div>

      <div class="admin-item">
        <div class="admin-title">掲示場ラベルの位置調整</div>
        <p class="muted">マップ上でラベルをドラッグして位置を保存できます。管理者だけの権限で、通常のログインでは無効です。</p>
        <div class="actions">
          <?php if ($mapUrl !== '' && $mapEnabled): ?>
            <a class="btn ghost" href="<?php echo h($mapUrl); ?>">マップを開いて調整する</a>
          <?php endif; ?>
        </div>
      </div>

      <div class="admin-item">
        <div class="admin-title">YouTube 動画アップロード</div>
        <p class="muted">動画をアップロードすると、サーバー側で音声を正規化してから YouTube へ投稿します。既定は非公開です。</p>
        <div class="actions">
          <a class="btn ghost" href="/youtube/">アップロード画面を開く</a>
        </div>
      </div>

      <div class="admin-item">
        <div class="admin-title">掲示場データの書き出し</div>
        <p class="muted">現在の掲示場と作業状況をKMLで取得します（この書き出し自体は誰でも利用できます）。</p>
        <div class="actions">
          <?php if ($kmlUrl !== '' && $mapEnabled): ?>
            <a class="btn ghost" href="<?php echo h($kmlUrl); ?>">KMLをダウンロード</a>
          <?php endif; ?>
        </div>
      </div>
    </div>
    <?php endif; ?>
  </div>
</body>
</html>
