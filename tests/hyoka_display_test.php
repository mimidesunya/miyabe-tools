<?php
declare(strict_types=1);

// 事務事業評価の画面用の控え（data/hyoka）を読む部品を見る。`php tests/hyoka_display_test.php` で走る。
// 控えのファイル名は詳細画面の ID から作るので、ID の形が崩れていれば読まないことも確かめる。

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'hyoka_display.php';

$failures = 0;

function check(string $label, bool $ok): void
{
    global $failures;
    if (!$ok) {
        $failures++;
        fwrite(STDERR, "FAIL: {$label}\n");
        return;
    }
    echo "ok: {$label}\n";
}

$dir = sys_get_temp_dir() . DIRECTORY_SEPARATOR . 'hyoka-display-test-' . bin2hex(random_bytes(4));
$slug = '99999-example-shi';
$docId = str_repeat('a', 16);
mkdir($dir . '/records/' . $slug, 0777, true);
file_put_contents($dir . '/records/' . $slug . '/' . $docId . '.json', json_encode([
    'slug' => $slug,
    'doc_id' => $docId,
    'source_url' => 'https://example.jp/a.pdf#old',
    'records' => [
        ['id' => 'r1', 'page' => 1, 'fields' => [
            ['key' => 'project_name', 'name' => '事業名', 'label' => '事業名', 'text' => '広報事業'],
        ], 'raw_fields' => []],
        ['id' => 'r2', 'page' => 3, 'layout' => 'ai_transcription', 'fields' => [
            ['key' => 'project_name', 'name' => '事業名', 'label' => '事務事業名', 'text' => '防災<script>'],
            ['key' => 'future_direction', 'name' => '今後の方向性', 'label' => '今後の方向性', 'text' => '継続'],
        ], 'raw_fields' => [['label' => '事業概要', 'text' => "訓練を行う。\n年 2 回。"]]],
    ],
], JSON_UNESCAPED_UNICODE));
putenv('MIYABE_HYOKA_DISPLAY_DIR=' . $dir);

check('ID から slug・文書・頁を取る', miyabe_hyoka_display_parse_id("hyoka:{$slug}:{$docId}:p3") === ['slug' => $slug, 'doc_id' => $docId, 'page' => 3]);
check('頁なしの ID', miyabe_hyoka_display_parse_id("hyoka:{$slug}:{$docId}")['page'] === 0);
check('形の崩れた ID は読まない', miyabe_hyoka_display_parse_id("hyoka:../{$slug}:{$docId}") === null
    && miyabe_hyoka_display_records("hyoka:{$slug}:../{$docId}") === []);

$all = miyabe_hyoka_display_records("hyoka:{$slug}:{$docId}");
check('文書全体なら全部の事業', array_column($all, 'id') === ['r1', 'r2']);
$page = miyabe_hyoka_display_records("hyoka:{$slug}:{$docId}:p3");
check('頁で分けた詳細なら、その頁の事業だけ', array_column($page, 'id') === ['r2']);
check('控えの無い文書は空', miyabe_hyoka_display_records("hyoka:{$slug}:" . str_repeat('b', 16)) === []);

$found = miyabe_hyoka_display_find_record("hyoka:{$slug}:{$docId}", 'r2');
check('事業を ID で引く', $found !== null && $found['source_url'] === 'https://example.jp/a.pdf#old');
$html = miyabe_hyoka_display_record_body_html($found['record'], $found['source_url']);
check('中身の文字を逃がす', str_contains($html, '防災&lt;script&gt;') && !str_contains($html, '<script>'));
check('原典の欄名が標準の名前と違えば添える', str_contains($html, '原典の欄名: 事務事業名'));
check('そのほかの欄も出す', str_contains($html, 'そのほかの欄') && str_contains($html, '事業概要'));
check('原典の頁へ飛ぶ（元の # は外す）', str_contains($html, 'href="https://example.jp/a.pdf#page=3"') && str_contains($html, '原典 3頁を開く'));
check('一覧の札は長い文を切る', miyabe_hyoka_display_short(str_repeat('あ', 40), 30) === str_repeat('あ', 30) . '…');
check('取得の状態の名前', miyabe_hyoka_display_fetch_label('entry_unreachable') === '入口に届かない');

