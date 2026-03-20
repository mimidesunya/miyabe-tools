<?php
// 掲示板ツールのユーザーダッシュボードとタスクの一括割り振り
// ログインと管理者権限（特定のLINEユーザーID）が必要です

declare(strict_types=1);
require '/var/www/lib/session.php';

// HTML エスケープ用ヘルパー（list.php と同等）
function h(?string $s): string {
  return htmlspecialchars($s ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function bulk_reassign_in_progress_tasks(PDO $pdo, int $fromUserId, int $toUserId): array {
    $pdo->beginTransaction();
    try {
        // 元のユーザーから現在の「着手中」タスクを取得
        $stmt = $pdo->prepare('SELECT board_code FROM task_status WHERE status = "in_progress" AND updated_by = ?');
        $stmt->execute([$fromUserId]);
        $tasks = $stmt->fetchAll(PDO::FETCH_COLUMN);
        
        if (empty($tasks)) {
            $pdo->commit();
            return ['reassigned_count' => 0, 'tasks' => []];
        }

        // すべての「着手中」タスクを新しいユーザーに更新
        $placeholders = str_repeat('?,', count($tasks) - 1) . '?';
        $stmt = $pdo->prepare("UPDATE task_status SET updated_by = ?, updated_at = CURRENT_TIMESTAMP WHERE status = 'in_progress' AND board_code IN ($placeholders)");
        $stmt->execute(array_merge([$toUserId], $tasks));
        
        $reassignedCount = $stmt->rowCount();
        $pdo->commit();
        
        return ['reassigned_count' => $reassignedCount, 'tasks' => $tasks];
    } catch (Throwable $e) {
        if ($pdo->inTransaction()) $pdo->rollBack();
        throw $e;
    }
}

if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['action']) && $_POST['action'] === 'bulk_reassign') {
    header('Content-Type: application/json; charset=UTF-8');
    
    $me = current_user();
    if (!$me) {
        http_response_code(401);
        echo json_encode(['error' => 'ログインが必要です']);
        exit;
    }
    
    if (!is_admin($me)) {
        http_response_code(403);
        echo json_encode(['error' => 'このページへのアクセス権限がありません']);
        exit;
    }
    
    $toUserId = (int)($_POST['to_user_id'] ?? 0);
    if ($toUserId <= 0) {
        http_response_code(400);
        echo json_encode(['error' => '割り振り先のユーザーを選択してください']);
        exit;
    }
    
    $slug = get_slug($_POST['slug'] ?? null);
    if ($slug === '') {
        http_response_code(400);
        echo json_encode(['error' => '自治体(slug)が正しく指定されていません']);
        exit;
    }

    $pdo = open_tasks_pdo($slug);
    
    try {
        $fromUserId = upsert_user($pdo, $me);
        $result = bulk_reassign_in_progress_tasks($pdo, $fromUserId, $toUserId);
        
        // ターゲットユーザー名を取得
        $stmt = $pdo->prepare('SELECT name FROM users.users WHERE id = ?');
        $stmt->execute([$toUserId]);
        $toUser = $stmt->fetch();
        $toUserName = $toUser ? $toUser['name'] : 'ユーザー';
        
        echo json_encode([
            'success' => true,
            'message' => "{$result['reassigned_count']} 件の着手中タスクを {$toUserName} に割り振りました",
            'reassigned_count' => $result['reassigned_count'],
            'tasks' => $result['tasks']
        ]);
    } catch (Throwable $e) {
        http_response_code(500);
        echo json_encode(['error' => '一括割り振りに失敗しました: ' . $e->getMessage()]);
    }
    exit;
}

// tasks.sqlite を開く
$slug = get_slug();
if ($slug === '') {
    die('自治体(slug)が正しく指定されていません。');
}

