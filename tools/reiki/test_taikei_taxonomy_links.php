#!/usr/bin/env php
<?php
declare(strict_types=1);

// 空白入りのリンクが指す目次の枝を、どちらの綴りでも開けることを確かめる。
//
// 与那国町は `r_taikei_13_08_01_ 1.html` と書いて実体は空白の無い名前、
// 阿久根市は同じ書き方で実体の名前にも空白がある（`%201` で 200、空白を
// 落とすと 404）。空白を落とす一方だった頃は、阿久根市の 4 枝が毎回
// 「taxonomy page unavailable」になっていた。

require_once __DIR__ . DIRECTORY_SEPARATOR . 'scrapers' . DIRECTORY_SEPARATOR . 'taikei.php';

// 取得元には繋がないので、1 件ごとの間隔は待たない。
taikei_throttle_scale(0.0);

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

$base = 'https://www.city.example.jp/section/reiki/reiki_taikei/';
$page = $base . 'r_taikei_07_01.html';

// 候補は「空白を落とした名前」が先、書かれたとおりの名前が次。
$candidates = taxonomy_link_candidates('r_taikei_07_01_05_ 1.html', $page);
check('空白を落とした名前が本命', ($candidates[0] ?? '') === $base . 'r_taikei_07_01_05_1.html');
check('書かれたとおりの名前も候補に残す', ($candidates[1] ?? '') === $base . 'r_taikei_07_01_05_%201.html');
check('空白が無いリンクは候補が 1 つ', taxonomy_link_candidates('r_taikei_07_01.html', $page) === [$page]);

$html = '<html><body>'
    . '<a href="r_taikei_07_01_05_ 1.html">第5節</a>'
    . '<a href="r_taikei_07_02.html">第2章</a>'
    . '<a href="../reiki_honbun/q707RG00000282.html">本文</a>'
    . '</body></html>';
$xpath = new DOMXPath(create_dom($html));
$links = extract_taxonomy_links($xpath, $page);
check('枝は本命の URL で並ぶ', array_keys($links) === [
    $base . 'r_taikei_07_01_05_1.html',
    $base . 'r_taikei_07_02.html',
]);
check('空白入りの枝には別の綴りが付く', ($links[$base . 'r_taikei_07_01_05_1.html'] ?? []) === [
    $base . 'r_taikei_07_01_05_%201.html',
]);
check('空白の無い枝には別の綴りが無い', ($links[$base . 'r_taikei_07_02.html'] ?? null) === []);

// 取得元の姿を差し替えて、実際に辿れるかを見る。
$row = static fn(string $code, string $title): string =>
    '<tr><td><a href="../reiki_honbun/' . $code . '.html">' . $title . '</a></td>'
    . '<td>◆平成5年9月22日</td><td>規則第11号</td></tr>';
$table = static fn(string $path, string $rows): string =>
    '<table class="scrollableA01"><tbody><tr><td>' . $path . '</td><td></td><td></td></tr>'
    . $rows . '</tbody></table>';

$crawl = static function (array $site): array {
    return crawl_taxonomy(
        array_key_first($site),
        static function (string $url) use ($site): string {
            if (!array_key_exists($url, $site)) {
                throw new RuntimeException("Failed to fetch {$url}: HTTP 404");
            }
            return $site[$url];
        }
    );
};

// 阿久根市の形: 実体の名前にも空白がある。
$akune = $crawl([
    $page => '<a href="r_taikei_07_01_05_ 1.html">第5節</a>',
    $base . 'r_taikei_07_01_05_%201.html' => $table('第5節', $row('q707RG00000282', '老人福祉法施行細則')),
]);
check('阿久根市: 空白入りの実体を開けた', $akune['missed'] === []);
check('阿久根市: その枝の例規を拾えた', count($akune['records']) === 1);

// 与那国町の形: 実体は空白の無い名前。これまでどおり 1 回で開ける。
$yonaguni = $crawl([
    $page => '<a href="r_taikei_07_01_05_ 1.html">第5節</a>',
    $base . 'r_taikei_07_01_05_1.html' => $table('第5節', $row('q707RG00000283', '在宅福祉アドバイザー要綱')),
]);
check('与那国町: 空白を落とした実体を開けた', $yonaguni['missed'] === []);
check('与那国町: その枝の例規を拾えた', count($yonaguni['records']) === 1);

// どちらも無ければ、開けなかった枝として 1 つだけ数える。
$neither = $crawl([
    $page => '<a href="r_taikei_07_01_05_ 1.html">第5節</a>',
]);
check('どちらも無い: 開けなかった枝は 1 つ', $neither['missed'] === [$base . 'r_taikei_07_01_05_1.html']);

if ($failures > 0) {
    echo "FAILED {$failures}\n";
    exit(1);
}
echo "OK\n";
