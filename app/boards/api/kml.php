<?php
// 掲示場データを KML 形式でダウンロードする
// GET /boards/api/kml.php?slug=...
// ステータスに応じてピンの色を変える（tasks DB がある場合）

declare(strict_types=1);

require_once dirname(__DIR__, 3) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'session.php';

$slug = get_slug();
$municipality = municipality_entry($slug);
if ($municipality === null) {
    http_response_code(404);
    exit;
}

$pdo = open_boards_with_tasks_pdo($slug);

$withTasks = false;
$stmt = $pdo->query("PRAGMA database_list");
while ($row = $stmt->fetch()) {
    if ($row['name'] === 'tasks') { $withTasks = true; break; }
}

if ($withTasks) {
    $sql = <<<SQL
        SELECT b.code, b.address, b.place, b.lat, b.lon,
               COALESCE(ts.status, 'pending') AS task_status
          FROM boards b
     LEFT JOIN tasks.task_status ts ON ts.board_code = b.code
         WHERE b.lat IS NOT NULL AND b.lat != '' AND b.lon IS NOT NULL AND b.lon != ''
      ORDER BY b.code ASC
    SQL;
} else {
    $sql = <<<SQL
        SELECT code, address, place, lat, lon, 'pending' AS task_status
          FROM boards
         WHERE lat IS NOT NULL AND lat != '' AND lon IS NOT NULL AND lon != ''
      ORDER BY code ASC
    SQL;
}

$stmt = $pdo->query($sql);
$boards = $stmt->fetchAll();

// ステータス別スタイルID
$styleMap = [
    'pending'     => 'stylePending',
    'in_progress' => 'styleInProgress',
    'done'        => 'styleDone',
    'issue'       => 'styleIssue',
];
// KML アイコン色 (aabbggrr 形式)
$colors = [
    'pending'     => 'ff999999', // グレー
    'in_progress' => 'ff00aaff', // オレンジ
    'done'        => 'ff44bb44', // 緑
    'issue'       => 'ff0000dd', // 赤
];

$name = (string)($municipality['boards']['title'] ?? ($municipality['name'] . ' ポスター掲示場'));
$filename = $slug . '-boards.kml';

header('Content-Type: application/vnd.google-earth.kml+xml; charset=UTF-8');
header('Content-Disposition: attachment; filename="' . $filename . '"');
header('Cache-Control: no-store');

echo '<?xml version="1.0" encoding="UTF-8"?>' . "\n";
?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <name><?php echo htmlspecialchars($name, ENT_XML1, 'UTF-8'); ?></name>
<?php foreach ($colors as $status => $color): ?>
  <Style id="<?php echo $styleMap[$status]; ?>">
    <IconStyle>
      <color><?php echo $color; ?></color>
      <scale>0.9</scale>
      <Icon><href>http://maps.google.com/mapfiles/kml/paddle/wht-blank.png</href></Icon>
    </IconStyle>
    <LabelStyle><scale>0.7</scale></LabelStyle>
  </Style>
<?php endforeach; ?>
<?php foreach ($boards as $b): ?>
  <Placemark>
    <name><?php echo htmlspecialchars((string)$b['code'], ENT_XML1, 'UTF-8'); ?></name>
    <description><?php echo htmlspecialchars((string)$b['place'] . "\n" . (string)$b['address'], ENT_XML1, 'UTF-8'); ?></description>
    <styleUrl>#<?php echo $styleMap[$b['task_status']] ?? $styleMap['pending']; ?></styleUrl>
    <Point>
      <coordinates><?php echo (float)$b['lon'] . ',' . (float)$b['lat'] . ',0'; ?></coordinates>
    </Point>
  </Placemark>
<?php endforeach; ?>
</Document>
</kml>
