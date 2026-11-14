<?php
declare(strict_types=1);

// 自治体のお知らせの API（/api/news.php・/api/news-item.php と MCP の list_municipal_news・
// get_municipal_news_item）。
//
// 索引は取得の worker が 3 時間おきの回の中で書く SQLite（tools/news/news_index.py）。ここは読むだけで開く。
// 本文と添付の PDF の文字は索引に入れていないので、索引に控えた位置から元のファイルを 1 行だけ読む。
//
//     work/news/index.sqlite        索引
//     data/news/<slug>/bodies/      本文（1 行 1 件）
//     data/news/<slug>/attachments/ 添付の PDF の文字（1 行 1 本）

const NEWS_API_SLUG_PATTERN = '/^(\d{5})(-[a-z0-9-]+)?$/';
const NEWS_API_ITEM_ID_PATTERN = '/^news:(\d{5}(?:-[a-z0-9-]+)?):([A-Za-z0-9_-]{1,64})$/';
const NEWS_API_REF_PATTERN = '/^(bodies|attachments)\/\d{4}\.jsonl$/';
const NEWS_API_MAX_PER_PAGE = 100;
const NEWS_API_MAX_WINDOW = 10000;
const NEWS_API_MAX_QUERY_CHARS = 50;
const NEWS_API_DEFAULT_TEXT_CHARS = 20000;
const NEWS_API_MAX_TEXT_CHARS = 200000;

final class NewsApiError extends RuntimeException
{
    public function __construct(
        public readonly int $httpStatus,
        public readonly string $statusCode,
        string $message,
        public readonly array $extra = []
    ) {
        parent::__construct($message);
    }
}

function news_api_index_path(): string
{
    $override = getenv('MIYABE_NEWS_INDEX');
    if (is_string($override) && $override !== '') {
        return $override;
    }
    return dirname(__DIR__) . DIRECTORY_SEPARATOR . 'work' . DIRECTORY_SEPARATOR . 'news' . DIRECTORY_SEPARATOR . 'index.sqlite';
}

function news_api_data_root(): string
{
    $override = getenv('MIYABE_NEWS_DATA_ROOT');
    if (is_string($override) && $override !== '') {
        return $override;
    }
    return dirname(__DIR__) . DIRECTORY_SEPARATOR . 'data' . DIRECTORY_SEPARATOR . 'news';
}

function news_api_open(): PDO
{
    $path = news_api_index_path();
    if (!is_file($path)) {
        throw new NewsApiError(503, 'unavailable', 'お知らせの索引がまだありません。');
    }
    $pdo = new PDO('sqlite:' . $path, null, null, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::SQLITE_ATTR_OPEN_FLAGS => PDO::SQLITE_OPEN_READONLY,
    ]);
    // worker が確定する一瞬は待つ。
    $pdo->setAttribute(PDO::ATTR_TIMEOUT, 10);
    return $pdo;
}

// 1 つの値だけを受ける。?slug[]=… のような配列は断る。
function news_api_param(array $query, string $key): ?string
{
    if (!array_key_exists($key, $query) || $query[$key] === null) {
        return null;
    }
    $value = $query[$key];
    if (!is_string($value) && !is_int($value)) {
        throw new NewsApiError(400, 'invalid_request', "{$key} は 1 つの値で指定してください。");
    }
    $value = trim((string)$value);
    return $value === '' ? null : $value;
}

function news_api_int(array $query, string $key, int $default, int $min, int $max): int
{
    $value = news_api_param($query, $key);
    if ($value === null) {
        return $default;
    }
    if (preg_match('/^\d{1,7}$/', $value) !== 1 || (int)$value < $min || (int)$value > $max) {
        throw new NewsApiError(400, 'invalid_request', "{$key} は {$min}〜{$max} の整数です。");
    }
    return (int)$value;
}

function news_api_bool(array $query, string $key, bool $default): bool
{
    $value = news_api_param($query, $key);
    if ($value === null) {
        return $default;
    }
    if (in_array(strtolower($value), ['1', 'true', 'yes'], true)) {
        return true;
    }
    if (in_array(strtolower($value), ['0', 'false', 'no'], true)) {
        return false;
    }
    throw new NewsApiError(400, 'invalid_request', "{$key} は 1 か 0 です。");
}

function news_api_date(array $query, string $key): ?string
{
    $value = news_api_param($query, $key);
    if ($value === null) {
        return null;
    }
    $date = DateTimeImmutable::createFromFormat('!Y-m-d', $value);
    if ($date === false || $date->format('Y-m-d') !== $value) {
        throw new NewsApiError(400, 'invalid_request', "{$key} は YYYY-MM-DD の形の日付です。");
    }
    return $value;
}

