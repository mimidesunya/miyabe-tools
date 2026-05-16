<?php
declare(strict_types=1);

require_once __DIR__ . DIRECTORY_SEPARATOR . 'japanese_search.php';
require_once __DIR__ . DIRECTORY_SEPARATOR . 'municipalities.php';

final class MiyabeOpenSearchException extends RuntimeException
{
    public function __construct(
        string $message,
        public readonly int $httpStatus = 503,
        public readonly string $errorCode = 'opensearch_unavailable'
    ) {
        parent::__construct($message);
    }
}

function miyabe_search_env(string $key, string $default = ''): string
{
    $value = getenv($key);
    if ($value === false || trim((string)$value) === '') {
        return $default;
    }
    return trim((string)$value);
}

function miyabe_search_opensearch_url(): string
{
    return rtrim(miyabe_search_env('OPENSEARCH_URL', 'http://opensearch:9200'), '/');
}

function miyabe_search_alias_for_type(string $docType): string
{
    return match ($docType) {
        'minutes' => miyabe_search_env('MIYABE_MINUTES_ALIAS', 'miyabe-minutes-current'),
        'reiki' => miyabe_search_env('MIYABE_REIKI_ALIAS', 'miyabe-reiki-current'),
        default => miyabe_search_env('MIYABE_SEARCH_ALIAS', 'miyabe-documents-current'),
    };
}

function miyabe_search_parse_http_status(array $headers): int
{
    $status = 0;
    foreach ($headers as $header) {
        if (preg_match('/^HTTP\/\S+\s+(\d{3})\b/', (string)$header, $matches) === 1) {
            $status = (int)$matches[1];
        }
    }
    return $status;
}

function miyabe_search_http_request(string $method, string $path, ?array $payload = null): array
{
    $baseUrl = miyabe_search_opensearch_url();
    if ($baseUrl === '') {
        throw new MiyabeOpenSearchException('OpenSearch URL is not configured.');
    }

    $url = $baseUrl . '/' . ltrim($path, '/');
    $body = $payload !== null
        ? json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE)
        : null;
    if ($payload !== null && !is_string($body)) {
        throw new MiyabeOpenSearchException('OpenSearch request JSON encoding failed.');
    }

    $headers = [
        'Accept: application/json',
    ];
    if ($body !== null) {
        $headers[] = 'Content-Type: application/json';
    }

    $user = miyabe_search_env('OPENSEARCH_USER');
    $password = miyabe_search_env('OPENSEARCH_PASSWORD');
    if ($user !== '' || $password !== '') {
        $headers[] = 'Authorization: Basic ' . base64_encode($user . ':' . $password);
    }

    $contextOptions = [
        'http' => [
            'method' => strtoupper($method),
            'header' => implode("\r\n", $headers),
            'content' => $body ?? '',
            'ignore_errors' => true,
            'timeout' => 20,
        ],
    ];
    $insecureDev = strtolower(miyabe_search_env('OPENSEARCH_INSECURE_DEV', 'false'));
    if (str_starts_with(strtolower($url), 'https://') && in_array($insecureDev, ['1', 'true', 'yes', 'on'], true)) {
        $contextOptions['ssl'] = [
            'verify_peer' => false,
            'verify_peer_name' => false,
        ];
    }

    $http_response_header = [];
    $response = @file_get_contents($url, false, stream_context_create($contextOptions));
    $status = miyabe_search_parse_http_status($http_response_header);
    if (!is_string($response)) {
        throw new MiyabeOpenSearchException('OpenSearch is unavailable.', 503, 'opensearch_unavailable');
    }

    $decoded = json_decode($response, true);
    $data = is_array($decoded) ? $decoded : [];
    if ($status < 200 || $status >= 300) {
        $reason = '';
        if (isset($data['error'])) {
            $reason = is_scalar($data['error'])
                ? (string)$data['error']
                : json_encode($data['error'], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        }
        $message = trim((string)$reason) !== '' ? trim((string)$reason) : ('OpenSearch HTTP ' . $status);
        $publicStatus = in_array($status, [401, 403], true) ? 503 : ($status === 404 ? 503 : $status);
        throw new MiyabeOpenSearchException($message, $publicStatus, 'opensearch_error');
    }

    return $data;
}

function miyabe_search_request_string(string $key, string $default = ''): string
{
    $value = $_GET[$key] ?? $default;
    return is_scalar($value) ? trim((string)$value) : $default;
}

