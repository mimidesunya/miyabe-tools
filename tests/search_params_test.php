<?php
declare(strict_types=1);

// 検索 API の条件の受け取り方を見る。`php tests/search_params_test.php` で走る。
//
// 打ち間違いは黙って無視せず断ること、深い頁は OpenSearch へ送る前に断ること、
// 自治体コードの 6 桁や年の逆指定を正しく読むこと、score を説明どおりに返すことを確かめる。

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'opensearch_search.php';

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

function build(array $params): array
{
    return miyabe_search_build_request($params + ['q' => '予算', 'doc_type' => 'minutes']);
}

function filters_of(array $request): array
{
    return $request['body']['query']['bool']['filter'] ?? [];
}

function filter_value(array $request, string $field): mixed
{
    foreach (filters_of($request) as $clause) {
        if (isset($clause['term'][$field])) {
            return $clause['term'][$field];
        }
    }
    return null;
}

function date_range(array $request): ?array
{
    foreach (filters_of($request) as $clause) {
        if (isset($clause['range']['sort_date'])) {
            return $clause['range']['sort_date'];
        }
    }
    return null;
}

// --- 打ち間違いは断る
check('正しい条件なら断らない', build(['sort' => 'relevance', 'pref_code' => '14', 'start_date' => '2020-01-01'])['query_error'] === '');
check('sort の打ち間違いを断る', build(['sort' => 'foo'])['query_error'] !== '');
check('空の sort は日付順', build(['sort' => ''])['query_error'] === '' && build(['sort' => ''])['relevance_sorted'] === false);
check('日付の書き方の誤りを断る', build(['start_date' => '2020/01/01'])['query_error'] !== '');
check('無い日付を断る', build(['end_date' => '2024-02-30'])['query_error'] !== '');
check('年の誤りを断る', build(['start_year' => 'abc'])['query_error'] !== '');
check('都道府県コードの誤りを断る', build(['pref_code' => 'abc'])['query_error'] !== '');
check('48 以上の都道府県コードを断る', build(['pref_code' => '48'])['query_error'] !== '');
check('1 桁の都道府県コードは 0 を補う', filter_value(build(['pref_code' => '9']), 'pref_code') === '09');
check('事務事業評価では日付を見ない（断らない）', build(['doc_type' => 'hyoka', 'start_date' => '2020/01/01'])['query_error'] === '');

// --- 自治体コード
check('5 桁はそのまま', filter_value(build(['municipality_code' => '14216']), 'municipality_code') === '14216');
check('検査数字つきの 6 桁は先頭 5 桁にする', filter_value(build(['municipality_code' => '142166']), 'municipality_code') === '14216');
check('5・6 桁でない自治体コードを断る', build(['municipality_code' => '1421'])['query_error'] !== '');

// --- 深い頁
check('10,000 件目までの頁は断らない', build(['page' => 500, 'per_page' => 20])['query_error'] === '');
check('10,000 件目より先の頁は送る前に断る', build(['page' => 501, 'per_page' => 20])['query_error'] !== '');
check('件数を増やしても上限は同じ', build(['page' => 101, 'per_page' => 100])['query_error'] !== '');

// --- 年の範囲
check('年の範囲は年の初めから終わりまで', date_range(build(['start_year' => '2020', 'end_year' => '2024'])) === ['gte' => '2020-01-01', 'lte' => '2024-12-31']);
check('逆に指定した年は入れ替えて同じ範囲にする', date_range(build(['start_year' => '2024', 'end_year' => '2020'])) === ['gte' => '2020-01-01', 'lte' => '2024-12-31']);
check('日付と年の逆指定も入れ替える', date_range(build(['start_date' => '2024-05-01', 'end_year' => '2020'])) === ['gte' => '2020-01-01', 'lte' => '2024-05-01']);
check('逆に指定した日付は入れ替える', date_range(build(['start_date' => '2024-05-01', 'end_date' => '2020-03-01'])) === ['gte' => '2020-03-01', 'lte' => '2024-05-01']);

// --- NEAR は AND として外す
check('NEAR を外す', miyabe_search_simple_query_syntax('盛土 NEAR 条例') === '盛土 条例');
check('NEAR/5 を外す', miyabe_search_simple_query_syntax('盛土 NEAR/5 条例') === '盛土 条例');

// --- score
$hit = ['_id' => 'x', '_score' => 3.0, '_source' => ['title' => 't']];
check('関連度順では score を返す', miyabe_search_hit_to_item($hit, '予算', false, true)['score'] === 3.0);
check('日付順では score を返さない', miyabe_search_hit_to_item($hit, '予算', false, false)['score'] === null);
check('ID で引いた文書は score を返さない', miyabe_search_hit_to_detail($hit)['score'] === null);
check('関連度順の要求は印が付く', build(['sort' => 'relevance'])['relevance_sorted'] === true);

echo $failures === 0 ? "\nall passed\n" : "\n{$failures} failed\n";
exit($failures === 0 ? 0 : 1);
