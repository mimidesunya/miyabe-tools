<?php
declare(strict_types=1);

// 自治体のお知らせの全文検索（/api/news-search.php と MCP の search_municipal_news）。
//
// 索引は tools/news/news_search.py が作る OpenSearch の miyabe-news-current。お知らせ 1 件（題名・概要・
// リンク先のページの本文）と、添付の PDF 1 本（題名・リンクの文字・PDF の文字）が別の文書で、同じお知らせの
// 文書は検索で 1 件にまとめる（collapse）。本文は索引に入れた字数の先頭だけでなく全部を引く。
//
// 条件の検査は、検索語の分かち書き（Python を 1 回起動する）より前に済ませる。

require_once __DIR__ . DIRECTORY_SEPARATOR . 'opensearch_search.php';
require_once __DIR__ . DIRECTORY_SEPARATOR . 'news_api.php';

const NEWS_SEARCH_MAX_QUERY_CHARS = 200;
const NEWS_SEARCH_MAX_PER_PAGE = 30;
// 1 件のお知らせの中で見せる、当たった文書（ページ本文・添付の PDF）の数。
const NEWS_SEARCH_MAX_PARTS = 3;
const NEWS_SEARCH_FACET_SIZE = 50;
// 長い PDF の抜粋で、解析する先頭の字数。1 頁 30 件で、ほとんどの文書は 1 万字に満たない。
const NEWS_SEARCH_HIGHLIGHT_CHARS = 120000;

