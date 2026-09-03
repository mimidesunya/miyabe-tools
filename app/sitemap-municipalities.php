<?php
declare(strict_types=1);

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'municipalities.php';
require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'site_assets.php';

// 自治体ごとの入口を検索エンジンに知らせる。
// 本文ページ（/search/detail/）は数十万件あって列挙できないので、ここでは
// 自治体単位の入口だけを出す。そこから先はクローラが辿る。

header('Content-Type: application/xml; charset=UTF-8');
header('Cache-Control: public, max-age=3600');

$base = site_base_url();
$entries = [];
foreach (municipality_switcher_items('reiki') as $item) {
    if (empty($item['enabled'])) {
        continue;
    }
    $slug = (string)($item['slug'] ?? '');
    if ($slug === '') {
        continue;
    }
    // 例規集は画面そのものが本文を持つので、自治体ごとに 1 本ずつ載せる。
    $entries[] = $base . '/reiki/?slug=' . rawurlencode($slug);
}
foreach (municipality_switcher_items('gijiroku') as $item) {
    if (empty($item['enabled'])) {
        continue;
    }
    $slug = (string)($item['slug'] ?? '');
    if ($slug === '') {
        continue;
    }
    // 会議録は検索画面が入口。本文は /search/detail/ にあり、そこから辿れる。
    $entries[] = $base . '/search/?doc_type=minutes&slug=' . rawurlencode($slug);
}

echo '<?xml version="1.0" encoding="UTF-8"?>' . "\n";
echo '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' . "\n";
foreach ($entries as $url) {
    echo "  <url>\n";
    echo '    <loc>' . htmlspecialchars($url, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') . "</loc>\n";
    echo "    <changefreq>weekly</changefreq>\n";
    echo "    <priority>0.5</priority>\n";
    echo "  </url>\n";
}
echo '</urlset>' . "\n";