function news_api_pref_code(array $query): ?string
{
    $value = news_api_param($query, 'pref_code');
    if ($value === null) {
        return null;
    }
    if (preg_match('/^(0?[1-9]|[1-3][0-9]|4[0-7])$/', $value) !== 1) {
        throw new NewsApiError(400, 'invalid_request', 'pref_code は 1〜47 の都道府県コードです。');
    }
    return sprintf('%02d', (int)$value);
}

// 自治体を 1 つに決める。slug・municipality_code・municipality（名前）のどれか。無ければ null。
function news_api_resolve_municipality(PDO $pdo, array $query, ?string $prefCode): ?array
{
    $slug = news_api_param($query, 'slug');
    $code = news_api_param($query, 'municipality_code');
    $name = news_api_param($query, 'municipality');
    if ($slug !== null) {
        if (preg_match(NEWS_API_SLUG_PATTERN, $slug, $match) !== 1) {
            throw new NewsApiError(400, 'invalid_request', 'slug の形が違います（例: 13209-machida-shi）。');
        }
        $code = $match[1];
    } elseif ($code !== null) {
        if (preg_match('/^\d{5,6}$/', $code) !== 1) {
            throw new NewsApiError(400, 'invalid_request', 'municipality_code は 5 桁（検査数字つきなら 6 桁）の数字です。');
        }
        $code = substr($code, 0, 5);
    }
    if ($code !== null) {
        $statement = $pdo->prepare('SELECT * FROM municipalities WHERE jis_code = ?');
        $statement->execute([$code]);
        $row = $statement->fetch();
        if (!is_array($row)) {
            throw new NewsApiError(422, 'municipality_not_found', 'その自治体コードの自治体が見つかりません。');
        }
        return $row;
    }
    if ($name === null) {
        return null;
    }
    $name = preg_replace('/[\s　]+/u', '', $name) ?? $name;
    if (mb_strlen($name) > 40) {
        throw new NewsApiError(400, 'invalid_request', 'municipality は 40 字までです。');
    }
    // 「町田市」も「東京都町田市」も受ける。
    $sql = 'SELECT * FROM municipalities WHERE (name = :name OR pref_name || name = :name)';
    $params = ['name' => $name];
    if ($prefCode !== null) {
        $sql .= ' AND pref_code = :pref';
        $params['pref'] = $prefCode;
    }
    $statement = $pdo->prepare($sql . ' ORDER BY jis_code');
    $statement->execute($params);
    $rows = $statement->fetchAll();
    if ($rows === []) {
        throw new NewsApiError(422, 'municipality_not_found', "「{$name}」という自治体が見つかりません。市区町村名は「町田市」のように、市・町・村・区まで書いてください。");
    }
    if (count($rows) > 1) {
        throw new NewsApiError(422, 'ambiguous_municipality', "「{$name}」という自治体が複数あります。pref_code か municipality_code で 1 つに絞ってください。", [
            'candidates' => array_map(static fn(array $row): array => [
                'municipality_name' => $row['name'],
                'pref_name' => $row['pref_name'],
                'pref_code' => $row['pref_code'],
                'municipality_code' => $row['jis_code'],
            ], $rows),
        ]);
    }
    return $rows[0];
}

function news_api_municipality_summary(array $row): array
{
    return [
        'municipality_name' => (string)$row['name'],
        'pref_name' => (string)$row['pref_name'],
        'municipality_code' => (string)$row['jis_code'],
        'pref_code' => (string)$row['pref_code'],
        'slug' => $row['slug'] !== null ? (string)$row['slug'] : null,
        'collecting' => $row['slug'] !== null,
        'items' => (int)$row['items'],
        'last_date' => $row['last_date'] !== null ? (string)$row['last_date'] : null,
    ];
}

function news_api_like(string $value): string
{
    return '%' . strtr($value, ['\\' => '\\\\', '%' => '\\%', '_' => '\\_']) . '%';
}

function news_api_updated_at(PDO $pdo): ?string
{
    $value = $pdo->query("SELECT value FROM meta WHERE key = 'updated_at'")->fetchColumn();
    return is_string($value) ? $value : null;
}

