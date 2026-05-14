<?php
declare(strict_types=1);

require_once __DIR__ . DIRECTORY_SEPARATOR . 'gijiroku_search.php';

function gijiroku_api_base_path(): string
{
    return '/api/gijiroku';
}

function gijiroku_api_directory_url(): string
{
    return gijiroku_api_base_path() . '/';
}

function gijiroku_api_openapi_url(): string
{
    return gijiroku_api_base_path() . '/openapi.json';
}

function gijiroku_api_openapi_disk_path(): string
{
    return project_root_path()
        . DIRECTORY_SEPARATOR . 'app'
        . DIRECTORY_SEPARATOR . 'api'
        . DIRECTORY_SEPARATOR . 'gijiroku'
        . DIRECTORY_SEPARATOR . 'openapi.json';
}

function gijiroku_api_municipalities_url(): string
{
    return gijiroku_api_base_path() . '/municipalities.php';
}

function gijiroku_api_search_url(?string $slug = null, array $params = []): string
{
    $path = gijiroku_api_base_path() . '/search.php';
    $query = [];
    $normalizedSlug = trim((string)$slug);
    if ($normalizedSlug !== '') {
        $query['slug'] = $normalizedSlug;
    }
    foreach ($params as $key => $value) {
        if (!is_scalar($value) && $value !== null) {
            continue;
        }
        if ($value === null || $value === '') {
            continue;
        }
        $query[(string)$key] = $value;
    }

    return $query === [] ? $path : ($path . '?' . http_build_query($query));
}

function gijiroku_api_search_url_template(): string
{
    return gijiroku_api_base_path() . '/search.php?slug={slug}&q={query}';
}

function gijiroku_api_documents_url(?string $slug = null, array $params = []): string
{
    $path = gijiroku_api_base_path() . '/documents.php';
    $query = [];
    $normalizedSlug = trim((string)$slug);
    if ($normalizedSlug !== '') {
        $query['slug'] = $normalizedSlug;
    }
    foreach ($params as $key => $value) {
        if (!is_scalar($value) && $value !== null) {
            continue;
        }
        if ($value === null || $value === '') {
            continue;
        }
        $query[(string)$key] = $value;
    }

    return $query === [] ? $path : ($path . '?' . http_build_query($query));
}

function gijiroku_api_documents_url_template(): string
{
    return gijiroku_api_base_path() . '/documents.php?slug={slug}&held_on={held_on}';
}

function gijiroku_api_document_url(?string $slug = null, ?int $id = null, array $params = []): string
{
    $path = gijiroku_api_base_path() . '/document.php';
    $query = [];
    $normalizedSlug = trim((string)$slug);
    if ($normalizedSlug !== '') {
        $query['slug'] = $normalizedSlug;
    }
    if ($id !== null && $id > 0) {
        $query['id'] = $id;
    }
    foreach ($params as $key => $value) {
        if (!is_scalar($value) && $value !== null) {
            continue;
        }
        if ($value === null || $value === '') {
            continue;
        }
        $query[(string)$key] = $value;
    }

    return $query === [] ? $path : ($path . '?' . http_build_query($query));
}

function gijiroku_api_document_url_template(): string
{
    return gijiroku_api_base_path() . '/document.php?slug={slug}&id={id}';
}

function gijiroku_api_document_url_template_for_slug(string $slug): string
{
    return gijiroku_api_base_path() . '/document.php?slug=' . rawurlencode($slug) . '&id={id}';
}

function gijiroku_api_encode_json(array $payload, int $options = 0): string
{
    $encoded = json_encode(
        $payload,
        JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE | $options
    );
    if (!is_string($encoded)) {
        throw new RuntimeException('gijiroku API JSON encode failed: ' . json_last_error_msg());
    }

    return $encoded;
}

function gijiroku_api_respond_json(
    array $payload,
    int $status = 200,
    string $contentType = 'application/json; charset=UTF-8',
    string $cacheControl = 'no-store, no-cache, must-revalidate, max-age=0',
    int $jsonOptions = 0
): never {
    http_response_code($status);
    header('Content-Type: ' . $contentType);
    header('Cache-Control: ' . $cacheControl);
    header('X-Content-Type-Options: nosniff');
    echo gijiroku_api_encode_json($payload, $jsonOptions) . "\n";
    exit;
}

function gijiroku_api_request_string(string $key, string $default = ''): string
{
    $value = $_GET[$key] ?? $default;
    return is_scalar($value) ? trim((string)$value) : $default;
}

