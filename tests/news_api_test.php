<?php
declare(strict_types=1);

// お知らせの API（lib/news_api.php）。`php tests/news_api_test.php` で走る。
//
// 一時の置き場にお知らせの記録を書き、tools/news/news_index.py で索引を作ってから、
// 一覧と 1 件を引く。自治体の名前の解き方、条件の検査、本文と添付の行の読み方を確かめる。

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'news_api.php';

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

function fails(callable $action, int $status, string $code): bool
{
    try {
        $action();
    } catch (NewsApiError $error) {
        return $error->httpStatus === $status && $error->statusCode === $code;
    }
    return false;
}

function write_lines(string $path, array $rows): void
{
    if (!is_dir(dirname($path))) {
        mkdir(dirname($path), 0777, true);
    }
    $text = '';
    foreach ($rows as $row) {
        $text .= json_encode($row, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . "\n";
    }
    file_put_contents($path, $text, FILE_APPEND);
}

function remove_tree(string $path): void
{
    if (!is_dir($path)) {
        @unlink($path);
        return;
    }
    foreach (scandir($path) ?: [] as $name) {
        if ($name !== '.' && $name !== '..') {
            remove_tree($path . DIRECTORY_SEPARATOR . $name);
        }
    }
    @rmdir($path);
}

$root = sys_get_temp_dir() . DIRECTORY_SEPARATOR . 'news-api-test-' . bin2hex(random_bytes(4));
$data = $root . DIRECTORY_SEPARATOR . 'data';
$work = $root . DIRECTORY_SEPARATOR . 'work';
register_shutdown_function(static fn() => remove_tree($root));

$machida = $data . '/13209-machida-shi';
$item = static fn(string $id, string $title, ?string $date, string $seen, array $extra = []): array => array_filter([
    'id' => $id, 'jis_code' => '13209', 'url' => "https://www.city.machida.tokyo.jp/{$id}.html",
    'title' => $title, 'date' => $date, 'first_seen_at' => $seen, 'revision' => 0,
] + $extra, static fn($value) => $value !== null);
write_lines("{$machida}/items/2026.jsonl", [
    $item('m1', '電子入札の公表', '2026-11-13', '2026-11-13 19:00:00', ['summary' => '一般競争入札の案件', 'categories' => ['入札']]),
    $item('m2', '図書館の休館', '2026-11-14', '2026-11-14 10:00:00'),
    $item('m3', '100%還元のお知らせ', '2026-10-01', '2026-11-13 19:00:00'),
    $item('m4', '市民まつり', '2026-12-01', '2026-11-12 09:00:00'),
]);
$bodyText = "ページ番号：255711136\n電子入札の公表\n町田市では、一般競争入札は原則として電子入札で行っています。\n一覧表で公開します。";
write_lines("{$machida}/bodies/2026.jsonl", [
    ['id' => 'm2', 'jis_code' => '13209', 'kind' => 'gone', 'status' => 404],
    ['id' => 'm1', 'jis_code' => '13209', 'revision' => 0, 'kind' => 'html', 'status' => 200, 'text' => $bodyText,
        'page_title' => '電子入札 | 町田市', 'fetched_at' => '2026-11-13 19:05:00',
        'attachments' => [
            ['url' => 'https://www.city.machida.tokyo.jp/list.pdf', 'text' => '一覧表（PDF 120KB）'],
            ['url' => 'https://www.city.machida.tokyo.jp/old.pdf', 'text' => '前の一覧表（PDF）'],
            ['url' => 'https://www.city.machida.tokyo.jp/form.xlsx', 'text' => '様式（Excel）'],
        ]],
]);
write_lines("{$machida}/attachments/2026.jsonl", [
    ['url' => 'https://www.city.machida.tokyo.jp/list.pdf', 'item' => 'm1', 'jis_code' => '13209', 'kind' => 'pdf',
        'pages' => 2, 'text' => "件名\f工事", 'fetched_at' => '2026-11-13 19:10:00'],
    ['url' => 'https://www.city.machida.tokyo.jp/old.pdf', 'item' => 'm1', 'jis_code' => '13209', 'kind' => 'pdf',
        'pages' => 1, 'text' => '前の件名', 'fetched_at' => '2026-11-13 19:10:02'],
]);
write_lines($data . '/07213-fukushima-date-shi/items/2026.jsonl', [
    ['id' => 'd1', 'jis_code' => '07213', 'url' => 'https://www.city.date.fukushima.jp/d1.html', 'title' => 'にこにこカフェ',
        'date' => '2026-11-14', 'first_seen_at' => '2026-11-14 10:00:00'],
]);

$python = getenv('MIYABE_PYTHON_BIN') ?: (PHP_OS_FAMILY === 'Windows' ? 'python' : 'python3');
$command = escapeshellarg($python) . ' ' . escapeshellarg(dirname(__DIR__) . '/tools/news/news_index.py')
    . ' --data-root ' . escapeshellarg($data) . ' --work-root ' . escapeshellarg($work) . ' 2>&1';
exec($command, $output, $exitCode);
check('索引を作れる', $exitCode === 0);
if ($exitCode !== 0) {
    fwrite(STDERR, implode("\n", $output) . "\n");
    exit(1);
}
putenv('MIYABE_NEWS_INDEX=' . $work . DIRECTORY_SEPARATOR . 'index.sqlite');
putenv('MIYABE_NEWS_DATA_ROOT=' . $data);
$pdo = news_api_open();

// 一覧
$all = news_api_list([], $pdo);
check('全国は新しい順', array_column($all['items'], 'id') === [
    'news:07213-fukushima-date-shi:d1', 'news:13209-machida-shi:m2', 'news:13209-machida-shi:m1',
    'news:13209-machida-shi:m4', 'news:13209-machida-shi:m3',
]);
check('催しの日は見つけた日で並べる', $all['items'][3]['date'] === '2026-12-01' && $all['items'][3]['sort_date'] === '2026-11-12');
check('条件が無ければ filters は空のオブジェクト', json_encode($all['filters']) === '{}');
check('索引の更新日時を返す', is_string($all['index_updated_at']));
$m1 = $all['items'][2];
check('本文の抜粋はページ番号と題名の行を飛ばす', ($m1['body']['excerpt'] ?? '') === '町田市では、一般競争入札は原則として電子入札で行っています。 一覧表で公開します。');
check('添付の数', $m1['attachments_count'] === 3 && $m1['categories'] === ['入札']);
check('消えていたページは gone', $all['items'][1]['body']['kind'] === 'gone' && $all['items'][1]['body']['excerpt'] === null);

$named = news_api_list(['municipality' => '東京都町田市', 'since' => '2026-11-13'], $pdo);
check('都道府県名つきの名前と since', $named['total'] === 2 && $named['municipality']['municipality_code'] === '13209'
    && $named['municipality']['collecting'] === true);
check('pref_code で都道府県', news_api_list(['pref_code' => '7'], $pdo)['total'] === 1);
$today = '2026-11-14';
$nationwide = news_api_list(['q' => '一般競争'], $pdo, $today);
check('q は題名と概要から', array_column($nationwide['items'], 'id') === ['news:13209-machida-shi:m1']);
check('全国の q は、since が無ければ直近 7 日', ($nationwide['filters']->since ?? null) === '2026-11-08'
    && isset($nationwide['note']));
check('全国の q で 31 日を超える期間は断る', fails(fn() => news_api_list(['q' => '還元', 'since' => '2026-10-01'], $pdo, $today), 400, 'invalid_request'));
check('全国の q の 31 日ちょうどは受ける', news_api_list(['q' => '還元', 'since' => '2026-10-15'], $pdo, $today)['total'] === 0);
check('自治体を指定した q は期間が自由', array_column(news_api_list(['q' => '100%', 'slug' => '13209-machida-shi'], $pdo, $today)['items'], 'id')
    === ['news:13209-machida-shi:m3']);
check('q の % は文字として探す', news_api_list(['q' => '%', 'pref_code' => '13', 'since' => '2026-01-01'], $pdo, $today)['total'] === 1);
check('自治体コードの 6 桁', news_api_list(['municipality_code' => '132098'], $pdo)['total'] === 4);
check('頁送り', array_column(news_api_list(['slug' => '13209-machida-shi', 'per_page' => '2', 'page' => '2'], $pdo)['items'], 'id')
    === ['news:13209-machida-shi:m4', 'news:13209-machida-shi:m3']);

$fuchu = news_api_list(['municipality' => '府中市', 'pref_code' => '13'], $pdo);
check('集めていない自治体は collecting=false と note', $fuchu['total'] === 0 && $fuchu['municipality']['collecting'] === false
    && isset($fuchu['note']));
try {
    news_api_list(['municipality' => '伊達市'], $pdo);
    check('同じ名前は 422', false);
} catch (NewsApiError $error) {
    check('同じ名前は 422 と候補', $error->httpStatus === 422 && count($error->extra['candidates']) === 2);
}
check('知らない名前は 422', fails(fn() => news_api_list(['municipality' => '町田'], $pdo), 422, 'municipality_not_found'));
check('知らないコードは 422', fails(fn() => news_api_list(['municipality_code' => '99999'], $pdo), 422, 'municipality_not_found'));
check('配列は断る', fails(fn() => news_api_list(['slug' => ['x']], $pdo), 400, 'invalid_request'));
check('壊れた日付は断る', fails(fn() => news_api_list(['since' => '2026-02-30'], $pdo), 400, 'invalid_request'));
check('since > until は断る', fails(fn() => news_api_list(['since' => '2026-11-14', 'until' => '2026-11-01'], $pdo), 400, 'invalid_request'));
check('page × per_page の上限', fails(fn() => news_api_list(['page' => '101', 'per_page' => '100'], $pdo), 400, 'invalid_request'));
check('pref_code 48 は断る', fails(fn() => news_api_list(['pref_code' => '48'], $pdo), 400, 'invalid_request'));
check('長すぎる q は断る', fails(fn() => news_api_list(['q' => str_repeat('あ', 51)], $pdo), 400, 'invalid_request'));

// 1 件
$one = news_api_item(['id' => 'news:13209-machida-shi:m1'], $pdo)['item'];
check('本文を元のファイルから読む', $one['body']['text'] === $bodyText && $one['body']['truncated'] === false
    && $one['body']['page_title'] === '電子入札 | 町田市');
check('添付の PDF に頁の印', ($one['attachments'][0]['text'] ?? '') === "〔1頁〕\n件名\n\n〔2頁〕\n工事");
check('読んでいない添付は read=false', $one['attachments'][2]['read'] === false && $one['attachments'][2]['kind'] === null);
check('1 件には api_item_url を付けない', !array_key_exists('api_item_url', $one));
$cut = news_api_item(['id' => 'news:13209-machida-shi:m1', 'max_body_chars' => '5', 'max_attachment_chars' => '3'], $pdo)['item'];
check('本文を切る', mb_strlen($cut['body']['text']) === 5 && $cut['body']['truncated'] === true
    && $cut['body']['chars'] === mb_strlen($bodyText));
check('切っても 1 頁目の印は付く', ($cut['attachments'][0]['text'] ?? '') === '〔1頁');
check('添付の上限は全部で。使い切った分は omitted', ($cut['attachments'][0]['truncated'] ?? null) === true
    && ($cut['attachments'][1]['omitted'] ?? null) === true && !isset($cut['attachments'][1]['text']));
$without = news_api_item(['id' => 'news:13209-machida-shi:m1', 'include_attachments' => '0'], $pdo)['item'];
check('include_attachments=0 は一覧だけ', count($without['attachments']) === 3 && !isset($without['attachments'][0]['text']));
check('本文の無い項目は body=null', news_api_item(['id' => 'news:13209-machida-shi:m3'], $pdo)['item']['body'] === null);
check('知らない id は 404', fails(fn() => news_api_item(['id' => 'news:13209-machida-shi:zz'], $pdo), 404, 'not_found'));
check('形の違う id は 400', fails(fn() => news_api_item(['id' => 'hyoka:13209-machida-shi:m1'], $pdo), 400, 'invalid_request')
    && fails(fn() => news_api_item(['id' => 'news:../../etc:m1'], $pdo), 400, 'invalid_request'));
check('include_attachments の値を確かめる', fails(fn() => news_api_item(['id' => 'news:13209-machida-shi:m1',
    'include_attachments' => 'maybe'], $pdo), 400, 'invalid_request'));

// 行の位置の守り
check('置き場の外は読まない', news_api_read_line('13209-machida-shi', '../items/2026.jsonl', 0) === null
    && news_api_read_line('..', 'bodies/2026.jsonl', 0) === null && news_api_read_line('13209-machida-shi', 'bodies/2026.jsonl', -1) === null);
// 元のファイルが作り直されて行がずれたら、本文は空にする（ほかの項目の本文を返さない）。
file_put_contents("{$machida}/bodies/2026.jsonl", json_encode(['id' => 'other', 'text' => '別の本文']) . "\n" . str_repeat(' ', 4000) . "\n");
$shifted = news_api_item(['id' => 'news:13209-machida-shi:m1'], $pdo)['item'];
check('行の id が違えば本文を返さない', $shifted['body']['text'] === '');

check('索引が無ければ 503', (static function (): bool {
    putenv('MIYABE_NEWS_INDEX=' . sys_get_temp_dir() . '/no-such-news-index.sqlite');
    try {
        news_api_open();
    } catch (NewsApiError $error) {
        return $error->httpStatus === 503;
    } finally {
        putenv('MIYABE_NEWS_INDEX');
    }
    return false;
})());

$pdo = null;
exit($failures > 0 ? 1 : 0);