// --- 検索結果の一覧に添える事業
$found = miyabe_hyoka_display_search_records("hyoka:{$slug}:{$docId}", ['訓練']);
check('検索結果に事業の数を添える', $found['total'] === 2);
check('検索語を含む事業を先にする', array_column($found['records'], 'id') === ['r2', 'r1']);
check('検索語を含むかの印', $found['records'][0]['matched'] === true && $found['records'][1]['matched'] === false);
check('事業名と方向性を短い形に入れる', $found['records'][0]['project_name'] === '防災<script>' && $found['records'][0]['future_direction'] === '継続');
check('AI が書き写した記録かを短い形に入れる', $found['records'][0]['method'] === 'ai' && $found['records'][1]['method'] === 'machine');
$aiRecord = ['layout' => 'ai_transcription'];
check('型の無い控えは最初の型と書く', miyabe_hyoka_display_ai_model_label([$aiRecord, ['layout' => '']]) === 'OpenAI の gpt-6-luna');
check('実際に使った型を重ねずに書く', miyabe_hyoka_display_ai_model_label([
    $aiRecord + ['model' => 'gpt-6-luna + claude-haiku-5-5'], $aiRecord + ['model' => 'claude-haiku-5-5'],
]) === 'OpenAI の gpt-6-luna・Anthropic の Claude Haiku 5.5');
check('語が無ければ原典の順', array_column(miyabe_hyoka_display_search_records("hyoka:{$slug}:{$docId}", [])['records'], 'id') === ['r1', 'r2']);
check('件数の上限で切る', count(miyabe_hyoka_display_search_records("hyoka:{$slug}:{$docId}", [], 1)['records']) === 1);
check('頁で分けた結果ならその頁の事業だけ', miyabe_hyoka_display_search_records("hyoka:{$slug}:{$docId}:p1", ['訓練'])['total'] === 1);
check('控えの無い文書は空', miyabe_hyoka_display_search_records("hyoka:{$slug}:" . str_repeat('b', 16), ['訓練']) === ['records' => [], 'total' => 0]);

// --- シートの全部（1 行目が目次、頁ごとに 1 行）
mkdir($dir . '/sheets/' . $slug, 0777, true);
$sheetPath = $dir . '/sheets/' . $slug . '/' . $docId . '.json.gz';
$sheetLines = [
    ['doc_id' => $docId, 'pages' => [1, 2, 3, 4]],
    ['page' => 1, 'garbled' => false, 'tables' => [[
        'top' => 40.0, 'rows' => 2, 'cols' => 2, 'widths' => [25.0, 75.0],
        'cells' => [
            ['r' => 0, 'c' => 0, 'rs' => 2, 'cs' => 1, 'text' => '事業名'],
            ['r' => 0, 'c' => 1, 'rs' => 1, 'cs' => 1, 'text' => '防災<script>'],
            ['r' => 1, 'c' => 1, 'rs' => 1, 'cs' => 1, 'text' => ''],
        ],
    ]], 'lines' => [['y' => 10, 'x' => 5, 'text' => '令和6年度 評価シート']], 'vertical' => ['縦の字']],
    ['page' => 2, 'garbled' => true, 'tables' => [], 'lines' => [], 'vertical' => []],
    ['page' => 3, 'image_only' => true],
    ['page' => 4, 'plain' => "多すぎる図形の頁\n<文字>"],
];
$sheetGz = gzopen($sheetPath, 'wb');
foreach ($sheetLines as $line) {
    gzwrite($sheetGz, json_encode($line, JSON_UNESCAPED_UNICODE) . "\n");
}
gzclose($sheetGz);