function miyabe_search_request_int(string $key, int $default, int $min, int $max): int
{
    $value = $_GET[$key] ?? $default;
    if (!is_scalar($value) || filter_var($value, FILTER_VALIDATE_INT) === false) {
        return $default;
    }
    return max($min, min($max, (int)$value));
}

function miyabe_search_normalize_doc_type(string $value): string
{
    $value = strtolower(trim($value));
    return in_array($value, ['minutes', 'reiki'], true) ? $value : 'all';
}

function miyabe_search_normalize_pref_code(string $value): string
{
    $value = preg_replace('/[^0-9]/', '', $value) ?? '';
    if ($value === '') {
        return '';
    }
    if (strlen($value) === 1) {
        $value = '0' . $value;
    }
    return preg_match('/^\d{2}$/', $value) === 1 ? $value : '';
}

function miyabe_search_year_range_filter(string $startYear, string $endYear): ?array
{
    $start = preg_match('/^\d{1,4}$/', $startYear) === 1 ? max(1, min(9999, (int)$startYear)) : 0;
    $end = preg_match('/^\d{1,4}$/', $endYear) === 1 ? max(1, min(9999, (int)$endYear)) : 0;
    if ($start > 0 && $end > 0 && $start > $end) {
        [$start, $end] = [$end, $start];
    }
    $range = [];
    if ($start > 0) {
        $range['gte'] = sprintf('%04d-01-01', $start);
    }
    if ($end > 0) {
        $range['lte'] = sprintf('%04d-12-31', $end);
    }
    return $range === [] ? null : ['range' => ['sort_date' => $range]];
}

function miyabe_search_build_query_clause(string $query): array
{
    $prepared = japanese_search_prepare_query($query);
    $rawQuery = trim((string)($prepared['raw_query'] ?? $query));
    $highlightTerms = is_array($prepared['highlight_terms'] ?? null) ? $prepared['highlight_terms'] : [];
    $termQuery = trim(implode(' ', array_filter(array_map('strval', $highlightTerms))));
    $exactPhrases = japanese_search_exact_phrases_from_prepared($prepared);

    $must = [];
    $should = [];
    if ($rawQuery !== '') {
        $should[] = [
            'simple_query_string' => [
                'query' => $rawQuery,
                'fields' => [
                    'title^4',
                    'title.ngram^1.4',
                    'meeting_name^2',
                    'body^1.5',
                    'body.ngram',
                ],
                'default_operator' => 'and',
            ],
        ];
    }
    if ($termQuery !== '') {
        $should[] = [
            'multi_match' => [
                'query' => $termQuery,
                'fields' => [
                    'title_terms^3',
                    'body_terms',
                ],
                'operator' => 'and',
            ],
        ];
    }
    if ($should !== []) {
        $must[] = [
            'bool' => [
                'should' => $should,
                'minimum_should_match' => 1,
            ],
        ];
    }

    foreach ($exactPhrases as $phrase) {
        $phrase = trim((string)$phrase);
        if ($phrase === '') {
            continue;
        }
        $must[] = [
            'bool' => [
                'should' => [
                    ['match_phrase' => ['title' => ['query' => $phrase, 'boost' => 3.0]]],
                    ['match_phrase' => ['meeting_name' => ['query' => $phrase, 'boost' => 2.0]]],
                    ['match_phrase' => ['body' => ['query' => $phrase]]],
                ],
                'minimum_should_match' => 1,
            ],
        ];
    }

    return [
        'query' => $must === [] ? ['match_all' => (object)[]] : ['bool' => ['must' => $must]],
        'highlight_terms' => array_values(array_filter(array_map('strval', $highlightTerms))),
    ];
}

