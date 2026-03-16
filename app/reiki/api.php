<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'municipalities.php';

header('Content-Type: application/json; charset=utf-8');

$workspaceRoot = dirname(__DIR__, 2);
$dbPath = $workspaceRoot . DIRECTORY_SEPARATOR . 'data' . DIRECTORY_SEPARATOR . 'reiki' . DIRECTORY_SEPARATOR . 'feedback.sqlite';

$dbDir = dirname($dbPath);
if (!is_dir($dbDir)) {
    @mkdir($dbDir, 0755, true);
}

try {
    $pdo = new PDO('sqlite:' . $dbPath);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    
    // Ensure tables exist (in case DB was rebuilt without new tables)
    $pdo->exec("CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT NOT NULL,
        vote TEXT NOT NULL CHECK(vote IN ('good', 'bad')),
        comment TEXT DEFAULT '',
        ip_hash TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )");
    $pdo->exec("CREATE INDEX IF NOT EXISTS idx_feedback_filename ON feedback(filename)");
    $pdo->exec("CREATE TABLE IF NOT EXISTS view_counts (
        filename TEXT PRIMARY KEY,
        count INTEGER DEFAULT 0
    )");
} catch (Exception $e) {
    http_response_code(500);
    echo json_encode(['error' => 'Database connection failed'], JSON_UNESCAPED_UNICODE);
    exit;
}

$method = $_SERVER['REQUEST_METHOD'];
$action = trim((string)($_GET['action'] ?? ''));

function request_slug(?array $input = null): string {
    $slug = get_slug($input['slug'] ?? ($_GET['slug'] ?? null));
    return $slug !== '' ? $slug : get_default_slug();
}

function feedback_scope_key(string $slug, string $filename): string {
    return $slug . ':' . $filename;
}

function ensure_reiki_feature(string $slug): void {
    if (!municipality_feature_enabled($slug, 'reiki')) {
        http_response_code(404);
        echo json_encode(['error' => 'この自治体の例規集はまだ利用できません'], JSON_UNESCAPED_UNICODE);
        exit;
    }
}

// Helper: hash IP for privacy
function hashIp(): string {
    $ip = $_SERVER['REMOTE_ADDR'] ?? '0.0.0.0';
    return hash('sha256', $ip . 'reiki-feedback-salt');
}

// ─── GET: Retrieve feedback stats and view count ───
if ($method === 'GET' && $action === 'stats') {
    $slug = request_slug();
    ensure_reiki_feature($slug);
    $filename = trim((string)($_GET['filename'] ?? ''));
    if ($filename === '') {
        http_response_code(400);
        echo json_encode(['error' => 'filename is required'], JSON_UNESCAPED_UNICODE);
        exit;
    }
    $scopeKey = feedback_scope_key($slug, $filename);

    // Vote counts
    $stmt = $pdo->prepare("SELECT vote, COUNT(*) as cnt FROM feedback WHERE filename = :f GROUP BY vote");
    $stmt->execute([':f' => $scopeKey]);
    $votes = ['good' => 0, 'bad' => 0];
    while ($row = $stmt->fetch(PDO::FETCH_ASSOC)) {
        $votes[$row['vote']] = (int)$row['cnt'];
    }

    // Recent comments (latest 20)
    $stmt = $pdo->prepare("SELECT vote, comment, created_at FROM feedback WHERE filename = :f AND comment != '' ORDER BY created_at DESC LIMIT 20");
    $stmt->execute([':f' => $scopeKey]);
    $comments = $stmt->fetchAll(PDO::FETCH_ASSOC);

    // View count
    $stmt = $pdo->prepare("SELECT count FROM view_counts WHERE filename = :f");
    $stmt->execute([':f' => $scopeKey]);
    $viewRow = $stmt->fetch(PDO::FETCH_ASSOC);
    $viewCount = $viewRow ? (int)$viewRow['count'] : 0;

    echo json_encode([
        'votes' => $votes,
        'comments' => $comments,
        'viewCount' => $viewCount,
    ], JSON_UNESCAPED_UNICODE);
    exit;
}

// ─── POST: Submit vote ───
if ($method === 'POST' && $action === 'vote') {
    $input = json_decode(file_get_contents('php://input'), true);
    if (!is_array($input)) {
        $input = [];
    }
    $slug = request_slug($input);
    ensure_reiki_feature($slug);
    $filename = trim((string)($input['filename'] ?? ''));
    $vote = trim((string)($input['vote'] ?? ''));
    $comment = mb_substr(trim((string)($input['comment'] ?? '')), 0, 200);

    if ($filename === '' || !in_array($vote, ['good', 'bad'], true)) {
        http_response_code(400);
        echo json_encode(['error' => 'filename and vote (good/bad) are required'], JSON_UNESCAPED_UNICODE);
        exit;
    }
    $scopeKey = feedback_scope_key($slug, $filename);

    $ipHash = hashIp();

    $stmt = $pdo->prepare("INSERT INTO feedback (filename, vote, comment, ip_hash) VALUES (:f, :v, :c, :ip)");
    $stmt->execute([
        ':f' => $scopeKey,
        ':v' => $vote,
        ':c' => $comment,
        ':ip' => $ipHash,
    ]);

    // Return updated counts
    $stmt = $pdo->prepare("SELECT vote, COUNT(*) as cnt FROM feedback WHERE filename = :f GROUP BY vote");
    $stmt->execute([':f' => $scopeKey]);
    $votes = ['good' => 0, 'bad' => 0];
    while ($row = $stmt->fetch(PDO::FETCH_ASSOC)) {
        $votes[$row['vote']] = (int)$row['cnt'];
    }

    echo json_encode(['ok' => true, 'votes' => $votes], JSON_UNESCAPED_UNICODE);
    exit;
}

// ─── POST: Increment view count ───
if ($method === 'POST' && $action === 'view') {
    $input = json_decode(file_get_contents('php://input'), true);
    if (!is_array($input)) {
        $input = [];
    }
    $slug = request_slug($input);
    ensure_reiki_feature($slug);
    $filename = trim((string)($input['filename'] ?? ''));
    if ($filename === '') {
        http_response_code(400);
        echo json_encode(['error' => 'filename is required'], JSON_UNESCAPED_UNICODE);
        exit;
    }
    $scopeKey = feedback_scope_key($slug, $filename);

    $stmt = $pdo->prepare("INSERT INTO view_counts (filename, count) VALUES (:f, 1) ON CONFLICT(filename) DO UPDATE SET count = count + 1");
    $stmt->execute([':f' => $scopeKey]);

    // Return updated count
    $stmt = $pdo->prepare("SELECT count FROM view_counts WHERE filename = :f");
    $stmt->execute([':f' => $scopeKey]);
    $viewRow = $stmt->fetch(PDO::FETCH_ASSOC);

    echo json_encode(['ok' => true, 'viewCount' => (int)($viewRow['count'] ?? 0)], JSON_UNESCAPED_UNICODE);
    exit;
}

http_response_code(404);
echo json_encode(['error' => 'Unknown action'], JSON_UNESCAPED_UNICODE);