check('シートのある文書を見つける', realpath((string)miyabe_hyoka_display_sheet_path("hyoka:{$slug}:{$docId}")) === realpath($sheetPath));
check('シートの無い文書は null', miyabe_hyoka_display_sheet_path("hyoka:{$slug}:" . str_repeat('b', 16)) === null);
check('形の崩れた ID ではシートを読まない', miyabe_hyoka_display_sheet_page("hyoka:../{$slug}:{$docId}") === null);
$sheet = miyabe_hyoka_display_sheet_page("hyoka:{$slug}:{$docId}");
check('頁を指定しなければ最初の頁', $sheet['page']['page'] === 1 && $sheet['pages'] === [1, 2, 3, 4]);
check('頁で分けた詳細なら、その頁を開く', miyabe_hyoka_display_sheet_page("hyoka:{$slug}:{$docId}:p3")['page']['page'] === 3);
check('頁を選べる', miyabe_hyoka_display_sheet_page("hyoka:{$slug}:{$docId}", 2)['page']['page'] === 2);
check('無い頁は null', miyabe_hyoka_display_sheet_page("hyoka:{$slug}:{$docId}", 9) === null);
$sheetHtml = miyabe_hyoka_display_sheet_html($sheet, 'https://example.jp/a.pdf#old');
check('表の結合を保つ', str_contains($sheetHtml, '<td rowspan="2">事業名</td>'));
check('表の文字を逃がす', str_contains($sheetHtml, '防災&lt;script&gt;') && !str_contains($sheetHtml, '<script>'));
check('空の升目も残す', str_contains($sheetHtml, '<td></td>'));
check('列の幅を保つ', str_contains($sheetHtml, '<col style="width:25.0%">'));
check('表の外の文字を上から順に出す', strpos($sheetHtml, '令和6年度') < strpos($sheetHtml, '事業名'));
check('縦書きの文字も出す', str_contains($sheetHtml, '縦の字'));
check('次の頁へ動ける', str_contains($sheetHtml, 'data-sheet-page="2"') && !str_contains($sheetHtml, '前の頁'));
check('原典の頁へ飛ぶ', str_contains($sheetHtml, 'href="https://example.jp/a.pdf#page=1"'));
check('文字化けの頁はそう書く', str_contains(miyabe_hyoka_display_sheet_page_html($sheetLines[2]), '文字化け'));
check('画像だけの頁はそう書く', str_contains(miyabe_hyoka_display_sheet_page_html($sheetLines[3]), '文字の層がありません'));
check('図形の多い頁は文字のまま出す', str_contains(miyabe_hyoka_display_sheet_page_html($sheetLines[4]), '&lt;文字&gt;'));
$canvasHtml = miyabe_hyoka_display_sheet_page_html(['page' => 5, 'width' => 595.0, 'height' => 842.0, 'tables' => [], 'lines' => [], 'canvas' => [
    ['l' => 10.5, 't' => 20.25, 's' => 1.4, 'x' => '事業<名>'],
    ['l' => 90.0, 't' => 5.0, 's' => 1.2, 'x' => '縦書き', 'v' => 1],
]]);
check('位置の表示は原典の位置に置く', str_contains($canvasHtml, 'left:10.50%;top:20.25%;font-size:1.40cqw'));
check('位置の表示も文字を逃がす', str_contains($canvasHtml, '事業&lt;名&gt;'));
check('位置の表示は頁の縦横比を保つ', str_contains($canvasHtml, 'aspect-ratio:595.0/842.0'));
check('位置の表示の縦書き', str_contains($canvasHtml, 'class="is-vertical"'));
check('javascript の原典には飛ばない', !str_contains(miyabe_hyoka_display_sheet_html($sheet, 'javascript:alert(1)'), 'href='));
check('HTML の原典には頁の印を付けない', str_contains(miyabe_hyoka_display_sheet_html($sheet, 'https://example.jp/a.htm'), 'href="https://example.jp/a.htm"'));

// 表全体を囲む帯の升目（川崎市の「事業の概要」）は、ほかの升目と重ならない上の行に縮める。
$frameHtml = miyabe_hyoka_display_sheet_table_html(['cells' => [
    ['r' => 0, 'c' => 0, 'rs' => 3, 'cs' => 3, 'text' => '事業の概要'],
    ['r' => 1, 'c' => 2, 'rs' => 1, 'cs' => 1, 'text' => '値'],
    ['r' => 1, 'c' => 1, 'rs' => 1, 'cs' => 1, 'text' => '欄'],
    ['r' => 2, 'c' => 1, 'rs' => 1, 'cs' => 2, 'text' => '下'],
]]);
check('囲む帯は上の行に縮める', str_contains($frameHtml, '<tr><td colspan="3">事業の概要</td></tr>'));
check('升目は列の順に並べ、空いた番地は空の升目で埋める', str_contains($frameHtml, '<tr><td></td><td>欄</td><td>値</td></tr>')
    && str_contains($frameHtml, '<tr><td></td><td colspan="2">下</td></tr>'));