function miyabe_search_build_request(array $params): array
{
    $query = trim((string)($params['q'] ?? ''));
    $docType = miyabe_search_normalize_doc_type((string)($params['doc_type'] ?? ($params['type'] ?? 'all')));
    $page = max(1, (int)($params['page'] ?? 1));
    $perPage = max(1, min(100, (int)($params['per_page'] ?? 20)));
    $sort = trim((string)($params['sort'] ?? 'date'));
    $requestedSlug = trim((string)($params['slug'] ?? ''));
    $resolvedSlug = $requestedSlug !== '' ? resolve_municipality_slug($requestedSlug) : '';
    $slug = $resolvedSlug !== '' ? municipality_public_slug($resolvedSlug) : $requestedSlug;
    $municipalityCode = trim((string)($params['municipality_code'] ?? ($params['code'] ?? '')));
    $prefCode = miyabe_search_normalize_pref_code((string)($params['pref_code'] ?? ($params['pref'] ?? '')));
    $yearFilter = miyabe_search_year_range_filter(
        trim((string)($params['start_year'] ?? '')),
        trim((string)($params['end_year'] ?? ''))
    );

    $queryClause = miyabe_search_build_query_clause($query);
    $filters = [];
    if ($docType !== 'all') {
        $filters[] = ['term' => ['doc_type' => $docType]];
    }
    if ($slug !== '') {
        $filters[] = ['term' => ['slug' => $slug]];
    }
    if ($municipalityCode !== '') {
        $filters[] = ['term' => ['municipality_code' => $municipalityCode]];
    }
    if ($prefCode !== '') {
        $filters[] = ['term' => ['pref_code' => $prefCode]];
    }
    if ($yearFilter !== null) {
        $filters[] = $yearFilter;
    }

    $bodyQuery = $queryClause['query'];
    if ($filters !== []) {
        if (isset($bodyQuery['bool']) && is_array($bodyQuery['bool'])) {
            $bodyQuery['bool']['filter'] = $filters;
        } else {
            $bodyQuery = [
                'bool' => [
                    'must' => [$bodyQuery],
                    'filter' => $filters,
                ],
            ];
        }
    }

    $sortSpec = [['_score' => ['order' => 'desc']]];
    if ($sort === 'date') {
        $sortSpec = [
            ['sort_date' => ['order' => 'desc', 'missing' => '_last']],
            ['_score' => ['order' => 'desc']],
        ];
    }

    return [
        'index' => miyabe_search_alias_for_type($docType),
        'doc_type' => $docType,
        'query' => $query,
        'page' => $page,
        'per_page' => $perPage,
        'body' => [
            'from' => ($page - 1) * $perPage,
            'size' => $perPage,
            'track_total_hits' => true,
            'query' => $bodyQuery,
            'sort' => $sortSpec,
            '_source' => [
                'doc_type',
                'slug',
                'municipality_code',
                'pref_code',
                'pref_name',
                'municipality_name',
                'title',
                'body_length',
                'source_url',
                'detail_url',
                'source_file',
                'source_system',
                'updated_at',
                'sort_date',
                'assembly_name',
                'meeting_name',
                'year_label',
                'held_on',
                'local_id',
                'filename',
                'ordinance_no',
                'category',
                'promulgated_on',
                'enforced_on',
                'amended_on',
            ],
            'highlight' => [
                'pre_tags' => ['[[['],
                'post_tags' => [']]]'],
                'fields' => [
                    'title' => ['number_of_fragments' => 0],
                    'meeting_name' => ['number_of_fragments' => 0],
                    'body' => [
                        'fragment_size' => 160,
                        'number_of_fragments' => 3,
                        'no_match_size' => 160,
                    ],
                ],
            ],
            'aggs' => [
                'doc_types' => ['terms' => ['field' => 'doc_type', 'size' => 5]],
                'prefectures' => ['terms' => ['field' => 'pref_code', 'size' => 50]],
                'municipalities' => ['terms' => ['field' => 'slug', 'size' => 50]],
            ],
        ],
    ];
}

function miyabe_search_first_highlight(array $hit, string $field): string
{
    $values = $hit['highlight'][$field] ?? null;
    if (!is_array($values) || $values === []) {
        return '';
    }
    return trim(implode(' … ', array_map('strval', $values)));
}

