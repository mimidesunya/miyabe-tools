<?php
declare(strict_types=1);

// 近接優先ソートの組み立てを検証する。`php tests/proximity_ranking_test.php` で走る。
//
// 近接優先は「しきい値より近い文書を日付順より前に出す」ためのもので、近さ順に
// 並べ替えるものではない。_score が 0/1 の二値になっていること、素の AND 検索に
// だけ効くこと、関連度順では従来どおりであることを見る。

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
    return miyabe_search_build_request($params + ['doc_type' => 'minutes']);
}

// --- 素の AND 検索（日付順）では近接優先が効く
$request = build(['q' => '空き家 対策', 'sort' => 'date']);
$body = $request['body'];
check('素の AND + 日付順で近接優先が有効になる', $request['proximity_ranked'] === true);
check(
    '並びは _score が先、次に sort_date',
    array_key_first($body['sort'][0]) === '_score' && array_key_first($body['sort'][1]) === 'sort_date'
);
$bool = $body['query']['bool'];
check('AND 条件は filter に移り、スコア計算から外れる', !isset($bool['must']) && count($bool['filter']) >= 2);
check(
    'スコアを生むのは近接判定の constant_score だけ',
    count($bool['should']) === 2
        && isset($bool['should'][0]['constant_score'])
        && isset($bool['should'][1]['constant_score'])
);

// 語単位が先、文字単位が後。索引側の分かち書きが古くて複合語が割れている場合でも
// 文字単位が拾うので、近接優先が空振りしない。
$termsScore = $bool['should'][0]['constant_score'];
$charsScore = $bool['should'][1]['constant_score'];
$termsPhrase = $termsScore['filter']['match_phrase'];
$charsPhrase = $charsScore['filter']['match_phrase'];
check('語単位の判定は body_terms を見る', array_key_exists('body_terms', $termsPhrase));
check('文字単位の判定は body（bigram）を見る', array_key_exists('body', $charsPhrase));
check('語単位のしきい値は slop=25', $termsPhrase['body_terms']['slop'] === MIYABE_SEARCH_PROXIMITY_SLOP);
check('文字単位のしきい値は slop=40', $charsPhrase['body']['slop'] === MIYABE_SEARCH_PROXIMITY_CHAR_SLOP);
check('語単位のほうが重い（確かな一致を前に出す）', $termsScore['boost'] > $charsScore['boost']);

// --- 関連度順は従来どおり（元から近い語が上に来るので触らない）
$request = build(['q' => '空き家 対策', 'sort' => 'relevance']);
check('関連度順では近接優先を使わない', $request['proximity_ranked'] === false);
check('関連度順の AND 条件は must のまま', isset($request['body']['query']['bool']['must']));

// --- 語が 1 つなら近接という概念がないので何もしない
$request = build(['q' => '空き家', 'sort' => 'date']);
check('1 語のときは近接優先を使わない', $request['proximity_ranked'] === false);
check(
    '1 語のときの並びは従来どおり sort_date が先',
    array_key_first($request['body']['sort'][0]) === 'sort_date'
);

// --- 演算子つきのクエリでは「近い＝関連が強い」が成り立たない
foreach (
    [
        '空き家 OR 対策' => 'OR 検索',
        '空き家 NOT 対策' => 'NOT 検索',
        '"空き家対策"' => '完全一致フレーズ',
        '(空き家 対策) 助成' => '括弧つき',
        '空き家 -対策' => '除外指定',
        '空き家|対策' => 'パイプの OR',
    ] as $query => $label
) {
    $request = build(['q' => $query, 'sort' => 'date']);
    check("{$label}では近接優先を使わない", $request['proximity_ranked'] === false);
}

// --- 絞り込みと併用しても filter に共存する
$request = build(['q' => '空き家 対策', 'sort' => 'date', 'pref_code' => '13']);
$filter = $request['body']['query']['bool']['filter'];
$hasPref = false;
foreach ($filter as $clause) {
    if (isset($clause['term']['pref_code'])) {
        $hasPref = true;
    }
}
check('都道府県の絞り込みと併用できる', $request['proximity_ranked'] === true && $hasPref);

// --- 結果の score / proximity の見せ方
$hit = ['_id' => 'x', '_score' => 3.0, '_source' => ['title' => 't']];
$item = miyabe_search_hit_to_item($hit, '空き家 対策', true);
check('近接優先では score を関連度として返さない', $item['score'] === null);
check('近接したかどうかは proximity で返す', $item['proximity'] === true);
// 文字単位でだけ当たった文書（score=1.0）も近接扱いにする。
$item = miyabe_search_hit_to_item(['_id' => 'x', '_score' => 1.0, '_source' => []], '空き家 対策', true);
check('文字単位でだけ当たった文書も proximity は true', $item['proximity'] === true);
$item = miyabe_search_hit_to_item(['_id' => 'x', '_score' => 0.0, '_source' => []], '空き家 対策', true);
check('近接しなかった文書の proximity は false', $item['proximity'] === false);
$item = miyabe_search_hit_to_item($hit, '空き家 対策', false);
check('従来モードでは score をそのまま返す', $item['score'] === 3.0 && $item['proximity'] === null);

echo $failures === 0 ? "\nall passed\n" : "\n{$failures} failed\n";
exit($failures === 0 ? 0 : 1);
