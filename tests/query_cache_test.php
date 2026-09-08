<?php
declare(strict_types=1);

// クエリ前処理のキャッシュを往復させても、Sudachi で切った語が失われないことを見る。
// `php tests/query_cache_test.php` で走る。
//
// 保存しているのは組み立て後の形（語は highlight_terms の名前）なので、読み戻すときに
// tokenizer の返り値と同じ名前へ戻さないと、語を捨てて空白区切りへ落ちる。そうなると
// 「ふるさと納税」のように Sudachi が割る複合語が body_terms と噛み合わず、
// 語単位の検索（multi_match / 近接優先）が当たらなくなる。

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'japanese_search.php';

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

// Sudachi は「ふるさと納税」を「ふるさと」「納税」に割る（本番で確認済み）。
// 索引の body_terms も同じ切り方なので、検索語もこの形でなければ噛み合わない。
$query = 'ふるさと納税 返礼品 ' . bin2hex(random_bytes(4));
$sudachiTerms = ['ふるさと', '納税', '返礼品'];

$path = japanese_search_query_cache_path($query);
@mkdir(dirname($path), 0777, true);
file_put_contents($path, json_encode([
    'raw_query' => $query,
    'fts_query' => '(("ふるさと" OR "古里") AND "納税") AND "返礼品"',
    'highlight_terms' => $sudachiTerms,
    'exact_phrases' => [],
    'query_cache_schema' => 'phrase-v7',
    'tokenizer' => 'sudachi',
], JSON_UNESCAPED_UNICODE));

$prepared = japanese_search_prepare_query($query);
check('キャッシュから読んでも Sudachi の語が残る', $prepared['highlight_terms'] === $sudachiTerms);
check('空白区切りへ落ちていない', !in_array('ふるさと納税', $prepared['highlight_terms'], true));
check('tokenizer の印はキャッシュのものを引き継ぐ', $prepared['tokenizer'] === 'sudachi');
check('fts_query もキャッシュのものを使う', str_contains((string)$prepared['fts_query'], '古里'));

// fallback で作ったキャッシュは使わない（Sudachi が無い環境で作られた可能性がある）。
$fallbackQuery = 'fallback 確認 ' . bin2hex(random_bytes(4));
$fallbackPath = japanese_search_query_cache_path($fallbackQuery);
file_put_contents($fallbackPath, json_encode([
    'raw_query' => $fallbackQuery,
    'fts_query' => 'x',
    'highlight_terms' => ['捨てられるはず'],
    'exact_phrases' => [],
    'query_cache_schema' => 'phrase-v7',
    'tokenizer' => 'fallback',
], JSON_UNESCAPED_UNICODE));
$prepared = japanese_search_prepare_query($fallbackQuery);
check(
    'fallback のキャッシュは使い回さない',
    !in_array('捨てられるはず', $prepared['highlight_terms'], true)
);

@unlink($path);
@unlink($fallbackPath);

// --- 読まれなくなったファイルを捨てる
// ファイル名は検索語とスキーマの hash なので、スキーマを上げると前のものは二度と読まれない。
// TTL の判定は読むときにしか働かず、ファイル自体は残る。本番では 3,880 件のうち有効なのが
// 18 件だけ、という状態になっていた。
$dir = japanese_search_query_cache_dir();
@mkdir($dir, 0777, true);

$stale = $dir . DIRECTORY_SEPARATOR . 'test_stale_' . bin2hex(random_bytes(6)) . '.json';
$fresh = $dir . DIRECTORY_SEPARATOR . 'test_fresh_' . bin2hex(random_bytes(6)) . '.json';
file_put_contents($stale, '{}');
file_put_contents($fresh, '{}');
touch($stale, time() - japanese_search_query_cache_max_age_seconds() - 60);

japanese_search_prune_query_cache();
check('読まれなくなったファイルは捨てる', !is_file($stale));
check('まだ読まれうるファイルは残す', is_file($fresh));

@unlink($stale);
@unlink($fresh);

echo $failures === 0 ? "\nall passed\n" : "\n{$failures} failed\n";
exit($failures === 0 ? 0 : 1);