function miyabe_search_hit_to_item(array $hit): array
{
    $source = is_array($hit['_source'] ?? null) ? $hit['_source'] : [];
    $titleHighlight = miyabe_search_first_highlight($hit, 'title');
    $meetingHighlight = miyabe_search_first_highlight($hit, 'meeting_name');
    $bodyHighlight = miyabe_search_first_highlight($hit, 'body');
    return [
        'id' => (string)($hit['_id'] ?? ''),
        'score' => isset($hit['_score']) ? (float)$hit['_score'] : null,
        'doc_type' => (string)($source['doc_type'] ?? ''),
        'slug' => (string)($source['slug'] ?? ''),
        'municipality_code' => (string)($source['municipality_code'] ?? ''),
        'pref_code' => (string)($source['pref_code'] ?? ''),
        'pref_name' => (string)($source['pref_name'] ?? ''),
        'municipality_name' => (string)($source['municipality_name'] ?? ''),
        'title' => (string)($source['title'] ?? ''),
        'title_highlight' => $titleHighlight,
        'excerpt' => $bodyHighlight !== '' ? $bodyHighlight : ($meetingHighlight !== '' ? $meetingHighlight : $titleHighlight),
        'body_length' => (int)($source['body_length'] ?? 0),
        'source_url' => (string)($source['source_url'] ?? ''),
        'detail_url' => (string)($source['detail_url'] ?? ''),
        'source_file' => (string)($source['source_file'] ?? ''),
        'source_system' => (string)($source['source_system'] ?? ''),
        'updated_at' => (string)($source['updated_at'] ?? ''),
        'sort_date' => (string)($source['sort_date'] ?? ''),
        'assembly_name' => (string)($source['assembly_name'] ?? ''),
        'meeting_name' => (string)($source['meeting_name'] ?? ''),
        'year_label' => (string)($source['year_label'] ?? ''),
        'held_on' => (string)($source['held_on'] ?? ''),
        'local_id' => (string)($source['local_id'] ?? ''),
        'filename' => (string)($source['filename'] ?? ''),
        'ordinance_no' => (string)($source['ordinance_no'] ?? ''),
        'category' => (string)($source['category'] ?? ''),
        'promulgated_on' => (string)($source['promulgated_on'] ?? ''),
        'enforced_on' => (string)($source['enforced_on'] ?? ''),
        'amended_on' => (string)($source['amended_on'] ?? ''),
    ];
}

function miyabe_search_serialize_aggregations(array $aggregations): array
{
    $serialized = [];
    foreach (['doc_types', 'prefectures', 'municipalities'] as $key) {
        $buckets = $aggregations[$key]['buckets'] ?? [];
        if (!is_array($buckets)) {
            $serialized[$key] = [];
            continue;
        }
        $serialized[$key] = array_values(array_map(
            static fn($bucket): array => [
                'key' => (string)($bucket['key'] ?? ''),
                'count' => (int)($bucket['doc_count'] ?? 0),
            ],
            array_filter($buckets, 'is_array')
        ));
    }
    return $serialized;
}

function miyabe_search_execute_request(array $params): array
{
    $searchRequest = miyabe_search_build_request($params);
    $query = trim((string)$searchRequest['query']);
    if ($query === '') {
        return [
            'status' => 'query_error',
            'error' => 'q を指定してください。',
            'items' => [],
            'total' => 0,
            'total_relation' => 'eq',
        ];
    }

    $index = (string)$searchRequest['index'];
    $response = miyabe_search_http_request('POST', '/' . rawurlencode($index) . '/_search', $searchRequest['body']);
    $hits = is_array($response['hits']['hits'] ?? null) ? $response['hits']['hits'] : [];
    $totalPayload = $response['hits']['total'] ?? 0;
    $total = is_array($totalPayload) ? (int)($totalPayload['value'] ?? 0) : (int)$totalPayload;
    $relation = is_array($totalPayload) ? (string)($totalPayload['relation'] ?? 'eq') : 'eq';
    $page = (int)$searchRequest['page'];
    $perPage = (int)$searchRequest['per_page'];

    return [
        'status' => 'ok',
        'error' => '',
        'query' => $query,
        'doc_type' => (string)$searchRequest['doc_type'],
        'index_alias' => $index,
        'page' => $page,
        'per_page' => $perPage,
        'total' => $total,
        'total_relation' => $relation,
        'has_more' => ($page * $perPage) < $total || $relation !== 'eq',
        'took_ms' => (int)($response['took'] ?? 0),
        'items' => array_values(array_map(
            static fn($hit): array => is_array($hit) ? miyabe_search_hit_to_item($hit) : [],
            $hits
        )),
        'aggregations' => miyabe_search_serialize_aggregations(
            is_array($response['aggregations'] ?? null) ? $response['aggregations'] : []
        ),
    ];
}

function miyabe_search_respond_json(array $payload, int $status = 200): never
{
    http_response_code($status);
    header('Content-Type: application/json; charset=UTF-8');
    header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
    header('X-Content-Type-Options: nosniff');
    echo json_encode(
        $payload,
        JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE
    ) . "\n";
    exit;
}
