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
        ['id' => 'r2', 'page' => 3, 'fields' => [
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

array_map('unlink', glob($dir . '/records/' . $slug . '/*.json') ?: []);
rmdir($dir . '/records/' . $slug);
rmdir($dir . '/records');
rmdir($dir);

exit($failures === 0 ? 0 : 1);
