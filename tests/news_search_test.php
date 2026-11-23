<?php
declare(strict_types=1);

// お知らせの全文検索の API（lib/news_search.php）。`php tests/news_search_test.php` で走る。
// OpenSearch は手元の固定応答に差し替える。自治体の名前は、Python で作った索引（SQLite）から引く。

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'news_search.php';

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

$root = sys_get_temp_dir() . DIRECTORY_SEPARATOR . 'news-search-test-' . bin2hex(random_bytes(4));
register_shutdown_function(static fn() => remove_tree($root));
$data = $root . DIRECTORY_SEPARATOR . 'data';
mkdir("{$data}/13209-machida-shi/items", 0777, true);
file_put_contents("{$data}/13209-machida-shi/items/2026.jsonl", json_encode([
    'id' => 'm1', 'jis_code' => '13209', 'url' => 'https://www.city.machida.tokyo.jp/m1.html', 'title' => '入札の公表',
    'date' => '2026-11-13', 'first_seen_at' => '2026-11-13 10:00:00', 'revision' => 0,
], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . "\n");
$work = $root . DIRECTORY_SEPARATOR . 'work';
$python = getenv('MIYABE_PYTHON_BIN') ?: (PHP_OS_FAMILY === 'Windows' ? 'python' : 'python3');
exec(escapeshellarg($python) . ' ' . escapeshellarg(dirname(__DIR__) . '/tools/news/news_index.py')
    . ' --data-root ' . escapeshellarg($data) . ' --work-root ' . escapeshellarg($work) . ' 2>&1', $output, $exitCode);
if ($exitCode !== 0) {
    fwrite(STDERR, implode("\n", $output) . "\n");
    exit(1);
}
putenv('MIYABE_NEWS_INDEX=' . $work . DIRECTORY_SEPARATOR . 'index.sqlite');
putenv('MIYABE_NEWS_DATA_ROOT=' . $data);
$pdo = news_api_open();

// 条件を本文に移す
$request = news_search_build_request(['q' => 'ごみ', 'municipality' => '東京都町田市', 'since' => '2026-11-01', 'until' => '2026-11-14',
    'part' => 'attachment', 'page' => '2', 'per_page' => '5'], $pdo);
$filters = $request['body']['query']['bool']['filter'];
check('重複して隠したものを外す', in_array(['term' => ['hidden' => false]], $filters, true));
check('自治体は自治体コードで絞る', in_array(['term' => ['municipality_code' => '13209']], $filters, true));
check('期間は並べる日付で絞る', in_array(['range' => ['sort_date' => ['gte' => '2026-11-01', 'lte' => '2026-11-14']]], $filters, true));
check('part で添付だけにする', in_array(['term' => ['part' => 'attachment']], $filters, true));
check('頁送り', $request['body']['from'] === 5 && $request['body']['size'] === 5);
check('お知らせ 1 件にまとめる', $request['body']['collapse']['field'] === 'item_key'
    && $request['body']['collapse']['inner_hits']['name'] === 'parts');
check('題名の強調はまとめた当たりに、本文の抜粋は文書ごとに付ける',
    array_keys($request['body']['highlight']['fields']) === ['title']
    && array_keys($request['body']['collapse']['inner_hits']['highlight']['fields']) === ['body']);
check('1 語の検索は日付の新しい順が先', $request['proximity_ranked'] === false && array_keys($request['body']['sort'][0])[0] === 'sort_date');
$relevance = news_search_build_request(['q' => 'ごみ', 'sort' => 'relevance'], $pdo);
check('関連度順', array_keys($relevance['body']['sort'][0])[0] === '_score');
$pref = news_search_build_request(['q' => 'ごみ', 'pref_code' => '13'], $pdo);
check('都道府県で絞る', in_array(['term' => ['pref_code' => '13']], $pref['body']['query']['bool']['filter'], true)
    && $pref['municipality'] === null);
check('自治体を指定すれば都道府県の条件は要らない', !in_array(['term' => ['pref_code' => '13']],
    news_search_build_request(['q' => 'ごみ', 'municipality' => '町田市', 'pref_code' => '13'], $pdo)['body']['query']['bool']['filter'], true));
$and = news_search_build_request(['q' => 'ごみ 有料化'], $pdo);
check('2 語の AND は、近くに現れた文書を先に（日付順のとき）', $and['proximity_ranked'] === true
    && array_keys($and['body']['sort'][0])[0] === '_score'
    && isset($and['body']['query']['bool']['should'][0]['constant_score']));
check('演算子のある検索は近さで並べない', news_search_build_request(['q' => 'ごみ OR 有料化'], $pdo)['proximity_ranked'] === false);
$fields = $and['body']['query']['bool']['filter'][1]['bool']['should'][0]['simple_query_string']['fields'];
check('概要とリンクの文字も引く', in_array('summary^2', $fields, true) && in_array('link_text^2', $fields, true));
check('事務事業評価の検索の項目は変わらない', in_array('heading^2',
    miyabe_search_build_query_clause('ごみ', 'hyoka')['must'][0]['bool']['should'][0]['simple_query_string']['fields'], true));
check('会議録の検索の項目は変わらない', !in_array('summary^2',
    miyabe_search_build_query_clause('ごみ', 'minutes')['must'][0]['bool']['should'][0]['simple_query_string']['fields'], true));

// 条件の検査
check('q が無い・空白だけは断る', fails(fn() => news_search_build_request([], $pdo), 400, 'invalid_request')
    && fails(fn() => news_search_build_request(['q' => "　 "], $pdo), 400, 'invalid_request'));
check('長すぎる q は断る', fails(fn() => news_search_build_request(['q' => str_repeat('あ', 201)], $pdo), 400, 'invalid_request'));
check('sort・part・per_page・page の値を確かめる',
    fails(fn() => news_search_build_request(['q' => 'x', 'sort' => 'z'], $pdo), 400, 'invalid_request')
    && fails(fn() => news_search_build_request(['q' => 'x', 'part' => 'z'], $pdo), 400, 'invalid_request')
    && fails(fn() => news_search_build_request(['q' => 'x', 'per_page' => '31'], $pdo), 400, 'invalid_request')
    && fails(fn() => news_search_build_request(['q' => 'x', 'page' => '400', 'per_page' => '30'], $pdo), 400, 'invalid_request'));
check('壊れた日付・逆の期間は断る', fails(fn() => news_search_build_request(['q' => 'x', 'since' => '2026-02-30'], $pdo), 400, 'invalid_request')
    && fails(fn() => news_search_build_request(['q' => 'x', 'since' => '2026-11-14', 'until' => '2026-11-01'], $pdo), 400, 'invalid_request'));
check('同じ名前の自治体は候補を返す', fails(fn() => news_search_build_request(['q' => 'x', 'municipality' => '伊達市'], $pdo), 422, 'ambiguous_municipality'));

// 応答の形
$hit = [
    '_id' => 'news:13209-machida-shi:a:1111111111111111',
    '_source' => ['item_key' => 'news:13209-machida-shi:m1', 'slug' => '13209-machida-shi', 'municipality_code' => '13209',
        'pref_code' => '13', 'pref_name' => '東京都', 'municipality_name' => '町田市', 'title' => '入札の公表',
        'url' => 'https://www.city.machida.tokyo.jp/m1.html', 'date' => '2026-11-13', 'sort_date' => '2026-11-13',
        'first_seen_at' => '2026-11-13 10:00:00', 'categories' => ['入札']],
    'highlight' => ['title' => ['[[[入札]]]の公表']],
    'inner_hits' => ['parts' => ['hits' => ['hits' => [
        ['_source' => ['part' => 'attachment', 'attachment_url' => 'https://www.city.machida.tokyo.jp/a.pdf', 'link_text' => '一覧表',
            'kind' => 'pdf', 'pages' => 3], 'highlight' => ['body' => ["件名\n[[[ごみ]]]収集", '…有料化']]],
        ['_source' => ['part' => 'item', 'kind' => 'html'], 'highlight' => []],
    ]]]],
];
$calls = [];
$http = static function (string $method, string $path, array $body) use (&$calls, $hit): array {
    $calls[] = [$method, $path, $body];
    return ['hits' => ['hits' => [$hit, ['_source' => ['item_key' => '../../x']]]], 'aggregations' => ['items' => ['value' => 12]]];
};
$result = news_search_execute(['q' => 'ごみ', 'municipality_code' => '13209'], $pdo, $http);
check('OpenSearch の別名 miyabe-news-current を引く', $calls[0][0] === 'POST' && $calls[0][1] === '/miyabe-news-current/_search');
check('お知らせの数と並べ方を返す', $result['total'] === 12 && $result['total_relation'] === 'eq' && $result['sort'] === 'date'
    && $result['municipality']['municipality_name'] === '町田市' && json_encode($result['filters']) === '{}');
check('形の違う id の当たりは捨てる', count($result['items']) === 1);
$item = $result['items'][0];
check('添付が当たっても、お知らせの id を返す', $item['id'] === 'news:13209-machida-shi:m1'
    && $item['api_item_url'] === '/api/news-item.php?id=news%3A13209-machida-shi%3Am1');
check('当たった文書の種類と抜粋', $item['matches'][0] === ['part' => 'attachment', 'link_text' => '一覧表',
    'attachment_url' => 'https://www.city.machida.tokyo.jp/a.pdf', 'kind' => 'pdf', 'pages' => 3,
    'excerpt' => '件名 [[[ごみ]]]収集 … …有料化', 'excerpt_has_hit' => true]
    && $item['matches'][1]['part'] === 'page' && $item['matches'][1]['attachment_url'] === null
    && $item['matches'][1]['excerpt_has_hit'] === false);
check('題名の強調', $item['title_highlight'] === '[[[入札]]]の公表');
$large = news_search_shape_response(['aggregations' => ['items' => ['value' => 20000]]], $request);
check('1 万を超える数は概数と分かる', $large['total_relation'] === 'approximate');
putenv('MIYABE_NEWS_ALIAS=miyabe-news-check');
check('別名は環境で変えられる', (static function () use ($pdo): bool {
    $path = '';
    news_search_execute(['q' => 'ごみ'], $pdo, static function (string $method, string $requested) use (&$path): array {
        $path = $requested;
        return [];
    });
    return $path === '/miyabe-news-check/_search';
})());
putenv('MIYABE_NEWS_ALIAS');

$pdo = null;
exit($failures > 0 ? 1 : 0);
