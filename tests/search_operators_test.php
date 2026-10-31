<?php
declare(strict_types=1);

// 検索語の OR・NOT・括弧が OpenSearch へ正しく渡るかを見る。`php tests/search_operators_test.php` で走る。
//
// simple_query_string は語の OR・NOT を演算子として読まないので、記号（| と -）に直して渡す。
// 語単位の句は語を全部 AND で並べるだけなので、演算子があるときは足さない。

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

// --- 語の演算子を記号に直す
foreach (
    [
        '同和 OR 部落' => '同和 | 部落',
        '同和 or 部落' => '同和 | 部落',
        'メガソーラー NOT 促進' => 'メガソーラー -促進',
        '(盛土 OR メガソーラー) 条例' => '(盛土 | メガソーラー) 条例',
        '盛土 AND 土砂' => '盛土 土砂',
        '温泉 NOT "同和団体"' => '温泉 -"同和団体"',
        '条例 NOT (盛土 OR 土砂)' => '条例 -(盛土 | 土砂)',
        '"盛土 OR 土砂" 条例' => '"盛土 OR 土砂" 条例',
        '同和OR部落' => '同和OR部落',
        'ORANGE NOTE' => 'ORANGE NOTE',
        '空き家 対策' => '空き家 対策',
    ] as $query => $expected
) {
    $actual = miyabe_search_simple_query_syntax($query);
    check("「{$query}」を「{$expected}」にする", $actual === $expected);
}

// --- 演算子の有無
check('OR は演算子', miyabe_search_query_has_operators('同和 OR 部落'));
check('NOT は演算子', miyabe_search_query_has_operators('同和 NOT 部落'));
check('先頭の - は演算子', miyabe_search_query_has_operators('同和 -部落'));
check('| は演算子', miyabe_search_query_has_operators('同和|部落'));
check('括弧は演算子', miyabe_search_query_has_operators('(同和 部落) 予算'));
check('素の AND は演算子なし', !miyabe_search_query_has_operators('同和 部落'));
check('引用符だけは演算子なし', !miyabe_search_query_has_operators('"同和団体" 温泉'));
check('引用符の中の OR は演算子にしない', !miyabe_search_query_has_operators('"盛土 OR 土砂"'));
check('語の途中の - は演算子にしない', !miyabe_search_query_has_operators('COVID-19 対策'));

// --- 組み立てた検索
function query_should(string $query): array
{
    $request = miyabe_search_build_request(['q' => $query, 'doc_type' => 'hyoka', 'sort' => 'relevance']);
    return $request['body']['query']['bool']['must'][0]['bool']['should'];
}

function has_clause(array $should, string $name): bool
{
    foreach ($should as $clause) {
        if (isset($clause[$name])) {
            return true;
        }
    }
    return false;
}

$should = query_should('同和 OR 部落');
check('OR 検索は simple_query_string に | で渡す', $should[0]['simple_query_string']['query'] === '同和 | 部落');
check('OR 検索に語の AND の句を足さない', !has_clause($should, 'multi_match'));

$should = query_should('同和 NOT 部落');
check('NOT 検索は simple_query_string に - で渡す', $should[0]['simple_query_string']['query'] === '同和 -部落');
check('NOT 検索に語の AND の句を足さない（外した文書が戻らない）', !has_clause($should, 'multi_match'));

check('- の除外にも語の AND の句を足さない', !has_clause(query_should('同和 -部落'), 'multi_match'));

$should = query_should('同和 部落');
check('素の AND はそのまま渡す', $should[0]['simple_query_string']['query'] === '同和 部落');
check('素の AND は語の句も使う（表記の揺れを拾う）', has_clause($should, 'multi_match'));
check('引用符つきの AND も語の句を使う', has_clause(query_should('"同和団体" 温泉'), 'multi_match'));

echo $failures === 0 ? "\nall passed\n" : "\n{$failures} failed\n";
exit($failures === 0 ? 0 : 1);