// 検索の条件を読み、OpenSearch に送る本文を作る。
function news_search_build_request(array $query, ?PDO $pdo = null): array
{
    $q = japanese_search_normalize_query_text(news_api_param($query, 'q') ?? '');
    if ($q === '') {
        throw new NewsApiError(400, 'invalid_request', 'q（検索語）を指定してください。新しい順に並べるだけなら /api/news.php を使ってください。');
    }
    if (mb_strlen($q) > NEWS_SEARCH_MAX_QUERY_CHARS) {
        throw new NewsApiError(400, 'invalid_request', 'q は ' . NEWS_SEARCH_MAX_QUERY_CHARS . ' 字までです。');
    }
    $sort = news_api_param($query, 'sort') ?? 'date';
    if (!in_array($sort, ['date', 'relevance'], true)) {
        throw new NewsApiError(400, 'invalid_request', 'sort は date（新しい順）か relevance（関連度順）です。');
    }
    $part = news_api_param($query, 'part');
    if ($part !== null && !in_array($part, ['item', 'attachment'], true)) {
        throw new NewsApiError(400, 'invalid_request', 'part は item（お知らせのページ）か attachment（添付の PDF）です。');
    }
    $page = news_api_int($query, 'page', 1, 1, MIYABE_SEARCH_MAX_RESULT_WINDOW);
    $perPage = news_api_int($query, 'per_page', 10, 1, NEWS_SEARCH_MAX_PER_PAGE);
    if ($page * $perPage > MIYABE_SEARCH_MAX_RESULT_WINDOW) {
        throw new NewsApiError(400, 'invalid_request', 'page × per_page は ' . MIYABE_SEARCH_MAX_RESULT_WINDOW . ' までです。条件を足して絞ってください。');
    }
    $since = news_api_date($query, 'since');
    $until = news_api_date($query, 'until');
    if ($since !== null && $until !== null && $since > $until) {
        throw new NewsApiError(400, 'invalid_request', 'since は until より前の日付にしてください。');
    }
    $includeFacets = news_api_bool($query, 'include_facets', false);
    $prefCode = news_api_pref_code($query);
    $municipality = null;
    foreach (['slug', 'municipality_code', 'municipality'] as $key) {
        if (news_api_param($query, $key) !== null) {
            $municipality = news_api_resolve_municipality($pdo ?? news_api_open(), $query, $prefCode);
            break;
        }
    }

    $clause = miyabe_search_build_query_clause($q, 'news');
    $must = $clause['must'];
    $proximity = $clause['proximity'];
    $filters = [['term' => ['hidden' => false]]];
    if ($municipality !== null) {
        $filters[] = ['term' => ['municipality_code' => (string)$municipality['jis_code']]];
    } elseif ($prefCode !== null) {
        $filters[] = ['term' => ['pref_code' => $prefCode]];
    }
    if ($since !== null || $until !== null) {
        $range = array_filter(['gte' => $since, 'lte' => $until], static fn($value) => $value !== null);
        $filters[] = ['range' => ['sort_date' => $range]];
    }
    if ($part !== null) {
        $filters[] = ['term' => ['part' => $part]];
    }

    // 日付順で、語を 2 つ以上並べた AND 検索（演算子なし）は、語が近くに現れた文書を先に置く（/api/search と同じ）。
    $proximityRanked = $sort === 'date' && $proximity !== [] && $must !== [];
    if ($proximityRanked) {
        $should = [];
        foreach ($proximity as $entry) {
            $should[] = ['constant_score' => ['filter' => $entry['clause'], 'boost' => $entry['boost']]];
        }
        $bodyQuery = ['bool' => ['filter' => array_merge($filters, $must), 'should' => $should]];
        $sortSpec = [['_score' => ['order' => 'desc']], ['sort_date' => ['order' => 'desc', 'missing' => '_last']]];
    } else {
        $bodyQuery = ['bool' => ['must' => $must === [] ? [['match_all' => (object)[]]] : $must, 'filter' => $filters]];
        $sortSpec = $sort === 'date'
            ? [['sort_date' => ['order' => 'desc', 'missing' => '_last']], ['_score' => ['order' => 'desc']]]
            : [['_score' => ['order' => 'desc']], ['sort_date' => ['order' => 'desc', 'missing' => '_last']]];
    }

    // 題名の強調はまとめた当たり（上の段）に、本文の抜粋は当たった文書ごと（inner_hits）に付ける。
    // inner_hits の強調は上の段の当たりには返らないので、題名を inner_hits に付けても題名の強調は空になる。
    $titleHighlight = [
        'pre_tags' => ['[[['],
        'post_tags' => [']]]'],
        'fields' => ['title' => ['number_of_fragments' => 0]],
    ];
    $highlight = [
        'pre_tags' => ['[[['],
        'post_tags' => [']]]'],
        'fields' => [
            'body' => [
                'fragment_size' => 160,
                'number_of_fragments' => 1,
                'no_match_size' => 120,
                'max_analyzer_offset' => NEWS_SEARCH_HIGHLIGHT_CHARS,
            ],
        ],
    ];
    $body = [
        'from' => ($page - 1) * $perPage,
        'size' => $perPage,
        'track_total_hits' => MIYABE_SEARCH_MAX_RESULT_WINDOW,
        'query' => $bodyQuery,
        'sort' => $sortSpec,
        // お知らせ 1 件の中で、当たった文書（ページ本文・添付）を 1 件にまとめる。
        'collapse' => [
            'field' => 'item_key',
            'inner_hits' => [
                'name' => 'parts',
                'size' => NEWS_SEARCH_MAX_PARTS,
                'sort' => [['_score' => ['order' => 'desc']]],
                '_source' => ['part', 'attachment_url', 'link_text', 'kind', 'pages'],
                'highlight' => $highlight,
            ],
            'max_concurrent_group_searches' => 4,
        ],
        '_source' => ['item_key', 'slug', 'municipality_code', 'pref_code', 'pref_name', 'municipality_name', 'title', 'url',
            'date', 'sort_date', 'first_seen_at', 'event_start', 'event_end', 'summary', 'categories', 'hidden'],
        'highlight' => $titleHighlight,
        'aggs' => ['items' => ['cardinality' => ['field' => 'item_key', 'precision_threshold' => 10000]]],
    ];
    if ($includeFacets) {
        // 検索画面の「多い都道府県」と地図のため。文書（ページ・添付）ではなくお知らせの数で数え、多い順に 50 まで。
        $byItems = ['items' => ['cardinality' => ['field' => 'item_key', 'precision_threshold' => 1000]]];
        foreach (['prefectures' => 'pref_code', 'municipalities' => 'slug'] as $name => $field) {
            $body['aggs'][$name] = [
                'terms' => ['field' => $field, 'size' => NEWS_SEARCH_FACET_SIZE, 'order' => ['items' => 'desc']],
                'aggs' => $byItems,
            ];
        }
    }

    return [
        'q' => $q,
        'sort' => $sort,
        'part' => $part,
        'page' => $page,
        'per_page' => $perPage,
        'since' => $since,
        'until' => $until,
        'pref_code' => $municipality === null ? $prefCode : null,
        'municipality' => $municipality,
        'proximity_ranked' => $proximityRanked,
        'include_facets' => $includeFacets,
        'body' => $body,
    ];
}

// 集計を /api/search と同じ形（key と count の並び）にする。count はお知らせの数。
function news_search_shape_facets(array $aggregations): array
{
    $facets = [];
    foreach (['prefectures', 'municipalities'] as $name) {
        $facets[$name] = [];
        foreach ($aggregations[$name]['buckets'] ?? [] as $bucket) {
            if (is_array($bucket) && isset($bucket['key'])) {
                $facets[$name][] = [
                    'key' => (string)$bucket['key'],
                    'count' => (int)($bucket['items']['value'] ?? $bucket['doc_count'] ?? 0),
                ];
            }
        }
    }
    return $facets;
}

function news_search_highlight(array $hit, string $field): string
{
    $values = $hit['highlight'][$field] ?? null;
    return is_array($values) ? trim(implode(' … ', array_map('strval', $values))) : '';
}

function news_search_shape_part(array $hit): array
{
    $source = is_array($hit['_source'] ?? null) ? $hit['_source'] : [];
    $excerpt = preg_replace('/\s+/u', ' ', news_search_highlight($hit, 'body')) ?? '';
    return [
        'part' => ($source['part'] ?? '') === 'attachment' ? 'attachment' : 'page',
        'link_text' => ($source['part'] ?? '') === 'attachment' ? (string)($source['link_text'] ?? '') : null,
        'attachment_url' => ($source['part'] ?? '') === 'attachment' ? (string)($source['attachment_url'] ?? '') : null,
        'kind' => isset($source['kind']) ? (string)$source['kind'] : null,
        'pages' => isset($source['pages']) ? (int)$source['pages'] : null,
        'excerpt' => trim($excerpt),
        // 抜粋に当たった語が入っているか。長い文書の後ろのほう（NEWS_SEARCH_HIGHLIGHT_CHARS より先）や、題名・
        // リンクの文字だけで当たったときは入らず、文書の頭が出る。
        'excerpt_has_hit' => str_contains($excerpt, '[[['),
    ];
}