function gijiroku_api_request_int(string $key, int $default, int $min, int $max): int
{
    $value = $_GET[$key] ?? $default;
    if (!is_scalar($value) || filter_var($value, FILTER_VALIDATE_INT) === false) {
        return $default;
    }

    return max($min, min($max, (int)$value));
}

function gijiroku_api_requested_slug(): string
{
    $requested = gijiroku_api_request_string('slug');
    if ($requested === '') {
        return '';
    }

    return gijiroku_search_resolve_ready_slug($requested);
}

function gijiroku_api_requested_document_id(): int
{
    return gijiroku_api_request_int('id', 0, 0, PHP_INT_MAX);
}

function gijiroku_api_public_summary(array $municipality): array
{
    $summary = gijiroku_search_public_summary($municipality);
    return gijiroku_api_summary_with_urls($summary);
}

function gijiroku_api_summary_with_urls(array $summary): array
{
    $public = gijiroku_search_public_summary($summary);
    $slug = trim((string)($public['slug'] ?? ''));
    $public['search_api_url'] = gijiroku_api_search_url($slug);
    $public['documents_api_url'] = gijiroku_api_documents_url($slug);
    $public['document_api_url_template'] = gijiroku_api_document_url_template_for_slug($slug);
    return $public;
}

function gijiroku_api_catalog_payload(string $query = ''): array
{
    $items = [];
    $normalizedQuery = trim(str_replace('　', ' ', $query));
    $availableTotal = count(gijiroku_search_ready_summaries());
    foreach (gijiroku_search_ready_summaries_for_query($normalizedQuery) as $readySummary) {
        $summary = gijiroku_api_summary_with_urls($readySummary);
        $items[] = $summary;
    }

    return [
        'items' => $items,
        'total' => count($items),
        'available_total' => $availableTotal,
        'query' => $normalizedQuery,
        'openapi_url' => gijiroku_api_openapi_url(),
        'search_url_template' => gijiroku_api_search_url_template(),
        'documents_url_template' => gijiroku_api_documents_url_template(),
        'document_url_template' => gijiroku_api_document_url_template(),
    ];
}

function gijiroku_api_ready_municipality(string $slug): ?array
{
    return gijiroku_search_ready_municipality($slug);
}

function gijiroku_api_search_payload(
    array $municipality,
    string $query,
    int $page,
    int $perPage,
    string $sort = 'date'
): array
{
    $result = gijiroku_search_execute($municipality, $query, $page, $perPage, 0, 0, $sort);
    $summary = gijiroku_api_public_summary($municipality);
    foreach ($summary as $key => $value) {
        $result[$key] = $value;
    }
    $result['query'] = trim($query);
    return $result;
}

function gijiroku_api_documents_payload(array $municipality, string $heldOn, int $page, int $perPage): array
{
    $result = gijiroku_search_list_documents($municipality, $heldOn, $page, $perPage);
    $summary = gijiroku_api_public_summary($municipality);
    foreach ($summary as $key => $value) {
        $result[$key] = $value;
    }
    return $result;
}

function gijiroku_api_document_payload(array $municipality, int $id, string $query = ''): array
{
    $result = gijiroku_search_get_document($municipality, $id, $query);
    $summary = gijiroku_api_public_summary($municipality);
    foreach ($summary as $key => $value) {
        $result[$key] = $value;
    }
    if (($result['status'] ?? '') === 'ok') {
        $result['document_api_url'] = gijiroku_api_document_url(
            trim((string)($summary['slug'] ?? '')),
            (int)($result['id'] ?? 0),
            ['q' => trim($query)]
        );
    }
    return $result;
}

