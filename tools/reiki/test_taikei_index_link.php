#!/usr/bin/env php
<?php
declare(strict_types=1);

// 中継ページから目次の入口を選ぶところを確かめる。
//
// 横浜市の `reiki_menu.html` は、市のヘッダの画像地図（`<area>`）が目次の
// リンクより前にある。`<a[^>]*` で探していた頃は `<area>` をリンクとみなし、
// 次の `</a>` までの「体系目次」を文言に抱えたまま、サイト内検索のページを
// 入口に選んでいた。目次 1 ページ・例規 0 件で毎回落ちていた。

require_once __DIR__ . DIRECTORY_SEPARATOR . 'scrapers' . DIRECTORY_SEPARATOR . 'taikei.php';

$failures = 0;

function check(string $label, bool $condition): void
{
    global $failures;
    if ($condition) {
        echo "ok   {$label}\n";
        return;
    }
    $failures += 1;
    echo "FAIL {$label}\n";
}

// 取得元には繋がない。渡したページだけを返す。
$siteFetch = static function (array $site): callable {
    return static function (string $url) use ($site): string {
        if (!array_key_exists($url, $site)) {
            throw new RuntimeException("Failed to fetch {$url}: HTTP 404");
        }
        return $site[$url];
    };
};

// 横浜市の形: 画像地図の `<area>` が先、目次のリンクが後。
$yokohamaMenu = 'https://cgi.city.yokohama.lg.jp/somu/reiki/reiki_menu.html';
$yokohamaHtml = '<html><body>'
    . '<img src="/coparts/header.gif/nh02.gif" usemap="#hd02" alt="横浜市ヘッダ">'
    . '<map name="hd02">'
    . '<area id="searchLink" shape="rect" coords="566,0,600,29"  href="https://www.city.yokohama.lg.jp/aboutweb/search/search.html" target="_top" alt="検索へのリンク">'
    . '<area id="homeLink" shape="rect" coords="510,0,564,29"  href="/index.html" target="_top" alt="トップメニューへのリンク">'
    . '<area shape="default" nohref alt="リンク無し">'
    . '</map>'
    . '<h2 class="center">横浜市 例規集<br />（内容現在 令和8年9月1日）</h2>'
    . '<ul class="menu-listA01">'
    . '<li><a href="reiki_taikei/taikei_default.html">体系目次</a></li>'
    . '<li><a href="reiki_kana/kana_default.html">五十音順目次</a></li>'
    . '<li><a href="help/reiki_help.html">ヘルプ</a></li>'
    . '</ul></body></html>';
check(
    '横浜市: 画像地図の <area> を入口に選ばない',
    find_reiki_index_link($yokohamaMenu, 2, $siteFetch([$yokohamaMenu => $yokohamaHtml]))
        === 'https://cgi.city.yokohama.lg.jp/somu/reiki/reiki_taikei/taikei_default.html'
);

// `<a` の直後が改行でもリンクとして読む。
$menu = 'https://www.city.example.jp/reiki/reiki_menu.html';
check(
    '<a の直後が改行でも読む',
    find_reiki_index_link($menu, 2, $siteFetch([
        $menu => "<ul><li><a\nhref=\"reiki_taikei/r_taikei_01.html\">体系目次</a></li></ul>",
    ])) === 'https://www.city.example.jp/reiki/reiki_taikei/r_taikei_01.html'
);

// 「体系」が無ければ、五十音順目次を使う。
check(
    '体系目次が無ければ五十音順目次',
    find_reiki_index_link($menu, 2, $siteFetch([
        $menu => '<a href="help.html">ヘルプ</a><a href="kana/index.html">五十音順目次</a>',
    ])) === 'https://www.city.example.jp/reiki/kana/index.html'
);

// 廿日市市の形: 文言の無い画像リンクが、別ホストの目次を指している。
check(
    '廿日市市: 文言の無い画像リンクでも行き先で選ぶ',
    find_reiki_index_link($menu, 2, $siteFetch([
        $menu => '<a href="https://reiki.example.net/hatsukaichi/reiki_taikei/taikei_default.html"><img src="reiki.png" alt=""></a>',
    ])) === 'https://reiki.example.net/hatsukaichi/reiki_taikei/taikei_default.html'
);

// 釧路町の形: 案内ページは例規サービス側のメニューを指すだけ。1 段辿る。
$relayMenu = 'https://www.g-reiki.example.net/kushiro/reiki_menu.html';
check(
    '釧路町: 例規サービス側のメニューまで辿る',
    find_reiki_index_link('https://www.town.example.jp/reiki.html', 2, $siteFetch([
        'https://www.town.example.jp/reiki.html' => '<a href="' . $relayMenu . '">例規集</a>',
        $relayMenu => '<a href="reiki_taikei/taikei_default.html">体系目次</a>',
    ])) === 'https://www.g-reiki.example.net/kushiro/reiki_taikei/taikei_default.html'
);

// 開けなければ空を返し、呼び出し側が既定の場所へ読み替える。
check('開けなければ空', find_reiki_index_link($menu, 2, $siteFetch([])) === '');

if ($failures > 0) {
    echo "FAILED {$failures}\n";
    exit(1);
}
echo "OK\n";
