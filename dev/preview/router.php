<?php
declare(strict_types=1);

// 手元で画面を確かめるための入口。画面は手元のファイルで出し、データは本番の公開 API から借りる。
//
//     php -S 127.0.0.1:18091 -t app dev/preview/router.php
//     php -S 127.0.0.1:18092 dev/preview/opensearch_stub.php   （詳細画面を見るときだけ）
//
// - /api/ への GET は https://tools.miya.be の同じ道へ取りに行き、10 分だけ手元に控える。
//   公開している API を読むだけで、本番に書き込むことは無い。
// - 詳細画面（/search/detail/）は PHP が OpenSearch を直接読むので、2 つ目の入口
//   （opensearch_stub.php）へ向ける。そちらが公開 API の /api/document から 1 件を作る。
// - 本番の nginx の書き換えのうち、画面を見るのに要るものだけ真似る。

const PREVIEW_UPSTREAM = 'https://tools.miya.be';
const PREVIEW_CACHE_SECONDS = 600;

putenv('OPENSEARCH_URL=http://127.0.0.1:18092');

$uri = (string)($_SERVER['REQUEST_URI'] ?? '/');
$path = (string)(parse_url($uri, PHP_URL_PATH) ?: '/');

// お知らせの API は手元の索引（work/news/index.sqlite）と data/news を読む。
// 手元で `python tools/news/fetch_news_feeds.py --code …` と `news_index.py` を流してから見る。
if (in_array($path, ['/api/news.php', '/api/news-item.php'], true)) {
    return false;
}

if (str_starts_with($path, '/api/')) {
    if (($_SERVER['REQUEST_METHOD'] ?? 'GET') !== 'GET') {
        http_response_code(405);
        header('Content-Type: text/plain; charset=utf-8');
        echo "プレビューは GET だけを本番へ取りに行きます。\n";
        return true;
    }
    $cacheDir = sys_get_temp_dir() . DIRECTORY_SEPARATOR . 'miyabe-preview-cache';
    if (!is_dir($cacheDir)) {
        mkdir($cacheDir, 0700, true);
    }
    $cacheFile = $cacheDir . DIRECTORY_SEPARATOR . sha1($uri) . '.json';
    $cached = is_file($cacheFile) && (time() - filemtime($cacheFile)) < PREVIEW_CACHE_SECONDS
        ? json_decode((string)file_get_contents($cacheFile), true)
        : null;
    if (!is_array($cached)) {
        $context = stream_context_create(['http' => [
            'method' => 'GET',
            'timeout' => 30,
            'ignore_errors' => true,
            'header' => "User-Agent: miyabe-tools-preview\r\nAccept: application/json\r\n",
        ]]);
        $body = @file_get_contents(PREVIEW_UPSTREAM . $uri, false, $context);
        $status = 502;
        $type = 'application/json; charset=utf-8';
        foreach ($http_response_header ?? [] as $line) {
            if (preg_match('#^HTTP/\S+\s+(\d{3})#', $line, $m) === 1) {
                $status = (int)$m[1];
            } elseif (stripos($line, 'Content-Type:') === 0) {
                $type = trim(substr($line, 13));
            }
        }
        $cached = ['status' => $status, 'type' => $type, 'body' => base64_encode($body === false ? '' : $body)];
        if ($body !== false && $status === 200) {
            file_put_contents($cacheFile, json_encode($cached));
        }
    }
    http_response_code((int)$cached['status']);
    header('Content-Type: ' . (string)$cached['type']);
    header('X-Preview-Upstream: ' . PREVIEW_UPSTREAM);
    echo base64_decode((string)$cached['body']);
    return true;
}

// /boards/<slug>/ は index.php?slug= へ（nginx の書き換えと同じ）。
if (preg_match('#^/boards/([a-z0-9_-]+)/?$#', $path, $m) === 1) {
    $_GET['slug'] = $m[1];
    require dirname(__DIR__, 2) . '/app/boards/index.php';
    return true;
}

return false;
