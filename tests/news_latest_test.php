<?php
declare(strict_types=1);

// トップページの「各地のお知らせ」の並べ方を見る。`php tests/news_latest_test.php` で走る。
//
// 都道府県の指定を 01〜47 と all だけ受けること、http(s) 以外のリンクを出さないこと、
// 題名を必ずエスケープすること、全国の一覧だけ都道府県名を付けることを確かめる。

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'news.php';

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

check('all を受ける', news_latest_scope('all') === 'all');
check('空は all', news_latest_scope(null) === 'all' && news_latest_scope('') === 'all');
check('01 と 47 を受ける', news_latest_scope('01') === '01' && news_latest_scope('47') === '47');
check('00・48・1・../all は断る', news_latest_scope('00') === null && news_latest_scope('48') === null
    && news_latest_scope('1') === null && news_latest_scope('../all') === null);
check('配列は断る', news_latest_scope(['13']) === null);
check('読めない scope は null', news_latest_load('../x') === null);

$item = ['date' => '2026-11-04', 'title' => '<script>注意</script>', 'url' => 'https://www.example.jp/a?x=1&y=2',
    'name' => '札幌市', 'pref_name' => '北海道'];
$html = news_render_items(['scope' => 'all', 'items' => [
    $item,
    ['date' => '2026-11-04', 'title' => '危ないリンク', 'url' => 'javascript:alert(1)', 'name' => '札幌市', 'pref_name' => '北海道'],
    ['date' => '2026-11-04', 'title' => '道の項目', 'url' => 'https://www.pref.example.jp/', 'name' => '北海道', 'pref_name' => '北海道'],
    ['date' => '11月4日', 'title' => '日付が読めない', 'url' => 'https://www.example.jp/', 'name' => '札幌市', 'pref_name' => '北海道'],
]]);
check('題名をエスケープする', str_contains($html, '&lt;script&gt;注意&lt;/script&gt;') && !str_contains($html, '<script>'));
check('URL の & をエスケープする', str_contains($html, 'href="https://www.example.jp/a?x=1&amp;y=2"'));
check('javascript: は出さない', !str_contains($html, 'javascript:') && !str_contains($html, '危ないリンク'));
check('日付の読めない項目は出さない', !str_contains($html, '日付が読めない'));
check('日付は 11/4', str_contains($html, '<time datetime="2026-11-04">11/4</time>'));
check('全国では都道府県名を付ける', str_contains($html, '北海道 札幌市'));
check('都道府県そのものは名前 1 つ', str_contains($html, '<span class="news-place">北海道</span>'));

$prefHtml = news_render_items(['scope' => '01', 'items' => [$item]]);
check('都道府県の一覧では付けない', str_contains($prefHtml, '<span class="news-place">札幌市</span>'));
check('空なら空と書く', str_contains(news_render_items(['scope' => '13', 'items' => []]), 'この 7 日のお知らせはありません'));
check('更新の時刻', news_render_updated(['generated_at' => '2026-11-14 16:05:00']) === '11月14日 16:05 更新');
check('時刻が無ければ空', news_render_updated([]) === '');

if ($failures > 0) {
    fwrite(STDERR, "{$failures} failure(s)\n");
    exit(1);
}
echo "all passed\n";