function gijiroku_api_openapi_spec(): array
{
    $catalog = gijiroku_api_catalog_payload();
    $firstMunicipality = $catalog['items'][0] ?? null;
    $slugExample = is_array($firstMunicipality) ? trim((string)($firstMunicipality['slug'] ?? '')) : '';
    if ($slugExample === '') {
        $slugExample = '14130-kawasaki-shi';
    }

    $municipalityExample = [
        'slug' => $slugExample,
        'code' => is_array($firstMunicipality) ? (string)($firstMunicipality['code'] ?? '') : '14130',
        'name' => is_array($firstMunicipality) ? (string)($firstMunicipality['name'] ?? '') : '川崎市',
        'assembly_name' => is_array($firstMunicipality) ? (string)($firstMunicipality['assembly_name'] ?? '') : '川崎市議会',
        'url' => is_array($firstMunicipality) ? (string)($firstMunicipality['url'] ?? '') : '/gijiroku/?slug=14130-kawasaki-shi',
        'search_api_url' => gijiroku_api_search_url($slugExample),
        'documents_api_url' => gijiroku_api_documents_url($slugExample),
        'document_api_url_template' => gijiroku_api_document_url_template_for_slug($slugExample),
    ];

    $documentRowExample = [
        'id' => 123,
        'municipality_slug' => $slugExample,
        'municipality_name' => $municipalityExample['name'],
        'assembly_name' => $municipalityExample['assembly_name'],
        'title' => '令和7年第2回定例会 代表質問',
        'meeting_name' => '令和7年第2回定例会',
        'year_label' => '令和7年',
        'held_on' => '2025-06-18',
        'rel_path' => '令和7年/定例会/代表質問.txt.gz',
        'source_url' => 'https://example.jp/minutes/123',
        'excerpt' => '…[[[補正予算]]] について、学校施設の改修と…',
        'excerpt_plain' => '…補正予算について、学校施設の改修と…',
        'detail_url' => gijiroku_search_detail_url($slugExample, 123, '補正予算'),
        'browse_url' => gijiroku_search_browse_url($slugExample, '補正予算'),
    ];

    $documentDetailExample = [
        'slug' => $slugExample,
        'code' => $municipalityExample['code'],
        'name' => $municipalityExample['name'],
        'assembly_name' => $municipalityExample['assembly_name'],
        'url' => $municipalityExample['url'],
        'search_api_url' => $municipalityExample['search_api_url'],
        'documents_api_url' => $municipalityExample['documents_api_url'],
        'document_api_url_template' => $municipalityExample['document_api_url_template'],
        'document_api_url' => gijiroku_api_document_url($slugExample, 123, ['q' => '補正予算']),
        'status' => 'ok',
        'error' => '',
        'query' => '補正予算',
        'id' => 123,
        'title' => '令和7年第2回定例会 代表質問',
        'meeting_name' => '令和7年第2回定例会',
        'year_label' => '令和7年',
        'held_on' => '2025-06-18',
        'rel_path' => '令和7年/定例会/代表質問.txt.gz',
        'source_url' => 'https://example.jp/minutes/123',
        'source_fino' => '12345',
        'content' => "○議長　これより代表質問を行います。\n○議員　補正予算について伺います。",
        'content_length' => 39,
        'document' => [
            'preamble' => [
                'header' => ['令和7年第2回定例会 代表質問'],
                'meta' => [
                    ['label' => '開催日', 'value' => '2025-06-18'],
                ],
                'agenda' => ['代表質問'],
            ],
            'blocks' => [
                [
                    'type' => 'speech',
                    'anchor' => 'block-1',
                    'mark' => '○',
                    'speaker' => '議長',
                    'role' => '',
                    'body' => 'これより代表質問を行います。',
                    'match_count' => 0,
                ],
                [
                    'type' => 'speech',
                    'anchor' => 'block-2',
                    'mark' => '○',
                    'speaker' => '議員',
                    'role' => '',
                    'body' => '補正予算について伺います。',
                    'match_count' => 1,
                ],
            ],
            'matches' => [
                [
                    'anchor' => 'block-2',
                    'label' => '議員',
                    'preview' => '議員 補正予算について伺います。',
                    'count' => 1,
                ],
            ],
        ],
        'detail_url' => gijiroku_search_detail_url($slugExample, 123, '補正予算'),
        'browse_url' => gijiroku_search_browse_url($slugExample, '補正予算'),
    ];

    return [
        'openapi' => '3.1.0',
        'info' => [
            'title' => 'Miyabe Tools 会議録検索 API',
            'version' => '1.1.0',
            'description' => '既存の会議録検索 UI と同じ SQLite FTS5 ベースの検索・閲覧ロジックを使う、読み取り専用 API です。',
        ],
        'servers' => [
            [
                'url' => 'https://tools.miya.be',
                'description' => 'Miyabe Tools 本番環境',
            ],
        ],
        'externalDocs' => [
            'description' => '既存の会議録横断検索 UI',
            'url' => 'https://tools.miya.be/gijiroku/cross.php',
        ],
        'tags' => [
            [
                'name' => 'Gijiroku',
                'description' => '自治体ごとの会議録検索と全文取得',
            ],
        ],
        'paths' => [
            gijiroku_api_municipalities_url() => [
                'get' => [
                    'tags' => ['Gijiroku'],
                    'operationId' => 'listMinutesMunicipalities',
                    'summary' => '検索可能な自治体一覧を取得',
                    'description' => '会議録検索 DB が利用可能な自治体だけを返します。`q` または `name` を指定すると、自治体名・議会名・都道府県名・自治体コード・slug で部分一致検索できます。',
                    'parameters' => [
                        [
                            'name' => 'q',
                            'in' => 'query',
                            'required' => false,
                            'description' => '自治体名などで絞り込む検索語。空白区切りは AND 条件です。',
                            'schema' => ['type' => 'string'],
                            'example' => '川崎',
                        ],
                        [
                            'name' => 'name',
                            'in' => 'query',
                            'required' => false,
                            'description' => '`q` と同じ自治体名検索用の別名です。`q` が指定されている場合は `q` が優先されます。',
                            'schema' => ['type' => 'string'],
                            'example' => '札幌',
                        ],
                    ],
                    'responses' => [
                        '200' => [
                            'description' => '検索可能な自治体一覧',
                            'content' => [
                                'application/json' => [
                                    'schema' => [
                                        '$ref' => '#/components/schemas/MunicipalityListResponse',
                                    ],
                                    'example' => [
                                        'items' => [$municipalityExample],
                                        'total' => 1,
                                        'available_total' => 1,
                                        'query' => '川崎',
                                        'openapi_url' => gijiroku_api_openapi_url(),
                                        'search_url_template' => gijiroku_api_search_url_template(),
                                        'documents_url_template' => gijiroku_api_documents_url_template(),
                                        'document_url_template' => gijiroku_api_document_url_template(),
                                    ],
                                ],
                            ],
                        ],
                    ],
                ],
            ],
            gijiroku_api_search_url() => [
                'get' => [
                    'tags' => ['Gijiroku'],
                    'operationId' => 'searchMunicipalityMinutes',
                    'summary' => '自治体の会議録を検索',
                    'description' => '一覧 API で取得した `slug` を指定して、その自治体の会議録 DB を検索します。',
                    'parameters' => [
                        [
                            'name' => 'slug',
                            'in' => 'query',
                            'required' => true,
                            'description' => '自治体 slug。自治体一覧 API の `slug` を使います。',
                            'schema' => ['type' => 'string'],
                            'example' => $slugExample,
                        ],
                        [
                            'name' => 'q',
                            'in' => 'query',
                            'required' => true,
                            'description' => '検索キーワード。既存 UI と同じ AND / OR / NOT / NEAR と、引用符でくくるフレーズ一致（例: `"同和地区"`）が使えます。',
                            'schema' => ['type' => 'string'],
                            'example' => '補正予算',
                        ],
                        [
                            'name' => 'page',
                            'in' => 'query',
                            'required' => false,
                            'description' => 'ページ番号。1 始まりです。',
                            'schema' => [
                                'type' => 'integer',
                                'minimum' => 1,
                                'default' => 1,
                            ],
                        ],
                        [
                            'name' => 'per_page',
                            'in' => 'query',
                            'required' => false,
                            'description' => '1 ページあたりの件数。1 から 20 まで。',
                            'schema' => [
                                'type' => 'integer',
                                'minimum' => 1,
                                'maximum' => 20,
                                'default' => 12,
                            ],
                        ],
                        [
                            'name' => 'sort',
                            'in' => 'query',
                            'required' => false,
                            'description' => '`relevance` はFTSの関連度順で高速に返します。`date` は開催日の新しい順です。',
                            'schema' => [
                                'type' => 'string',
                                'enum' => ['relevance', 'date'],
                                'default' => 'relevance',
                            ],
                        ],
                    ],
                    'responses' => [
                        '200' => [
                            'description' => '検索結果',
                            'content' => [
                                'application/json' => [
                                    'schema' => [
                                        '$ref' => '#/components/schemas/MinutesSearchResponse',
                                    ],
                                ],
                            ],
                        ],
                        '404' => [
                            'description' => '自治体が見つからない',
                            'content' => [
                                'application/json' => [
                                    'schema' => [
                                        '$ref' => '#/components/schemas/ErrorResponse',
                                    ],
                                ],
                            ],
                        ],
                        '422' => [
                            'description' => '検索式エラー',
                            'content' => [
                                'application/json' => [
                                    'schema' => [
                                        '$ref' => '#/components/schemas/ErrorResponse',
                                    ],
                                ],
                            ],
                        ],
                    ],
                ],
            ],
            gijiroku_api_documents_url() => [
                'get' => [
                    'tags' => ['Gijiroku'],
                    'operationId' => 'listMunicipalityMinutesDocuments',
                    'summary' => '自治体の会議録一覧を取得',
                    'description' => '最新順の会議録一覧を返します。`held_on` を指定すると特定開催日の会議だけに絞れます。',
                    'parameters' => [
                        [
                            'name' => 'slug',
                            'in' => 'query',
                            'required' => true,
                            'description' => '自治体 slug。',
                            'schema' => ['type' => 'string'],
                            'example' => $slugExample,
                        ],
                        [
                            'name' => 'held_on',
                            'in' => 'query',
                            'required' => false,
                            'description' => '開催日。YYYY-MM-DD 形式。',
                            'schema' => [
                                'type' => 'string',
                                'format' => 'date',
                            ],
                            'example' => '2025-06-18',
                        ],
                        [
                            'name' => 'page',
                            'in' => 'query',
                            'required' => false,
                            'description' => 'ページ番号。1 始まりです。',
                            'schema' => [
                                'type' => 'integer',
                                'minimum' => 1,
                                'default' => 1,
                            ],
                        ],
                        [
                            'name' => 'per_page',
                            'in' => 'query',
                            'required' => false,
                            'description' => '1 ページあたりの件数。1 から 100 まで。',
                            'schema' => [
                                'type' => 'integer',
                                'minimum' => 1,
                                'maximum' => 100,
                                'default' => 20,
                            ],
                        ],
                    ],
                    'responses' => [
                        '200' => [
                            'description' => '会議録一覧',
                            'content' => [
                                'application/json' => [
                                    'schema' => [
                                        '$ref' => '#/components/schemas/MinutesDocumentListResponse',
                                    ],
                                    'example' => [
                                        'slug' => $slugExample,
                                        'code' => $municipalityExample['code'],
                                        'name' => $municipalityExample['name'],
                                        'assembly_name' => $municipalityExample['assembly_name'],
                                        'url' => $municipalityExample['url'],
                                        'search_api_url' => $municipalityExample['search_api_url'],
                                        'documents_api_url' => $municipalityExample['documents_api_url'],
                                        'document_api_url_template' => $municipalityExample['document_api_url_template'],
                                        'status' => 'ok',
                                        'error' => '',
                                        'held_on' => '2025-06-18',
                                        'rows' => [$documentRowExample],
                                        'total' => 1,
                                        'total_exact' => true,
                                        'has_more' => false,
                                        'page' => 1,
                                        'per_page' => 20,
                                        'total_pages' => 1,
                                        'start' => 1,
                                        'end' => 1,
                                    ],
                                ],
                            ],
                        ],
                        '404' => [
                            'description' => '自治体が見つからない',
                            'content' => [
                                'application/json' => [
                                    'schema' => [
                                        '$ref' => '#/components/schemas/ErrorResponse',
                                    ],
                                ],
                            ],
                        ],
                        '422' => [
                            'description' => '日付パラメータエラー',
                            'content' => [
                                'application/json' => [
                                    'schema' => [
                                        '$ref' => '#/components/schemas/ErrorResponse',
                                    ],
                                ],
                            ],
                        ],
                    ],
                ],
            ],
            gijiroku_api_document_url() => [
                'get' => [
                    'tags' => ['Gijiroku'],
                    'operationId' => 'getMunicipalityMinutesDocument',
                    'summary' => '会議録全文を取得',
                    'description' => '会議録本文と、既存ビューアと同じ構造化ブロックを返します。`q` を渡すと一致ブロック情報も返ります。',
                    'parameters' => [
                        [
                            'name' => 'slug',
                            'in' => 'query',
                            'required' => true,
                            'description' => '自治体 slug。',
                            'schema' => ['type' => 'string'],
                            'example' => $slugExample,
                        ],
                        [
                            'name' => 'id',
                            'in' => 'query',
                            'required' => true,
                            'description' => '会議録文書 ID。検索 API または一覧 API の `id` を使います。',
                            'schema' => [
                                'type' => 'integer',
                                'minimum' => 1,
                            ],
                            'example' => 123,
                        ],
                        [
                            'name' => 'q',
                            'in' => 'query',
                            'required' => false,
                            'description' => '任意のキーワード。渡すと一致ブロック一覧を返します。',
                            'schema' => ['type' => 'string'],
                            'example' => '補正予算',
                        ],
                    ],
                    'responses' => [
                        '200' => [
                            'description' => '会議録全文',
                            'content' => [
                                'application/json' => [
                                    'schema' => [
                                        '$ref' => '#/components/schemas/MinutesDocumentResponse',
                                    ],
                                    'example' => $documentDetailExample,
                                ],
                            ],
                        ],
                        '404' => [
                            'description' => '自治体または会議録が見つからない',
                            'content' => [
                                'application/json' => [
                                    'schema' => [
                                        '$ref' => '#/components/schemas/ErrorResponse',
                                    ],
                                ],
                            ],
                        ],
                        '422' => [
                            'description' => 'ID パラメータエラー',
                            'content' => [
                                'application/json' => [
                                    'schema' => [
                                        '$ref' => '#/components/schemas/ErrorResponse',
                                    ],
                                ],
                            ],
                        ],
                    ],
                ],
            ],
        ],
        'components' => [
            'schemas' => [
                'MunicipalitySummary' => [
                    'type' => 'object',
                    'required' => [
                        'slug',
                        'code',
                        'name',
                        'assembly_name',
                        'url',
                        'search_api_url',
                        'documents_api_url',
                        'document_api_url_template',
                    ],
                    'properties' => [
                        'slug' => ['type' => 'string'],
                        'code' => ['type' => 'string'],
                        'name' => ['type' => 'string'],
                        'assembly_name' => ['type' => 'string'],
                        'url' => [
                            'type' => 'string',
                            'description' => '既存の自治体別会議録画面への相対 URL',
                        ],
                        'search_api_url' => [
                            'type' => 'string',
                            'description' => 'この自治体を検索する API URL',
                        ],
                        'documents_api_url' => [
                            'type' => 'string',
                            'description' => 'この自治体の会議一覧を取得する API URL',
                        ],
                        'document_api_url_template' => [
                            'type' => 'string',
                            'description' => '文書全文取得 API の URL テンプレート',
                        ],
                    ],
                ],
                'MunicipalityListResponse' => [
                    'type' => 'object',
                    'required' => [
                        'items',
                        'total',
                        'available_total',
                        'query',
                        'openapi_url',
                        'search_url_template',
                        'documents_url_template',
                        'document_url_template',
                    ],
                    'properties' => [
                        'items' => [
                            'type' => 'array',
                            'items' => [
                                '$ref' => '#/components/schemas/MunicipalitySummary',
                            ],
                        ],
                        'total' => [
                            'type' => 'integer',
                            'minimum' => 0,
                        ],
                        'available_total' => [
                            'type' => 'integer',
                            'minimum' => 0,
                            'description' => '絞り込み前の検索可能自治体数',
                        ],
                        'query' => [
                            'type' => 'string',
                            'description' => '自治体一覧の絞り込みに使った検索語',
                        ],
                        'openapi_url' => ['type' => 'string'],
                        'search_url_template' => ['type' => 'string'],
                        'documents_url_template' => ['type' => 'string'],
                        'document_url_template' => ['type' => 'string'],
                    ],
                ],
                'SearchStats' => [
                    'type' => 'object',
                    'required' => ['last_date', 'latest_hit_date'],
                    'properties' => [
                        'last_date' => [
                            'type' => ['string', 'null'],
                            'format' => 'date',
                        ],
                        'latest_hit_date' => [
                            'type' => ['string', 'null'],
                            'format' => 'date',
                        ],
                    ],
                ],
                'MinutesSearchRow' => [
                    'type' => 'object',
                    'required' => [
                        'id',
                        'municipality_slug',
                        'municipality_name',
                        'assembly_name',
                        'title',
                        'meeting_name',
                        'year_label',
                        'held_on',
                        'rel_path',
                        'source_url',
                        'excerpt',
                        'excerpt_plain',
                        'detail_url',
                        'browse_url',
                    ],
                    'properties' => [
                        'id' => ['type' => 'integer'],
                        'municipality_slug' => ['type' => 'string'],
                        'municipality_name' => ['type' => 'string'],
                        'assembly_name' => ['type' => 'string'],
                        'title' => ['type' => 'string'],
                        'meeting_name' => ['type' => 'string'],
                        'year_label' => ['type' => 'string'],
                        'held_on' => ['type' => 'string', 'format' => 'date'],
                        'rel_path' => ['type' => 'string'],
                        'source_url' => ['type' => 'string'],
                        'excerpt' => [
                            'type' => 'string',
                            'description' => '`[[[` と `]]]` でハイライト範囲を示した抜粋',
                        ],
                        'excerpt_plain' => [
                            'type' => 'string',
                            'description' => 'ハイライト記号を除いた抜粋',
                        ],
                        'detail_url' => ['type' => 'string'],
                        'browse_url' => ['type' => 'string'],
                    ],
                ],
                'MinutesSearchResponse' => [
                    'type' => 'object',
                    'required' => [
                        'slug',
                        'code',
                        'name',
                        'assembly_name',
                        'url',
                        'search_api_url',
                        'documents_api_url',
                        'document_api_url_template',
                        'query',
                        'status',
                        'error',
                        'rows',
                        'total',
                        'page',
                        'per_page',
                        'total_pages',
                        'start',
                        'end',
                        'stats',
                    ],
                    'properties' => [
                        'slug' => ['type' => 'string'],
                        'code' => ['type' => 'string'],
                        'name' => ['type' => 'string'],
                        'assembly_name' => ['type' => 'string'],
                        'url' => ['type' => 'string'],
                        'search_api_url' => ['type' => 'string'],
                        'documents_api_url' => ['type' => 'string'],
                        'document_api_url_template' => ['type' => 'string'],
                        'query' => ['type' => 'string'],
                        'sort' => [
                            'type' => 'string',
                            'enum' => ['relevance', 'date'],
                        ],
                        'status' => [
                            'type' => 'string',
                            'enum' => ['ok', 'query_error', 'missing_db', 'db_error', 'search_error'],
                        ],
                        'error' => ['type' => 'string'],
                        'rows' => [
                            'type' => 'array',
                            'items' => [
                                '$ref' => '#/components/schemas/MinutesSearchRow',
                            ],
                        ],
                        'total' => ['type' => 'integer', 'minimum' => 0],
                        'total_exact' => ['type' => 'boolean'],
                        'has_more' => ['type' => 'boolean'],
                        'candidate_limit_reached' => [
                            'type' => 'boolean',
                            'description' => 'フレーズ一致検索でFTS候補の上限に達した場合は true。true の場合、total は下限です。',
                        ],
                        'candidate_limit' => [
                            'type' => 'integer',
                            'minimum' => 0,
                            'description' => 'フレーズ一致検索でSQL側に渡すFTS候補の上限。0 は候補制限が適用されていないことを示します。',
                        ],
                        'candidates_scanned' => [
                            'type' => 'integer',
                            'minimum' => 0,
                            'description' => 'フレーズ一致検索で評価対象にしたFTS候補数。',
                        ],
                        'page' => ['type' => 'integer', 'minimum' => 1],
                        'per_page' => ['type' => 'integer', 'minimum' => 1],
                        'total_pages' => ['type' => 'integer', 'minimum' => 0],
                        'start' => ['type' => 'integer', 'minimum' => 0],
                        'end' => ['type' => 'integer', 'minimum' => 0],
                        'stats' => [
                            '$ref' => '#/components/schemas/SearchStats',
                        ],
                    ],
                ],
                'MinutesDocumentListResponse' => [
                    'type' => 'object',
                    'required' => [
                        'slug',
                        'code',
                        'name',
                        'assembly_name',
                        'url',
                        'search_api_url',
                        'documents_api_url',
                        'document_api_url_template',
                        'status',
                        'error',
                        'held_on',
                        'rows',
                        'total',
                        'page',
                        'per_page',
                        'total_pages',
                        'start',
                        'end',
                    ],
                    'properties' => [
                        'slug' => ['type' => 'string'],
                        'code' => ['type' => 'string'],
                        'name' => ['type' => 'string'],
                        'assembly_name' => ['type' => 'string'],
                        'url' => ['type' => 'string'],
                        'search_api_url' => ['type' => 'string'],
                        'documents_api_url' => ['type' => 'string'],
                        'document_api_url_template' => ['type' => 'string'],
                        'status' => [
                            'type' => 'string',
                            'enum' => ['ok', 'invalid_date', 'missing_db', 'db_error', 'search_error'],
                        ],
                        'error' => ['type' => 'string'],
                        'held_on' => [
                            'type' => ['string', 'null'],
                            'format' => 'date',
                        ],
                        'rows' => [
                            'type' => 'array',
                            'items' => [
                                '$ref' => '#/components/schemas/MinutesSearchRow',
                            ],
                        ],
                        'total' => ['type' => 'integer', 'minimum' => 0],
                        'total_exact' => ['type' => 'boolean'],
                        'has_more' => ['type' => 'boolean'],
                        'page' => ['type' => 'integer', 'minimum' => 1],
                        'per_page' => ['type' => 'integer', 'minimum' => 1],
                        'total_pages' => ['type' => 'integer', 'minimum' => 0],
                        'start' => ['type' => 'integer', 'minimum' => 0],
                        'end' => ['type' => 'integer', 'minimum' => 0],
                    ],
                ],
                'MinutesDocumentMetaItem' => [
                    'type' => 'object',
                    'required' => ['label', 'value'],
                    'properties' => [
                        'label' => ['type' => 'string'],
                        'value' => ['type' => 'string'],
                    ],
                ],
                'MinutesDocumentMatch' => [
                    'type' => 'object',
                    'required' => ['anchor', 'label', 'preview', 'count'],
                    'properties' => [
                        'anchor' => ['type' => 'string'],
                        'label' => ['type' => 'string'],
                        'preview' => ['type' => 'string'],
                        'count' => ['type' => 'integer', 'minimum' => 0],
                    ],
                ],
                'MinutesDocumentBlock' => [
                    'type' => 'object',
                    'properties' => [
                        'type' => ['type' => 'string'],
                        'kind' => ['type' => 'string'],
                        'anchor' => ['type' => 'string'],
                        'mark' => ['type' => 'string'],
                        'speaker' => ['type' => 'string'],
                        'role' => ['type' => 'string'],
                        'body' => ['type' => 'string'],
                        'match_count' => ['type' => 'integer', 'minimum' => 0],
                    ],
                ],
                'MinutesDocumentStructured' => [
                    'type' => 'object',
                    'required' => ['preamble', 'blocks', 'matches'],
                    'properties' => [
                        'preamble' => [
                            'type' => 'object',
                            'required' => ['header', 'meta', 'agenda'],
                            'properties' => [
                                'header' => [
                                    'type' => 'array',
                                    'items' => ['type' => 'string'],
                                ],
                                'meta' => [
                                    'type' => 'array',
                                    'items' => [
                                        '$ref' => '#/components/schemas/MinutesDocumentMetaItem',
                                    ],
                                ],
                                'agenda' => [
                                    'type' => 'array',
                                    'items' => ['type' => 'string'],
                                ],
                            ],
                        ],
                        'blocks' => [
                            'type' => 'array',
                            'items' => [
                                '$ref' => '#/components/schemas/MinutesDocumentBlock',
                            ],
                        ],
                        'matches' => [
                            'type' => 'array',
                            'items' => [
                                '$ref' => '#/components/schemas/MinutesDocumentMatch',
                            ],
                        ],
                    ],
                ],
                'MinutesDocumentResponse' => [
                    'type' => 'object',
                    'required' => [
                        'slug',
                        'code',
                        'name',
                        'assembly_name',
                        'url',
                        'search_api_url',
                        'documents_api_url',
                        'document_api_url_template',
                        'status',
                        'error',
                        'query',
                    ],
                    'properties' => [
                        'slug' => ['type' => 'string'],
                        'code' => ['type' => 'string'],
                        'name' => ['type' => 'string'],
                        'assembly_name' => ['type' => 'string'],
                        'url' => ['type' => 'string'],
                        'search_api_url' => ['type' => 'string'],
                        'documents_api_url' => ['type' => 'string'],
                        'document_api_url_template' => ['type' => 'string'],
                        'document_api_url' => ['type' => 'string'],
                        'status' => [
                            'type' => 'string',
                            'enum' => ['ok', 'invalid_id', 'missing_db', 'db_error', 'search_error', 'not_found'],
                        ],
                        'error' => ['type' => 'string'],
                        'query' => ['type' => 'string'],
                        'id' => ['type' => 'integer'],
                        'title' => ['type' => 'string'],
                        'meeting_name' => ['type' => 'string'],
                        'year_label' => ['type' => 'string'],
                        'held_on' => ['type' => 'string', 'format' => 'date'],
                        'rel_path' => ['type' => 'string'],
                        'source_url' => ['type' => 'string'],
                        'source_fino' => ['type' => 'string'],
                        'content' => ['type' => 'string'],
                        'content_length' => ['type' => 'integer', 'minimum' => 0],
                        'document' => [
                            '$ref' => '#/components/schemas/MinutesDocumentStructured',
                        ],
                        'detail_url' => ['type' => 'string'],
                        'browse_url' => ['type' => 'string'],
                    ],
                ],
                'ErrorResponse' => [
                    'type' => 'object',
                    'required' => ['error'],
                    'properties' => [
                        'error' => ['type' => 'string'],
                    ],
                ],
            ],
        ],
    ];
}