$coveredHtml = miyabe_hyoka_display_sheet_table_html(['cells' => [
    ['r' => 0, 'c' => 0, 'rs' => 1, 'cs' => 2, 'text' => '覆われた'],
    ['r' => 0, 'c' => 0, 'rs' => 1, 'cs' => 1, 'text' => 'A'],
    ['r' => 0, 'c' => 1, 'rs' => 1, 'cs' => 1, 'text' => 'B'],
]]);
check('すべて覆われた升目の文字も落とさない', str_contains($coveredHtml, '<td colspan="2">覆われた</td>'));
$spanHtml = miyabe_hyoka_display_sheet_table_html(['cells' => [
    ['r' => 0, 'c' => 0, 'rs' => 3, 'cs' => 1, 'text' => '縦'],
    ['r' => 0, 'c' => 1, 'rs' => 3, 'cs' => 1, 'text' => '縦2'],
]]);
check('上の升目に覆われた行も数に入れる', substr_count($spanHtml, '<tr>') === 3);

// 様式ごとの読み取りの記録（金額などを表で持つ）
$formRecord = [
    'id' => $docId . '-1', 'layout' => 'form_reader', 'page' => 2,
    'fields' => [['key' => 'project_name', 'name' => '事業名', 'label' => '', 'text' => '人権関連事業'],
                 ['key' => 'future_direction', 'name' => '今後の方向性', 'label' => '', 'text' => '継続']],
    'raw_fields' => [['label' => '根拠法令', 'text' => '人権教育法']],
    'amounts' => ['unit' => '千円', 'columns' => [['year' => 2024, 'stage' => '予算'], ['year' => 2024, 'stage' => '決算']],
                  'rows' => [['label' => '事業費 A', 'kind' => '事業費（人件費を除く）', 'unit' => '', 'values' => ['44,140', '37,861']],
                             ['label' => '総コスト<A+B>', 'kind' => '総費用（人件費を含む）', 'unit' => '', 'values' => ['89,749', '']]]],
    'funding' => null, 'staff' => null,
    'indicators' => [['name' => '受講者数', 'kind' => '活動', 'unit' => '人', 'values' => [['year' => 2024, 'target' => '560', 'actual' => '612', 'rate' => '']]]],
    'evaluations' => [['stage' => '所管局', 'rating' => '3', 'direction' => '継続', 'aspects' => ['必要性: A']]],
    'checks' => ['pairs' => 4, 'problems' => []],
];
$formHtml = miyabe_hyoka_display_record_body_html($formRecord, 'https://example.jp/a.pdf');
check('様式ごとの読み取りの記録か見分ける', miyabe_hyoka_display_is_form($formRecord) && !miyabe_hyoka_display_is_ai($formRecord));
check('一覧の札は書き写し方を program にする', miyabe_hyoka_display_record_summary($formRecord)['method'] === 'program');
check('金額は年度と段階の表にする', str_contains($formHtml, '（単位：千円）') && str_contains($formHtml, '2024年度 決算') && str_contains($formHtml, '<td>37,861</td>'));
check('金額の欄名も逃がす', str_contains($formHtml, '総コスト&lt;A+B&gt;'));
check('指標の目標と実績を出す', str_contains($formHtml, '受講者数') && str_contains($formHtml, '<td>612</td>') && !str_contains($formHtml, '達成率'));
check('評価の段階を出す', str_contains($formHtml, '所管局') && str_contains($formHtml, '必要性: A'));
check('検算の結果を出す', str_contains($formHtml, '合計と内訳を 4 組確かめ、すべて合っています'));
check('表の無い記録には表を出さない', !str_contains(miyabe_hyoka_display_record_body_html(
    ['id' => 'r9', 'fields' => [['key' => 'project_name', 'name' => '事業名', 'label' => '', 'text' => '広報事業']], 'raw_fields' => []], ''
), 'hyoka-record-table'));

unlink($sheetPath);
rmdir($dir . '/sheets/' . $slug);
rmdir($dir . '/sheets');

array_map('unlink', glob($dir . '/records/' . $slug . '/*.json') ?: []);
rmdir($dir . '/records/' . $slug);
rmdir($dir . '/records');
rmdir($dir);

exit($failures === 0 ? 0 : 1);