function news_api_item_shape(array $row): array
{
    $categories = json_decode((string)($row['categories'] ?? ''), true);
    $id = 'news:' . $row['slug'] . ':' . $row['item_id'];
    $body = null;
    if ($row['body_kind'] !== null) {
        $body = [
            'kind' => (string)$row['body_kind'],
            'chars' => (int)$row['body_chars'],
            'excerpt' => $row['body_excerpt'] !== null ? (string)$row['body_excerpt'] : null,
        ];
    }
    return [
        'id' => $id,
        'title' => (string)$row['title'],
        'date' => $row['date'] !== null ? (string)$row['date'] : null,
        'sort_date' => (string)$row['sort_date'],
        'url' => (string)$row['url'],
        'municipality_name' => (string)$row['name'],
        'pref_name' => (string)$row['pref_name'],
        'municipality_code' => (string)$row['jis_code'],
        'pref_code' => (string)$row['pref_code'],
        'slug' => (string)$row['slug'],
        'categories' => is_array($categories) ? array_values($categories) : [],
        'summary' => $row['summary'] !== null ? (string)$row['summary'] : null,
        'event_start' => $row['event_start'] !== null ? (string)$row['event_start'] : null,
        'event_end' => $row['event_end'] !== null ? (string)$row['event_end'] : null,
        'first_seen_at' => (string)$row['first_seen_at'],
        'body' => $body,
        'attachments_count' => (int)($row['attachments_count'] ?? 0),
        'api_item_url' => '/api/news-item.php?id=' . rawurlencode($id),
    ];
}

const NEWS_API_ITEM_COLUMNS = 'i.slug, i.item_id, i.jis_code, i.pref_code, i.title, i.url, i.date, i.sort_date,
    i.first_seen_at, i.summary, i.categories, i.event_start, i.event_end, m.name, m.pref_name,
    b.kind AS body_kind, b.chars AS body_chars, b.excerpt AS body_excerpt, b.attachments_count';

