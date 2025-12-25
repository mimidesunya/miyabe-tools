<?php
// boards.sqlite をバックエンドとした bbox JSON エンドポイント
// JSON 配列を返す: { code, address, place, lat, lon }
//
// 使い方:
//   GET /boards/api/query.php?slug=...&min_lat=...&max_lat=...&min_lon=...&max_lon=...&limit=1000
//   - bbox パラメータが指定された場合、R-Tree を使用してバウンディングボックス内のポイントを返す。
//   - bbox パラメータがない場合、すべての行を返す（limit まで）。

declare(strict_types=1);

require '/var/www/lib/session.php';

header('Content-Type: application/json; charset=UTF-8');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
header('Pragma: no-cache');

$slug = get_slug();
$pdo = open_boards_with_tasks_pdo($slug);

// tasks DB がアタッチされているか確認
$withTasks = false;
$stmt = $pdo->query("PRAGMA database_list");
while ($row = $stmt->fetch()) {
    if ($row['name'] === 'tasks') {
        $withTasks = true;
        break;
    }
}

// bbox パラメータのパース
$minLat = isset($_GET['min_lat']) && is_numeric($_GET['min_lat']) ? (float)$_GET['min_lat'] : null;
$maxLat = isset($_GET['max_lat']) && is_numeric($_GET['max_lat']) ? (float)$_GET['max_lat'] : null;
$minLon = isset($_GET['min_lon']) && is_numeric($_GET['min_lon']) ? (float)$_GET['min_lon'] : null;
$maxLon = isset($_GET['max_lon']) && is_numeric($_GET['max_lon']) ? (float)$_GET['max_lon'] : null;

$limit = 10000; // デフォルトの制限
if (isset($_GET['limit']) && is_numeric($_GET['limit'])) {
    $lim = (int)$_GET['limit'];
    if ($lim > 0 && $lim <= 1000000) { $limit = $lim; }
}

// SQL の構築
$useBbox = ($minLat !== null && $maxLat !== null && $minLon !== null && $maxLon !== null);
    if ($useBbox) {
        $join = '';
        if (isset($withTasks) && $withTasks) { $join = 'LEFT JOIN tasks.task_status ts ON ts.board_code = b.code LEFT JOIN users.users u ON u.id = ts.updated_by'; }
    $selectStatus = (isset($withTasks) && $withTasks)
    ? "COALESCE(ts.status, 'pending') AS task_status, u.line_user_id AS updated_by_line_id, CASE WHEN ts.last_comment IS NOT NULL AND ts.last_comment <> '' THEN 1 ELSE 0 END AS has_comment"
    : "'pending' AS task_status, NULL AS updated_by_line_id, 0 AS has_comment";
    $sql = <<<SQL
        SELECT b.code, b.address, b.place, b.lat, b.lon, $selectStatus
        FROM boards_rtree r
        JOIN boards b ON b.id = r.id
        $join
        WHERE r.min_lon <= :max_lon AND r.max_lon >= :min_lon
          AND r.min_lat <= :max_lat AND r.max_lat >= :min_lat
        ORDER BY b.code ASC
        LIMIT :limit
    SQL;
    $stmt = $pdo->prepare($sql);
    $stmt->bindValue(':max_lon', $maxLon, PDO::PARAM_STR);
    $stmt->bindValue(':min_lon', $minLon, PDO::PARAM_STR);
    $stmt->bindValue(':max_lat', $maxLat, PDO::PARAM_STR);
    $stmt->bindValue(':min_lat', $minLat, PDO::PARAM_STR);
    $stmt->bindValue(':limit', $limit, PDO::PARAM_INT);
} else {
    $join = '';
    if (isset($withTasks) && $withTasks) { $join = 'LEFT JOIN tasks.task_status ts ON ts.board_code = b.code LEFT JOIN users.users u ON u.id = ts.updated_by'; }
    $selectStatus = (isset($withTasks) && $withTasks)
        ? "COALESCE(ts.status, 'pending') AS task_status, u.line_user_id AS updated_by_line_id, CASE WHEN ts.last_comment IS NOT NULL AND ts.last_comment <> '' THEN 1 ELSE 0 END AS has_comment"
        : "'pending' AS task_status, NULL AS updated_by_line_id, 0 AS has_comment";
    $sql = <<<SQL
        SELECT b.code, b.address, b.place, b.lat, b.lon, $selectStatus
        FROM boards b
        $join
        ORDER BY b.code ASC
        LIMIT :limit
    SQL;
    $stmt = $pdo->prepare($sql);
    $stmt->bindValue(':limit', $limit, PDO::PARAM_INT);
}

try { $stmt->execute(); } catch (Throwable $e) {
    http_response_code(500);
    echo json_encode(['error' => 'クエリに失敗しました'], JSON_UNESCAPED_UNICODE);
    exit;
}

$out = [];
while ($row = $stmt->fetch()) {
    $lat = $row['lat'];
    $lon = $row['lon'];
    $out[] = [
        'code' => isset($row['code']) ? (string)$row['code'] : '',
        'address' => isset($row['address']) ? (string)$row['address'] : '',
        'place' => isset($row['place']) ? (string)$row['place'] : '',
        'lat' => ($lat === null || $lat === '') ? null : (float)$lat,
        'lon' => ($lon === null || $lon === '') ? null : (float)$lon,
    'status' => isset($row['task_status']) ? (string)$row['task_status'] : null,
    'updated_by_line_id' => isset($row['updated_by_line_id']) ? (string)$row['updated_by_line_id'] : null,
    'has_comment' => isset($row['has_comment']) ? (bool)$row['has_comment'] : false,
    ];
}

echo json_encode($out, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
exit;
?>