$municipality = municipality_entry($slug);
$municipalityName = (string)($municipality['name'] ?? $slug);
$switcherItems = municipality_switcher_items('boards');
$pdo = open_tasks_pdo($slug);
$me = current_user();
$users = [];
if ($pdo) {
    // クエリパラメータによるオプションのソート
    $sort = $_GET['sort'] ?? 'done';
    $allowed = ['done','in_progress','issue','boards','name','last'];
    if (!in_array($sort, $allowed, true)) $sort = 'done';
    $order = [
        'done' => 'done_count DESC, in_progress_count DESC, issue_count DESC, name COLLATE NOCASE ASC',
        'in_progress' => 'in_progress_count DESC, done_count DESC, issue_count DESC, name COLLATE NOCASE ASC',
        'issue' => 'issue_count DESC, done_count DESC, in_progress_count DESC, name COLLATE NOCASE ASC',
        'boards' => 'boards_updated DESC, done_count DESC, name COLLATE NOCASE ASC',
        'name' => 'name COLLATE NOCASE ASC',
        'last' => 'last_activity DESC NULLS LAST, name COLLATE NOCASE ASC',
    ][$sort] ?? 'done_count DESC, name COLLATE NOCASE ASC';

    // 最終アクティビティとコメントの相関サブクエリを含むクエリを構築
    $sql = "
        SELECT
          u.id,
          COALESCE(u.name, '') AS name,
          COALESCE(u.avatar, '') AS avatar,
          COALESCE(u.line_user_id, '') AS line_user_id,
          -- このユーザーが最後に更新した task_status からのカウント
          COALESCE(SUM(CASE WHEN ts.status = 'in_progress' THEN 1 ELSE 0 END), 0) AS in_progress_count,
          COALESCE(SUM(CASE WHEN ts.status = 'done' THEN 1 ELSE 0 END), 0) AS done_count,
          COALESCE(SUM(CASE WHEN ts.status = 'issue' THEN 1 ELSE 0 END), 0) AS issue_count,
          COALESCE(COUNT(DISTINCT ts.board_code), 0) AS boards_updated,
          -- 最新のタイムスタンプ
          (
            SELECT MAX(x.updated_at)
            FROM task_status x
            WHERE x.updated_by = u.id
          ) AS last_ts,
          (
            SELECT MAX(y.created_at)
            FROM status_history y
            WHERE y.user_id = u.id
          ) AS last_hist,
          -- コメント数
          (
            SELECT COUNT(1)
            FROM status_history c
            WHERE c.user_id = u.id AND c.note IS NOT NULL AND TRIM(c.note) <> ''
          ) AS comments_count
        FROM users.users u
        LEFT JOIN task_status ts ON ts.updated_by = u.id
        GROUP BY u.id, u.name, u.avatar, u.line_user_id
    ";
    $stmt = $pdo->query($sql);
    $rows = $stmt ? $stmt->fetchAll() : [];
    foreach ($rows as $r) {
        $last_ts = $r['last_ts'] ?? null;
        $last_hist = $r['last_hist'] ?? null;
        $last = $last_ts;
        if ($last_hist && (!$last || strcmp((string)$last_hist, (string)$last) > 0)) {
            $last = $last_hist;
        }
        $users[] = [
            'id' => (int)$r['id'],
            'name' => (string)$r['name'],
            'avatar' => (string)$r['avatar'],
            'line_user_id' => (string)$r['line_user_id'],
            'in_progress_count' => (int)$r['in_progress_count'],
            'done_count' => (int)$r['done_count'],
            'issue_count' => (int)$r['issue_count'],
            'boards_updated' => (int)$r['boards_updated'],
            'comments_count' => (int)$r['comments_count'],
            'last_activity' => $last ? (string)$last : null,
        ];
    }
    // 古いバージョンの SQLite で NULLS LAST を尊重するために PHP でソート
    usort($users, function($a, $b) use ($sort) {
        $keyOrder = [
            'done' => ['done_count','in_progress_count','issue_count','name'],
            'in_progress' => ['in_progress_count','done_count','issue_count','name'],
            'issue' => ['issue_count','done_count','in_progress_count','name'],
            'boards' => ['boards_updated','done_count','name'],
            'name' => ['name'],
            'last' => ['last_activity','name'],
        ][$sort] ?? ['done_count','name'];
        foreach ($keyOrder as $k) {
            $av = $a[$k] ?? null; $bv = $b[$k] ?? null;
            if ($k === 'name') {
                $cmp = strcasecmp((string)$av, (string)$bv);
            } elseif ($k === 'last_activity') {
                if ($av === $bv) { $cmp = 0; }
                else if ($av === null) { $cmp = 1; } // nulls last
                else if ($bv === null) { $cmp = -1; }
                else { $cmp = strcmp((string)$bv, (string)$av); } // desc
            } else {
                // 数値の降順
                $cmp = (int)$bv <=> (int)$av;
            }
            if ($cmp !== 0) return $cmp;
        }
        return 0;
    });
}