// お知らせの一覧。新しい順（日付。日付の無いもの・催しの日のものは見つけた日）。
function news_api_list(array $query, ?PDO $pdo = null): array
{
    $pdo ??= news_api_open();
    $prefCode = news_api_pref_code($query);
    $municipality = news_api_resolve_municipality($pdo, $query, $prefCode);
    $since = news_api_date($query, 'since');
    $until = news_api_date($query, 'until');
    if ($since !== null && $until !== null && $since > $until) {
        throw new NewsApiError(400, 'invalid_request', 'since は until より前の日付にしてください。');
    }
    $q = news_api_param($query, 'q');
    if ($q !== null && mb_strlen($q) > NEWS_API_MAX_QUERY_CHARS) {
        throw new NewsApiError(400, 'invalid_request', 'q は ' . NEWS_API_MAX_QUERY_CHARS . ' 字までです。');
    }
    $page = news_api_int($query, 'page', 1, 1, NEWS_API_MAX_WINDOW);
    $perPage = news_api_int($query, 'per_page', 20, 1, NEWS_API_MAX_PER_PAGE);
    if ($page * $perPage > NEWS_API_MAX_WINDOW) {
        throw new NewsApiError(400, 'invalid_request', 'page × per_page は ' . NEWS_API_MAX_WINDOW . ' までです。期間で絞ってください。');
    }

    $where = ['i.hidden = 0'];
    $params = [];
    if ($municipality !== null) {
        $where[] = 'i.jis_code = :code';
        $params['code'] = $municipality['jis_code'];
    } elseif ($prefCode !== null) {
        $where[] = 'i.pref_code = :pref';
        $params['pref'] = $prefCode;
    }
    if ($since !== null) {
        $where[] = 'i.sort_date >= :since';
        $params['since'] = $since;
    }
    if ($until !== null) {
        $where[] = 'i.sort_date <= :until';
        $params['until'] = $until;
    }
    if ($q !== null) {
        $words = preg_split('/[\s　]+/u', $q, -1, PREG_SPLIT_NO_EMPTY) ?: [];
        foreach (array_slice($words, 0, 5) as $index => $word) {
            $where[] = "(i.title LIKE :q{$index} ESCAPE '\\' OR i.summary LIKE :q{$index} ESCAPE '\\')";
            $params["q{$index}"] = news_api_like($word);
        }
    }
    $condition = implode(' AND ', $where);

    $count = $pdo->prepare("SELECT COUNT(*) FROM items AS i WHERE {$condition}");
    $count->execute($params);
    $total = (int)$count->fetchColumn();

    $statement = $pdo->prepare('SELECT ' . NEWS_API_ITEM_COLUMNS . "
        FROM items AS i
        JOIN municipalities AS m ON m.jis_code = i.jis_code
        LEFT JOIN bodies AS b ON b.slug = i.slug AND b.item_id = i.item_id
        WHERE {$condition}
        ORDER BY i.sort_date DESC, i.first_seen_at DESC, i.item_id
        LIMIT :limit OFFSET :offset");
    foreach ($params as $key => $value) {
        $statement->bindValue($key, $value);
    }
    $statement->bindValue('limit', $perPage, PDO::PARAM_INT);
    $statement->bindValue('offset', ($page - 1) * $perPage, PDO::PARAM_INT);
    $statement->execute();
    $items = array_map('news_api_item_shape', $statement->fetchAll());

    $payload = [
        'status' => 'ok',
        'total' => $total,
        'page' => $page,
        'per_page' => $perPage,
        // 条件が無くても JSON では {} にする。
        'filters' => (object)array_filter([
            'pref_code' => $municipality === null ? $prefCode : null,
            'since' => $since,
            'until' => $until,
            'q' => $q,
        ], static fn($value) => $value !== null),
        'municipality' => $municipality !== null ? news_api_municipality_summary($municipality) : null,
        'index_updated_at' => news_api_updated_at($pdo),
        'items' => $items,
    ];
    if ($municipality !== null && $municipality['slug'] === null) {
        $payload['note'] = 'この自治体のお知らせはまだ集めていません（新着情報のフィードが見つかっていないなど）。';
    }
    return $payload;
}

// 索引に控えた位置から 1 行だけ読む。行の中身が違えば（ファイルが作り直されたなど）null。
function news_api_read_line(string $slug, string $ref, int $offset): ?array
{
    if (preg_match(NEWS_API_SLUG_PATTERN, $slug) !== 1 || preg_match(NEWS_API_REF_PATTERN, $ref) !== 1 || $offset < 0) {
        return null;
    }
    $path = news_api_data_root() . DIRECTORY_SEPARATOR . $slug . DIRECTORY_SEPARATOR . str_replace('/', DIRECTORY_SEPARATOR, $ref);
    $handle = @fopen($path, 'rb');
    if ($handle === false) {
        return null;
    }
    try {
        if (fseek($handle, $offset) !== 0) {
            return null;
        }
        $line = fgets($handle);
    } finally {
        fclose($handle);
    }
    if (!is_string($line)) {
        return null;
    }
    $data = json_decode($line, true);
    return is_array($data) ? $data : null;
}

function news_api_cut(string $text, int $limit): array
{
    $length = mb_strlen($text);
    if ($length <= $limit) {
        return [$text, $length, false];
    }
    return [mb_substr($text, 0, $limit), $length, true];
}

// 添付の PDF の文字は改頁文字（\f）で頁を区切ってある。読む人に分かるよう、頁の印に直す。
function news_api_mark_pages(string $text): string
{
    if (!str_contains($text, "\f")) {
        return $text;
    }
    $pages = explode("\f", $text);
    $out = [];
    foreach ($pages as $index => $page) {
        $out[] = '〔' . ($index + 1) . '頁〕' . "\n" . trim($page);
    }
    return implode("\n\n", $out);
}

// お知らせ 1 件の本文と、添付の PDF の文字。
function news_api_item(array $query, ?PDO $pdo = null): array
{
    $id = news_api_param($query, 'id');
    if ($id === null || preg_match(NEWS_API_ITEM_ID_PATTERN, $id, $match) !== 1) {
        throw new NewsApiError(400, 'invalid_request', 'id は一覧の id（news:<slug>:<項目の印>）です。');
    }
    [, $slug, $itemId] = $match;
    $maxBody = news_api_int($query, 'max_body_chars', NEWS_API_DEFAULT_TEXT_CHARS, 0, NEWS_API_MAX_TEXT_CHARS);
    $withAttachments = news_api_bool($query, 'include_attachments', true);
    $maxAttachments = news_api_int($query, 'max_attachment_chars', NEWS_API_DEFAULT_TEXT_CHARS, 0, NEWS_API_MAX_TEXT_CHARS);
    $pdo ??= news_api_open();

    $statement = $pdo->prepare('SELECT ' . NEWS_API_ITEM_COLUMNS . ',
            b.status AS body_status, b.pages AS body_pages, b.page_title, b.final_url, b.fetched_at AS body_fetched_at,
            b.ref AS body_ref, b.offset AS body_offset, b.attachments
        FROM items AS i
        JOIN municipalities AS m ON m.jis_code = i.jis_code
        LEFT JOIN bodies AS b ON b.slug = i.slug AND b.item_id = i.item_id
        WHERE i.slug = :slug AND i.item_id = :item');
    $statement->execute(['slug' => $slug, 'item' => $itemId]);
    $row = $statement->fetch();
    if (!is_array($row)) {
        throw new NewsApiError(404, 'not_found', 'そのお知らせは見つかりません。');
    }
    $item = news_api_item_shape($row);
    unset($item['api_item_url']);

    $body = null;
    if ($row['body_kind'] !== null) {
        $text = '';
        if ((int)$row['body_chars'] > 0) {
            $line = news_api_read_line($slug, (string)$row['body_ref'], (int)$row['body_offset']);
            if ($line !== null && (string)($line['id'] ?? '') === $itemId) {
                $text = (string)($line['text'] ?? '');
            }
        }
        [$cut, $length, $truncated] = news_api_cut($text, $maxBody);
        $body = [
            'kind' => (string)$row['body_kind'],
            'status' => $row['body_status'] !== null ? (int)$row['body_status'] : null,
            'pages' => $row['body_pages'] !== null ? (int)$row['body_pages'] : null,
            'page_title' => $row['page_title'] !== null ? (string)$row['page_title'] : null,
            'final_url' => $row['final_url'] !== null ? (string)$row['final_url'] : null,
            'fetched_at' => $row['body_fetched_at'] !== null ? (string)$row['body_fetched_at'] : null,
            'text' => $row['body_kind'] === 'pdf' ? news_api_mark_pages($cut) : $cut,
            'chars' => $length,
            'truncated' => $truncated,
        ];
    }
    $item['body'] = $body;

    $attachments = [];
    $links = json_decode((string)($row['attachments'] ?? ''), true);
    if (is_array($links)) {
        $lookup = $pdo->prepare('SELECT * FROM attachments WHERE slug = ? AND url = ?');
        $budget = $maxAttachments;
        foreach ($links as $link) {
            if (!is_array($link) || !is_string($link['url'] ?? null)) {
                continue;
            }
            $lookup->execute([$slug, $link['url']]);
            $found = $lookup->fetch();
            $lookup->closeCursor();
            $entry = [
                'url' => $link['url'],
                'link_text' => (string)($link['text'] ?? ''),
                'read' => is_array($found),
                'kind' => is_array($found) ? $found['kind'] : null,
                'pages' => is_array($found) && $found['pages'] !== null ? (int)$found['pages'] : null,
                'chars' => is_array($found) ? (int)$found['chars'] : 0,
                'fetched_at' => is_array($found) ? $found['fetched_at'] : null,
            ];
            if ($withAttachments && is_array($found) && (int)$found['chars'] > 0) {
                if ($budget <= 0) {
                    // 文字の上限（max_attachment_chars は添付の全部で）を使い切った。
                    $entry['omitted'] = true;
                } else {
                    $line = news_api_read_line($slug, (string)$found['ref'], (int)$found['offset']);
                    if ($line !== null && (string)($line['url'] ?? '') === $link['url']) {
                        [$cut, , $truncated] = news_api_cut((string)($line['text'] ?? ''), $budget);
                        $budget -= mb_strlen($cut);
                        $entry['text'] = news_api_mark_pages($cut);
                        $entry['truncated'] = $truncated || (bool)$found['truncated'];
                    }
                }
            }
            $attachments[] = $entry;
        }
    }
    $item['attachments'] = $attachments;

    return [
        'status' => 'ok',
        'index_updated_at' => news_api_updated_at($pdo),
        'item' => $item,
    ];
}

function news_api_respond(array $payload, int $status = 200, bool $cacheable = false): never
{
    http_response_code($status);
    header('Content-Type: application/json; charset=UTF-8');
    header('X-Content-Type-Options: nosniff');
    // 索引は 3 時間おきに変わる。
    header($cacheable ? 'Cache-Control: public, max-age=300' : 'Cache-Control: no-store');
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE) . "\n";
    exit;
}

// /api/news.php と /api/news-item.php の入口。
function news_api_handle(callable $action, string $label): never
{
    try {
        news_api_respond($action($_GET), 200, true);
    } catch (NewsApiError $error) {
        news_api_respond(['status' => $error->statusCode, 'error' => $error->getMessage()] + $error->extra, $error->httpStatus);
    } catch (Throwable $error) {
        error_log("[api/{$label}] " . $error->getMessage());
        news_api_respond(['status' => 'error', 'error' => 'お知らせを読めませんでした。'], 500);
    }
}
