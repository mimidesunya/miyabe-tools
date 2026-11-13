<?php
declare(strict_types=1);

// トップページの「各地のお知らせ」。
// 一覧は取得の worker が 3 時間おきに work/news/latest/ へ書く（tools/news/latest_news.py）。
// ここではそれを読んで並べるだけで、項目の記録には触らない。

function news_latest_dir(): string
{
    return dirname(__DIR__) . DIRECTORY_SEPARATOR . 'work' . DIRECTORY_SEPARATOR . 'news' . DIRECTORY_SEPARATOR . 'latest';
}

// 'all' か都道府県コード（01〜47）。それ以外は null。
function news_latest_scope(mixed $value): ?string
{
    if ($value === null || $value === '' || $value === 'all') {
        return 'all';
    }
    if (is_string($value) && preg_match('/^(0[1-9]|[1-3][0-9]|4[0-7])$/', $value) === 1) {
        return $value;
    }
    return null;
}

function news_latest_load(string $scope): ?array
{
    if (news_latest_scope($scope) !== $scope) {
        return null;
    }
    $path = news_latest_dir() . DIRECTORY_SEPARATOR . $scope . '.json';
    if (!is_file($path)) {
        return null;
    }
    $raw = @file_get_contents($path);
    if (!is_string($raw)) {
        return null;
    }
    $payload = json_decode($raw, true);
    if (!is_array($payload) || !is_array($payload['items'] ?? null)) {
        return null;
    }
    return $payload;
}

function news_h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

// 全国の一覧では都道府県名を付ける。都道府県そのものの項目は名前が 1 つで済む。
function news_item_place(array $item, bool $withPrefecture): string
{
    $name = (string)($item['name'] ?? '');
    $prefecture = (string)($item['pref_name'] ?? '');
    if (!$withPrefecture || $prefecture === '' || $prefecture === $name) {
        return $name;
    }
    return $prefecture . ' ' . $name;
}

function news_render_items(array $payload): string
{
    $withPrefecture = ($payload['scope'] ?? 'all') === 'all';
    $html = '';
    foreach ($payload['items'] as $item) {
        if (!is_array($item)) {
            continue;
        }
        $url = (string)($item['url'] ?? '');
        $title = (string)($item['title'] ?? '');
        $date = (string)($item['date'] ?? '');
        if ($title === '' || preg_match('#^https?://#i', $url) !== 1 || preg_match('/^\d{4}-(\d{2})-(\d{2})$/', $date, $parts) !== 1) {
            continue;
        }
        $html .= '<li class="news-item">'
            . '<time datetime="' . news_h($date) . '">' . (int)$parts[1] . '/' . (int)$parts[2] . '</time>'
            . '<span class="news-place">' . news_h(news_item_place($item, $withPrefecture)) . '</span>'
            . '<a href="' . news_h($url) . '" target="_blank" rel="noopener">' . news_h($title) . '</a>'
            . '</li>';
    }
    if ($html === '') {
        return '<li class="news-empty">この 7 日のお知らせはありません。</li>';
    }
    return $html;
}

// 「11月14日 16:05 更新」
function news_render_updated(array $payload): string
{
    $generated = (string)($payload['generated_at'] ?? '');
    if (preg_match('/^\d{4}-(\d{2})-(\d{2}) (\d{2}):(\d{2})/', $generated, $parts) !== 1) {
        return '';
    }
    return (int)$parts[1] . '月' . (int)$parts[2] . '日 ' . $parts[3] . ':' . $parts[4] . ' 更新';
}