// アクセス制御: ログイン必須 & 管理者のみ
if (!$me) {
    require_login();
    exit;
}
if (!is_admin($me)) {
    http_response_code(403);
    header('Content-Type: text/html; charset=UTF-8');
    echo '<!DOCTYPE html><html lang="ja"><meta charset="UTF-8"><title>403 Forbidden</title><body><h1>403 Forbidden</h1><p>このページへのアクセス権限がありません。</p></body></html>';
    exit;
}

?><!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ユーザー一覧 - <?php echo h($municipalityName); ?> 掲示場タスク</title>
  <style>
    :root { --bg:#f6f8fb; --card:#fff; --text:#222; --muted:#667788; --accent:#275ea3; --success:#10b981; --error:#ef4444; --warning:#f59e0b; }
    body { margin:0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans JP', 'Hiragino Kaku Gothic ProN', Meiryo, Arial, sans-serif; background: var(--bg); color: var(--text); }
    header { display:flex; align-items:center; justify-content:space-between; padding:12px 16px; background:#fff; box-shadow:0 1px 4px rgba(0,0,0,0.06); }
    header .title-wrap { display:grid; gap:2px; }
    header .title { font-weight:700; }
    header .subtitle { font-size:12px; color: var(--muted); }
    header .links { display:flex; flex-wrap:wrap; align-items:center; gap:10px; }
    header .links a { color: var(--accent); text-decoration:none; }
    header .links select { padding: 6px 10px; border:1px solid #d1d5db; border-radius:999px; font-size:13px; }

    .container { max-width: 1080px; margin: 18px auto; padding: 0 12px; }
    .toolbar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:12px; color: var(--muted); }
    .toolbar a { color: var(--accent); text-decoration: none; }

    .bulk-reassign { background: var(--card); border-radius:10px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); padding:16px; margin-bottom:16px; }
    .bulk-reassign h3 { margin: 0 0 12px 0; font-size: 16px; }
    .bulk-reassign .form-row { display: flex; gap: 12px; align-items: end; flex-wrap: wrap; }
    .bulk-reassign .form-group { display: flex; flex-direction: column; gap: 4px; }
    .bulk-reassign label { font-size: 14px; color: var(--muted); font-weight: 500; }
    .bulk-reassign select { padding: 8px 12px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; min-width: 150px; }
    .bulk-reassign button { padding: 8px 16px; background: var(--accent); color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: 500; }
    .bulk-reassign button:hover { background: #1e40af; }
    .bulk-reassign button:disabled { background: #9ca3af; cursor: not-allowed; }
    .bulk-reassign .status-message { margin-top: 12px; padding: 8px 12px; border-radius: 6px; font-size: 14px; }
    .bulk-reassign .status-message.success { background: #ecfdf5; color: var(--success); border: 1px solid #a7f3d0; }
    .bulk-reassign .status-message.error { background: #fef2f2; color: var(--error); border: 1px solid #fca5a5; }

    .grid { display:grid; grid-template-columns: repeat(1, minmax(0,1fr)); gap:12px; }
    @media(min-width:640px){ .grid{ grid-template-columns: repeat(2, minmax(0,1fr)); } }
    @media(min-width:960px){ .grid{ grid-template-columns: repeat(3, minmax(0,1fr)); } }

    .card { background: var(--card); border-radius:10px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); padding:12px; display:flex; gap:12px; }
    .avatar { width:56px; height:56px; border-radius:50%; background:#eee; flex:0 0 auto; overflow:hidden; display:flex; align-items:center; justify-content:center; font-size:22px; color:#999; }
    .avatar img { width:100%; height:100%; object-fit:cover; }
    .card .name { font-weight:700; }
    .muted { color: var(--muted); font-size: 13px; }
    .stats { display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap:6px; margin-top:8px; }
    .stat { background:#f7fafc; border:1px solid #e6edf5; border-radius:8px; padding:6px 8px; text-align:center; }
    .stat .label { font-size:12px; color:#53657a; }
    .stat .value { font-weight:700; margin-top:2px; }
    .meta { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-top:8px; font-size:13px; color:#53657a; }
  </style>
</head>
<body>
  <script>if(/Line\//i.test(navigator.userAgent)&&!location.search.includes('openExternalBrowser=1')){var u=new URL(location.href);u.searchParams.set('openExternalBrowser','1');location.replace(u.toString());}</script>
  <header>
    <div class="title-wrap">
      <div class="title">ユーザー一覧</div>
      <div class="subtitle"><?php echo h($municipalityName); ?></div>
    </div>
    <div class="links">
      <a href="/">トップ</a>
      <a href="/boards/<?php echo h($slug); ?>/">マップ</a>
      <a href="/boards/list.php?slug=<?php echo h($slug); ?>">一覧</a>
      <select aria-label="自治体切り替え" onchange="if (this.value) { window.location.href = this.value; }">
        <?php foreach ($switcherItems as $item): ?>
          <?php $switchMunicipality = municipality_entry((string)$item['slug']); ?>
          <?php $switchUrl = (string)($switchMunicipality['boards']['users_url'] ?? ''); ?>
          <?php if (!$item['enabled']): ?>
            <option value="" disabled><?php echo h($item['name']); ?> (準備中)</option>
          <?php else: ?>
            <option value="<?php echo h($switchUrl); ?>" <?php echo $item['slug'] === $slug ? 'selected' : ''; ?>>
              <?php echo h($item['name']); ?>
            </option>
          <?php endif; ?>
        <?php endforeach; ?>
      </select>
    </div>
  </header>
  <div class="container">
    <div class="toolbar">
      <div>並び替え:</div>
      <a href="?slug=<?php echo h($slug); ?>&sort=done">掲示が多い順</a>
      <a href="?slug=<?php echo h($slug); ?>&sort=in_progress">着手が多い順</a>
      <a href="?slug=<?php echo h($slug); ?>&sort=issue">異常が多い順</a>
      <a href="?slug=<?php echo h($slug); ?>&sort=boards">更新地点が多い順</a>
      <a href="?slug=<?php echo h($slug); ?>&sort=last">最終更新が新しい順</a>
      <a href="?slug=<?php echo h($slug); ?>&sort=name">名前順</a>
      <div style="margin-left:auto;">
        <?php if ($me) { echo '<span class="muted">ログイン中: ' . h($me['name'] ?? '') . '</span>'; } else { echo '<a href="/line/login.php?slug=' . h($slug) . '">LINEでログイン</a>'; } ?>
      </div>
    </div>

    <?php if (is_admin($me) && !empty($users)): ?>
    <div class="bulk-reassign">
      <h3>📋 一括割り振り</h3>
      <p class="muted" style="margin: 0 0 12px 0;">自分が着手中の掲示板を他のユーザーに一括で割り振ることができます。</p>
      <form id="bulkReassignForm" onsubmit="handleBulkReassign(event)">
        <input type="hidden" name="slug" value="<?php echo h($slug); ?>">
        <div class="form-row">
          <div class="form-group">
            <label for="toUserId">割り振り先ユーザー</label>
            <select id="toUserId" name="to_user_id" required>
              <option value="">選択してください</option>
              <?php foreach ($users as $u): ?>
                <?php if ($u['line_user_id'] !== ($me['id'] ?? '')): ?>
                <option value="<?php echo (int)$u['id']; ?>">
                  <?php echo h($u['name'] ?: '（名称未設定）'); ?>
                  (着手: <?php echo (int)$u['in_progress_count']; ?>件)
                </option>
                <?php endif; ?>
              <?php endforeach; ?>
            </select>
          </div>
          <div class="form-group">
            <button type="submit" id="reassignBtn">一括割り振り実行</button>
          </div>
        </div>
        <div id="statusMessage" class="status-message" style="display: none;"></div>
      </form>
    </div>
    <?php endif; ?>

    <?php if (!$pdo): ?>
      <div class="muted">tasks.sqlite が見つからないため、ユーザー情報を表示できません。</div>
    <?php elseif (empty($users)): ?>
      <div class="muted">ユーザーがまだ登録されていません。</div>
    <?php else: ?>
      <div class="grid">
        <?php foreach ($users as $u): ?>
          <div class="card">
            <div class="avatar">
              <?php if ($u['avatar']): ?>
                <img src="<?php echo h($u['avatar']); ?>" alt="avatar" />
              <?php else: ?>
                <span>👤</span>
              <?php endif; ?>
            </div>
            <div style="flex:1 1 auto; min-width:0;">
              <div class="name"><?php echo h($u['name'] ?: '（名称未設定）'); ?></div>
              <div class="muted">LINE ID: <?php echo h($u['line_user_id']); ?></div>
              <div class="stats">
                <div class="stat">
                  <div class="label">⏳ 着手</div>
                  <div class="value"><?php echo (int)$u['in_progress_count']; ?></div>
                </div>
                <div class="stat">
                  <div class="label">✅ 掲示</div>
                  <div class="value"><?php echo (int)$u['done_count']; ?></div>
                </div>
                <div class="stat">
                  <div class="label">⚠️ 異常</div>
                  <div class="value"><?php echo (int)$u['issue_count']; ?></div>
                </div>
              </div>
              <div class="meta">
                <div>更新地点: <strong><?php echo (int)$u['boards_updated']; ?></strong></div>
                <div>コメント: <strong><?php echo (int)$u['comments_count']; ?></strong></div>
                <div>最終更新: <strong><?php echo $u['last_activity'] ? h($u['last_activity']) : '—'; ?></strong></div>
              </div>
            </div>
          </div>
        <?php endforeach; ?>
      </div>
    <?php endif; ?>
  </div>

  <script>
    async function handleBulkReassign(event) {
      event.preventDefault();
      
      const form = event.target;
      const formData = new FormData(form);
      formData.append('action', 'bulk_reassign');
      
      const btn = document.getElementById('reassignBtn');
      const statusMsg = document.getElementById('statusMessage');
      
      btn.disabled = true;
      btn.textContent = '実行中...';
      statusMsg.style.display = 'none';
      
      try {
        const response = await fetch('users.php', {
          method: 'POST',
          body: formData
        });
        
        const result = await response.json();
        
        if (result.success) {
          statusMsg.className = 'status-message success';
          statusMsg.textContent = result.message;
          statusMsg.style.display = 'block';
          
          // Reset form
          form.reset();
          
          // Reload page after 2 seconds to show updated counts
          setTimeout(() => {
            window.location.reload();
          }, 2000);
        } else {
          statusMsg.className = 'status-message error';
          statusMsg.textContent = result.error || '一括割り振りに失敗しました';
          statusMsg.style.display = 'block';
        }
      } catch (error) {
        statusMsg.className = 'status-message error';
        statusMsg.textContent = '通信エラーが発生しました';
        statusMsg.style.display = 'block';
      } finally {
        btn.disabled = false;
        btn.textContent = '一括割り振り実行';
      }
    }
  </script>
</body>
</html>