function news_search_shape_hit(array $hit): ?array
{
    $source = is_array($hit['_source'] ?? null) ? $hit['_source'] : [];
    $key = (string)($source['item_key'] ?? '');
    // 一覧と 1 件の API と同じ id。添付の文書が上に来ても、お知らせの id を返す。
    if (preg_match(NEWS_API_ITEM_ID_PATTERN, $key) !== 1) {
        return null;
    }
    $parts = [];
    foreach ($hit['inner_hits']['parts']['hits']['hits'] ?? [] as $inner) {
        if (is_array($inner)) {
            $parts[] = news_search_shape_part($inner);
        }
    }
    return [
        'id' => $key,
        'title' => (string)($source['title'] ?? ''),
        'title_highlight' => news_search_highlight($hit, 'title'),
        'date' => isset($source['date']) ? (string)$source['date'] : null,
        'sort_date' => (string)($source['sort_date'] ?? ''),
        'url' => (string)($source['url'] ?? ''),
        'municipality_name' => (string)($source['municipality_name'] ?? ''),
        'pref_name' => (string)($source['pref_name'] ?? ''),
        'municipality_code' => (string)($source['municipality_code'] ?? ''),
        'pref_code' => (string)($source['pref_code'] ?? ''),
        'slug' => (string)($source['slug'] ?? ''),
        'categories' => is_array($source['categories'] ?? null) ? array_values($source['categories']) : [],
        'summary' => isset($source['summary']) ? (string)$source['summary'] : null,
        'event_start' => isset($source['event_start']) ? (string)$source['event_start'] : null,
        'event_end' => isset($source['event_end']) ? (string)$source['event_end'] : null,
        'first_seen_at' => (string)($source['first_seen_at'] ?? ''),
        'matches' => $parts,
        'api_item_url' => '/api/news-item.php?id=' . rawurlencode($key),
    ];
}

function news_search_shape_response(array $response, array $request): array
{
    $items = [];
    foreach ($response['hits']['hits'] ?? [] as $hit) {
        $item = is_array($hit) ? news_search_shape_hit($hit) : null;
        if ($item !== null) {
            $items[] = $item;
        }
    }
    $total = (int)($response['aggregations']['items']['value'] ?? count($items));
    $payload = [
        'status' => 'ok',
        'query' => $request['q'],
        'sort' => $request['sort'],
        // 語が近くに現れた文書を先に置いたか（日付順の AND 検索のとき）。
        'proximity_ranked' => $request['proximity_ranked'],
        'total' => $total,
        // お知らせの数は、1 万を超えると概数（数の散らばりで数えるため）。
        'total_relation' => $total > 10000 ? 'approximate' : 'eq',
        'page' => $request['page'],
        'per_page' => $request['per_page'],
        'filters' => (object)array_filter([
            'pref_code' => $request['pref_code'],
            'since' => $request['since'],
            'until' => $request['until'],
            'part' => $request['part'],
        ], static fn($value) => $value !== null),
        'municipality' => $request['municipality'] !== null
            ? news_api_municipality_summary($request['municipality']) : null,
        'items' => $items,
    ];
    if ($request['include_facets'] ?? false) {
        $payload['aggregations'] = news_search_shape_facets(is_array($response['aggregations'] ?? null) ? $response['aggregations'] : []);
    }
    return $payload;
}

function news_search_execute(array $query, ?PDO $pdo = null, ?callable $http = null): array
{
    $request = news_search_build_request($query, $pdo);
    $http ??= 'miyabe_search_http_request';
    $response = $http('POST', '/' . miyabe_search_alias_for_type('news') . '/_search', $request['body']);
    return news_search_shape_response(is_array($response) ? $response : [], $request);
}

// /api/news-search.php の入口。OpenSearch の生のエラーには索引の世代名が入るので、応答には出さない。
function news_search_handle(): never
{
    try {
        news_api_respond(news_search_execute($_GET), 200, true);
    } catch (NewsApiError $error) {
        news_api_respond(['status' => $error->statusCode, 'error' => $error->getMessage()] + $error->extra, $error->httpStatus);
    } catch (MiyabeOpenSearchException $error) {
        error_log('[api/news-search] ' . $error->getMessage());
        news_api_respond(['status' => $error->errorCode, 'error' => 'お知らせの全文検索は、いま使えません。'], $error->httpStatus);
    } catch (Throwable $error) {
        error_log('[api/news-search] ' . $error->getMessage());
        news_api_respond(['status' => 'error', 'error' => 'お知らせを検索できませんでした。'], 500);
    }
}
