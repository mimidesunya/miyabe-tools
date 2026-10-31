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

// --- 演算子つきでも語単位の句は演算子どおりに使う（複合語の当たり方を素の検索とそろえる）
function terms_clause(array $should): ?string
{
    foreach ($should as $clause) {
        if (isset($clause['simple_query_string']) && in_array('body_terms', $clause['simple_query_string']['fields'], true)) {
            return $clause['simple_query_string']['query'];
        }
    }
    return null;
}

$termsQuery = terms_clause(query_should('同和団体 NOT 部落'));
check('NOT 検索の語単位の句は Sudachi の語で引き、NOT も効かせる', $termsQuery === null || $termsQuery === '(同和 団体) -部落');
check('語句があるときは語単位の句を使わない', terms_clause(query_should('"同和団体" OR 温泉')) === null);

// --- 語句と演算子
function must_count(string $query): int
{
    $request = miyabe_search_build_request(['q' => $query, 'doc_type' => 'hyoka', 'sort' => 'relevance']);
    return count($request['body']['query']['bool']['must']);
}
check('-"語句" を「必ず含む語句」にしない', must_count('温泉 -"同和団体"') === 1);
check('"語句" | 語 で語句を必ず含むにしない', must_count('"同和団体" | 温泉') === 1);
check('演算子の無い語句は必ず含む', must_count('"同和団体" 温泉') === 2);

// --- 記号の読み方
$should = query_should('同和 部落');
check('あいまい検索と語句の距離の記号は読ませない', $should[0]['simple_query_string']['flags'] === 'AND|OR|NOT|PHRASE|PRECEDENCE|PREFIX|WHITESPACE');
check('索引に無い body.ngram を引かない', !in_array('body.ngram', $should[0]['simple_query_string']['fields'], true));
check('閉じ括弧の直後の OR も演算子', miyabe_search_simple_query_syntax('(条例 盛土)OR メガソーラー') === '(条例 盛土)| メガソーラー');

// --- 全角
check('全角の NOT を演算子として読む', query_should('メガソーラー ＮＯＴ 促進')[0]['simple_query_string']['query'] === 'メガソーラー -促進');
check('全角の OR を演算子として読む', query_should('盛土 ＯＲ 土砂')[0]['simple_query_string']['query'] === '盛土 | 土砂');
check('全角の括弧を演算子として読む', query_should('条例 （盛土 OR メガソーラー）')[0]['simple_query_string']['query'] === '条例 (盛土 | メガソーラー)');
check('曲がった引用符を語句として読む', must_count('“同和団体”') === 2);
$blank = miyabe_search_build_request(['q' => '　', 'doc_type' => 'hyoka']);
check('全角空白だけの検索語は空として断る', $blank['query'] === '');

// --- そのほかの不正な条件
check('長すぎる検索語を断る', miyabe_search_build_request(['q' => str_repeat('あ', 201)])['query_error'] !== '');
check('200 字までは断らない', miyabe_search_build_request(['q' => str_repeat('あ', 200)])['query_error'] === '');
check('見つからない slug を断る', miyabe_search_build_request(['q' => '予算', 'slug' => 'no-such-place'])['query_error'] !== '');
check('slug の別名は断らない', miyabe_search_build_request(['q' => '予算', 'slug' => '14130'])['query_error'] === '');
check('doc_type の打ち間違いを断る', miyabe_search_build_request(['q' => '予算', 'doc_type' => 'hyouka'])['query_error'] !== '');

echo $failures === 0 ? "\nall passed\n" : "\n{$failures} failed\n";
exit($failures === 0 ? 0 : 1);
