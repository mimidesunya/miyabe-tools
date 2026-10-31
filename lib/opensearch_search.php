<?php
declare(strict_types=1);

require_once __DIR__ . DIRECTORY_SEPARATOR . 'japanese_search.php';
require_once __DIR__ . DIRECTORY_SEPARATOR . 'municipalities.php';

// AND 検索で「2 語が近くに現れる文書」を日付順より先に出すときのしきい値。
//
// 語単位（body_terms）は 1 形態素につき表層形と正規形の両方が入るので、25 トークンで
// おおむね 18〜21 形態素になり、LexisNexis の /s（同一文 = 約 25 語）と同じ粒度になる。
// 本番データで測ると会議録の 1 文は中央値 35 文字・p75 61 文字で、slop 40 を超えた
// あたりから「野猿対策事業」のような無関係な共起が混じり始めた。
const MIYABE_SEARCH_PROXIMITY_SLOP = 25;

// 文字単位（body の CJK bigram）のしきい値。bigram の position はほぼ文字位置なので、
// slop がそのまま「2 語の間隔の文字数」になる。語単位の 25 トークンに対応する幅。
const MIYABE_SEARCH_PROXIMITY_CHAR_SLOP = 40;

// OpenSearch の index.max_result_window（既定 10,000）。from + size がこれを超える頁は取れない。
const MIYABE_SEARCH_MAX_RESULT_WINDOW = 10000;

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
        'hyoka' => miyabe_search_env('MIYABE_HYOKA_ALIAS', 'miyabe-hyoka-current'),
        default => miyabe_search_env('MIYABE_MINUTES_ALIAS', 'miyabe-minutes-current'),
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

function miyabe_search_truthy(mixed $value): bool
{
    return is_scalar($value)
        && in_array(strtolower(trim((string)$value)), ['1', 'true', 'yes', 'on'], true);
}

function miyabe_search_normalize_doc_type(string $value): string
{
    $value = strtolower(trim($value));
    return in_array($value, ['reiki', 'hyoka'], true) ? $value : 'minutes';
}

function miyabe_search_hyoka_source_fields(): array
{
    return [
        'fiscal_year', 'kind', 'link_text', 'heading', 'source_page_url',
        'page', 'source_page_link', 'sha256', 'fetched_at', 'status',
        'text_status', 'page_split_reason',
    ];
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

/**
 * 全国地方公共団体コードを索引の形（5 桁）にそろえる。検査数字つきの 6 桁は先頭 5 桁にする。
 * 空なら空、数字 5・6 桁でなければ null（呼び出し側で不正な条件として断る）。
 */
function miyabe_search_normalize_municipality_code(string $value): ?string
{
    $value = trim($value);
    if ($value === '') {
        return '';
    }
    if (preg_match('/^\d{5,6}$/', $value) !== 1) {
        return null;
    }
    return substr($value, 0, 5);
}

function miyabe_search_normalize_date(string $value): string
{
    $value = trim($value);
    if (preg_match('/^(\d{4})-(\d{2})-(\d{2})$/', $value, $matches) !== 1) {
        return '';
    }
    $year = (int)$matches[1];
    $month = (int)$matches[2];
    $day = (int)$matches[3];
    if ($year < 1 || $year > 9999 || !checkdate($month, $day, $year)) {
        return '';
    }
    return sprintf('%04d-%02d-%02d', $year, $month, $day);
}

function miyabe_search_date_from_year(string $year, string $edge): string
{
    $year = trim($year);
    if (preg_match('/^\d{1,4}$/', $year) !== 1) {
        return '';
    }
    $normalizedYear = max(1, min(9999, (int)$year));
    return $edge === 'end'
        ? sprintf('%04d-12-31', $normalizedYear)
        : sprintf('%04d-01-01', $normalizedYear);
}

function miyabe_search_sort_date_range_filter(
    string $startDate,
    string $endDate,
    string $startYear = '',
    string $endYear = ''
): ?array {
    $startDay = miyabe_search_normalize_date($startDate);
    $endDay = miyabe_search_normalize_date($endDate);
    $start = $startDay !== '' ? $startDay : miyabe_search_date_from_year($startYear, 'start');
    $end = $endDay !== '' ? $endDay : miyabe_search_date_from_year($endYear, 'end');
    if ($start !== '' && $end !== '' && $start > $end) {
        // 逆に指定されたら入れ替える。年で指定された側は、入れ替えた先の端（年の初め・終わり）で
        // 取り直す。日付にしてから入れ替えると、2024 年〜2020 年が 2020-12-31〜2024-01-01 に縮む。
        [$start, $end] = [
            $endDay !== '' ? $endDay : miyabe_search_date_from_year($endYear, 'start'),
            $startDay !== '' ? $startDay : miyabe_search_date_from_year($startYear, 'end'),
        ];
    }
    $range = [];
    if ($start !== '') {
        $range['gte'] = $start;
    }
    if ($end !== '') {
        $range['lte'] = $end;
    }
    return $range === [] ? null : ['range' => ['sort_date' => $range]];
}

/**
 * 画面と API の案内どおりの書き方（OR・NOT・AND）を simple_query_string の記号に直す。
 *
 * simple_query_string は語の OR・NOT を演算子として読まず、ただの語として探す。
 * そのままだと「同和 OR 部落」が「同和」「OR」「部落」の AND になり、OR 検索にならない。
 * OR は `|`、NOT は次の語の頭の `-` にし、AND は既定の結合（default_operator=and）なので外す。
 * NEAR（SQLite FTS 時代の近接演算子）は OpenSearch のこの書き方に無いので、AND として外す。
 * 外さないと「near」という語を探して、ほとんど当たらない。
 * 引用符の中は語句なので触らない。大文字・小文字は問わない（ほかの語の切り分けと同じ）。
 */
function miyabe_search_simple_query_syntax(string $query): string
{
    $converted = preg_replace_callback(
        '/"[^"]*"|(?<![^\s()"])(?:AND|OR|NOT|NEAR(?:\/\d+)?)(?![^\s()"])\s*/iu',
        static function (array $matches): string {
            $token = $matches[0];
            if (str_starts_with($token, '"')) {
                return $token;
            }
            return match (strtoupper(trim($token))) {
                'OR' => '| ',
                'NOT' => '-',
                default => '',
            };
        },
        $query
    );
    return trim($converted ?? $query);
}

// simple_query_string に読ませる記号。あいまい検索（~）と語句の距離（"…"~N）は外す。
// 「令和5年～6年」の ～ が半角の ~ になると、後ろの語が距離として読まれて黙って捨てられていた。
const MIYABE_SEARCH_QUERY_FLAGS = 'AND|OR|NOT|PHRASE|PRECEDENCE|PREFIX|WHITESPACE';

// 検索語の長さの上限。文字 2 つずつの組の数だけ句が増え、330 字前後で OpenSearch の句の上限
// （maxClauseCount 1024）を超えて 500 になっていた。
const MIYABE_SEARCH_MAX_QUERY_CHARS = 200;

/**
 * OR・NOT・括弧などの演算子を含むか。引用符の中は語句なので見ない。
 */
function miyabe_search_query_has_operators(string $query): bool
{
    $unquoted = preg_replace('/"[^"]*"/u', ' ', $query) ?? $query;
    return preg_match('/[()|]|(?:^|\s)[+\-]|(?<![^\s()"])(?:AND|OR|NOT|NEAR(?:\/\d+)?)(?![^\s()"])/iu', $unquoted) === 1;
}

function miyabe_search_build_query_clause(string $query, string $docType = 'minutes'): array
{
    $prepared = japanese_search_prepare_query($query);
    $rawQuery = trim((string)($prepared['raw_query'] ?? $query));
    $highlightTerms = is_array($prepared['highlight_terms'] ?? null) ? $prepared['highlight_terms'] : [];
    $termQuery = trim(implode(' ', array_filter(array_map('strval', $highlightTerms))));
    $exactPhrases = japanese_search_exact_phrases_from_prepared($prepared);
    // 語単位の句の multi_match は語を全部 AND で並べるだけなので、OR・NOT を表せない（足すと NOT で
    // 外した文書が戻る）。演算子があるときは、tokenizer が演算子を保ったまま語を Sudachi の語に
    // 置き換えた問い合わせ（term_query）を simple_query_string で引く。外すと複合語の当たり方が
    // 素の検索と大きく変わる（「同和団体」は 1,490 件、語単位を外すと 42 件）。
    // 引用符の語句も simple_query_string が自分で扱えるので、語句の must は足さない。足すと
    // `-"語句"` が「必ず含む」になって 0 件、`"語句" | 語` は OR が効かなくなる。
    $termOperatorQuery = '';
    if (miyabe_search_query_has_operators($rawQuery)) {
        $termQuery = '';
        $exactPhrases = [];
        $termOperatorQuery = trim((string)($prepared['term_query'] ?? ''));
    }

    $must = [];
    $should = [];
    if ($rawQuery !== '') {
        $should[] = [
            'simple_query_string' => [
                'query' => miyabe_search_simple_query_syntax($rawQuery),
                // body.ngram は索引の定義に無い（title.ngram だけ）。
                'fields' => array_merge([
                    'title^4',
                    'title.ngram^1.4',
                    'meeting_name^2',
                    'body^1.5',
                ], $docType === 'hyoka' ? ['link_text^2', 'heading^2'] : []),
                'default_operator' => 'and',
                'flags' => MIYABE_SEARCH_QUERY_FLAGS,
            ],
        ];
    }
    if ($termOperatorQuery !== '') {
        $should[] = [
            'simple_query_string' => [
                'query' => $termOperatorQuery,
                'fields' => [
                    'title_terms^3',
                    'body_terms',
                ],
                'default_operator' => 'and',
                'flags' => MIYABE_SEARCH_QUERY_FLAGS,
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
                'should' => array_merge([
                    ['match_phrase' => ['title' => ['query' => $phrase, 'boost' => 3.0]]],
                    ['match_phrase' => ['meeting_name' => ['query' => $phrase, 'boost' => 2.0]]],
                    ['match_phrase' => ['body' => ['query' => $phrase]]],
                ], $docType === 'hyoka' ? [
                    ['match_phrase' => ['link_text' => ['query' => $phrase]]],
                    ['match_phrase' => ['heading' => ['query' => $phrase]]],
                ] : []),
                'minimum_should_match' => 1,
            ],
        ];
    }

    return [
        'query' => $must === [] ? ['match_all' => (object)[]] : ['bool' => ['must' => $must]],
        // 近接優先ではこの must をまるごと filter へ移し、スコアを近接判定だけに残す。
        'must' => $must,
        'proximity' => miyabe_search_proximity_clauses($prepared, $rawQuery, $termQuery, $exactPhrases),
        'highlight_terms' => array_values(array_filter(array_map('strval', $highlightTerms))),
    ];
}

/**
 * 語が近くに現れるかを見る句。素の AND 検索のときだけ返す。
 *
 * 語単位（body_terms）と文字単位（body の CJK bigram）を両方見て、確かな順に重みを付ける。
 * 語単位のほうが精度は高い（bigram だと「審議会」の中の「議会」を拾ってしまい、実データでも
 * 誤ったヒットが出た）が、索引を作った時点と今で Sudachi の分割が変わっていて、
 * 「ふるさと納税」「情報公開」のような複合語は body_terms 側で語が割れていて当たらない。
 * そこで文字単位を控えめな重みで足し、複合語でも近接優先が働くようにする。
 *
 * 返すのは [句, 重み] の組。重みが大きいものから順に前へ出る。
 */
function miyabe_search_proximity_clauses(
    array $prepared,
    string $rawQuery,
    string $termQuery,
    array $exactPhrases
): array {
    if ($exactPhrases !== []) {
        // 完全一致フレーズは位置を固定して探しているので、重ねて近接を見る意味がない。
        return [];
    }

    $terms = array_values(array_filter(array_map(
        static fn($value): string => trim((string)$value),
        is_array($prepared['highlight_terms'] ?? null) ? $prepared['highlight_terms'] : []
    ), static fn(string $value): bool => $value !== ''));
    if (count($terms) < 2) {
        return [];
    }
    if (!japanese_search_query_is_plain_and($rawQuery)) {
        return [];
    }

    $clauses = [];
    if ($termQuery !== '') {
        $clauses[] = [
            'clause' => [
                'match_phrase' => [
                    'body_terms' => [
                        'query' => $termQuery,
                        'slop' => MIYABE_SEARCH_PROXIMITY_SLOP,
                    ],
                ],
            ],
            'boost' => 2.0,
        ];
    }
    if ($rawQuery !== '') {
        $clauses[] = [
            'clause' => [
                'match_phrase' => [
                    'body' => [
                        'query' => $rawQuery,
                        'slop' => MIYABE_SEARCH_PROXIMITY_CHAR_SLOP,
                    ],
                ],
            ],
            'boost' => 1.0,
        ];
    }
    return $clauses;
}

function miyabe_search_build_request(array $params): array
{
    // 全角空白だけの q を空として断れるよう、ここで表記をそろえる（trim は全角空白を削らない）。
    $query = japanese_search_normalize_query_text((string)($params['q'] ?? ''));
    $docTypeRaw = strtolower(trim((string)($params['doc_type'] ?? ($params['type'] ?? 'minutes'))));
    $docType = miyabe_search_normalize_doc_type($docTypeRaw);
    $page = max(1, (int)($params['page'] ?? 1));
    $perPage = max(1, min(100, (int)($params['per_page'] ?? 20)));
    $sort = trim((string)($params['sort'] ?? 'date'));
    if ($sort === '') {
        $sort = 'date';
    }
    $includeFacets = miyabe_search_truthy($params['include_facets'] ?? '');
    $requestedSlug = trim((string)($params['slug'] ?? ''));
    $resolvedSlug = $requestedSlug !== '' ? resolve_municipality_slug($requestedSlug) : '';
    $slug = $resolvedSlug !== '' ? municipality_public_slug($resolvedSlug) : $requestedSlug;
    // 本文スニペットは既定で返す。body は CJK bigram 解析なのでハイライト追加コストは
    // 1 ページ 20 件あたり 200ms 弱（実測）。不要なら include_body_highlight=0 で外せる。
    $includeBodyHighlightRaw = trim((string)($params['include_body_highlight'] ?? ''));
    $includeBodyHighlight = $includeBodyHighlightRaw === ''
        ? true
        : miyabe_search_truthy($includeBodyHighlightRaw);
    $trackTotalHits = max(1, min(100000, (int)($params['track_total_hits_limit'] ?? 10000)));
    $municipalityCode = miyabe_search_normalize_municipality_code(
        (string)($params['municipality_code'] ?? ($params['code'] ?? ''))
    );
    $prefCodeRaw = trim((string)($params['pref_code'] ?? ($params['pref'] ?? '')));
    $prefCode = miyabe_search_normalize_pref_code($prefCodeRaw);
    $dateParams = [];
    foreach (['start_date', 'end_date', 'start_year', 'end_year'] as $key) {
        $dateParams[$key] = trim((string)($params[$key] ?? ''));
    }
    $yearFilter = miyabe_search_sort_date_range_filter(
        $dateParams['start_date'],
        $dateParams['end_date'],
        $dateParams['start_year'],
        $dateParams['end_year']
    );

    $queryClause = miyabe_search_build_query_clause($query, $docType);
    $filters = [];
    $filters[] = ['term' => ['doc_type' => $docType]];
    if ($slug !== '') {
        $filters[] = ['term' => ['slug' => $slug]];
    }
    if ($municipalityCode !== null && $municipalityCode !== '') {
        $filters[] = ['term' => ['municipality_code' => $municipalityCode]];
    }
    if ($prefCode !== '') {
        $filters[] = ['term' => ['pref_code' => $prefCode]];
    }
    if ($docType !== 'hyoka' && $yearFilter !== null) {
        $filters[] = $yearFilter;
    }
    // 打ち間違いを黙って無視すると、絞ったつもりで全体を見てしまう。fiscal_year・kind と同じく断る。
    $queryError = '';
    if ($docTypeRaw !== '' && !in_array($docTypeRaw, ['minutes', 'reiki', 'hyoka'], true)) {
        $queryError = 'doc_type は minutes / reiki / hyoka を指定してください。';
    }
    if ($requestedSlug !== '' && $resolvedSlug === '') {
        $queryError = 'slug に当たる自治体が見つかりません。';
    }
    if (mb_strlen($query, 'UTF-8') > MIYABE_SEARCH_MAX_QUERY_CHARS) {
        $queryError = sprintf('検索語は%d字までにしてください。', MIYABE_SEARCH_MAX_QUERY_CHARS);
    }
    if (!in_array($sort, ['date', 'relevance'], true)) {
        $queryError = 'sort は date か relevance を指定してください。';
    }
    if ($prefCodeRaw !== '' && ($prefCode === '' || (int)$prefCode < 1 || (int)$prefCode > 47)) {
        $queryError = 'pref_code は都道府県コード（1〜47）を指定してください。';
    }
    if ($municipalityCode === null) {
        $queryError = 'municipality_code は5桁の全国地方公共団体コード（検査数字つきの6桁も可）を指定してください。';
    }
    if ($docType !== 'hyoka') {
        foreach (['start_date', 'end_date'] as $key) {
            if ($dateParams[$key] !== '' && miyabe_search_normalize_date($dateParams[$key]) === '') {
                $queryError = "{$key} は YYYY-MM-DD 形式の日付を指定してください。";
            }
        }
        foreach (['start_year', 'end_year'] as $key) {
            if ($dateParams[$key] !== ''
                && (preg_match('/^\d{1,4}$/', $dateParams[$key]) !== 1 || (int)$dateParams[$key] < 1)) {
                $queryError = "{$key} は西暦の年（1〜9999）を指定してください。";
            }
        }
    }
    // OpenSearch は from + size が上限を超える要求を 400 で断る。そのまま返すと「検索が使えない」
    // と読まれるので、条件の誤りとして先に断る。
    if ($page * $perPage > MIYABE_SEARCH_MAX_RESULT_WINDOW) {
        $queryError = sprintf(
            '検索結果は先頭から%s件目までしか表示できません。条件を足して絞り込んでください。',
            number_format(MIYABE_SEARCH_MAX_RESULT_WINDOW)
        );
    }
    if ($docType === 'hyoka') {
        $fiscalYear = trim((string)($params['fiscal_year'] ?? ''));
        $kind = trim((string)($params['kind'] ?? ''));
        if ($fiscalYear !== '') {
            if (preg_match('/^[1-9][0-9]{3}$/', $fiscalYear) !== 1) {
                $queryError = 'fiscal_year は4桁の西暦年度を指定してください。';
            } else {
                $filters[] = ['term' => ['fiscal_year' => (int)$fiscalYear]];
            }
        }
        if ($kind !== '') {
            if (!in_array($kind, ['project_evaluation', 'policy_evaluation', 'external_review', 'overview'], true)) {
                $queryError = 'kind は project_evaluation / policy_evaluation / external_review / overview を指定してください。';
            } else {
                $filters[] = ['term' => ['kind' => $kind]];
            }
        }
    }

    $proximityClauses = is_array($queryClause['proximity'] ?? null) ? $queryClause['proximity'] : [];
    $clauseMust = is_array($queryClause['must'] ?? null) ? $queryClause['must'] : [];
    // 関連度順は元から近い語を上に出せるので、日付順のときだけ近接を優先する。
    $proximityRanked = $sort === 'date' && $proximityClauses !== [] && $clauseMust !== [];

    if ($proximityRanked) {
        // AND 条件を filter に落としてスコア計算から外し、_score を近接判定の結果だけにする。
        // constant_score なので _score は語単位 2.0 / 文字単位 1.0 の足し合わせにしかならず、
        // [_score, sort_date] の 2 段ソートで「近いものが先、その中では新しい順」が
        // 検索 1 回で出せる。近さの度合いで並べ替えるのではなく、しきい値の内か外かで
        // 前後に分けるのが狙い（内側は語単位で当たったほうが確かなので先に置く）。
        $should = [];
        foreach ($proximityClauses as $entry) {
            $should[] = [
                'constant_score' => [
                    'filter' => $entry['clause'],
                    'boost' => $entry['boost'],
                ],
            ];
        }
        $bodyQuery = [
            'bool' => [
                'filter' => array_merge($filters, $clauseMust),
                'should' => $should,
            ],
        ];
    } else {
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
    }

    $sortSpec = [['_score' => ['order' => 'desc']]];
    if ($sort === 'date') {
        // hyoka の年度は掲載文書の年度。実際の日付や評価実施年度を推測しない。
        $dateField = $docType === 'hyoka' ? 'fiscal_year' : 'sort_date';
        $sortSpec = $proximityRanked
            ? [
                ['_score' => ['order' => 'desc']],
                [$dateField => ['order' => 'desc', 'missing' => '_last']],
            ]
            : [
                [$dateField => ['order' => 'desc', 'missing' => '_last']],
                ['_score' => ['order' => 'desc']],
            ];
    }

    $highlightFields = [
        'title' => ['number_of_fragments' => 0],
        'meeting_name' => ['number_of_fragments' => 0],
    ];
    if ($includeBodyHighlight) {
        $highlightFields['body'] = [
            'fragment_size' => 160,
            'number_of_fragments' => 2,
            'no_match_size' => 160,
            // 会議録 1 件の本文は数百 KB に達することがある。クエリ時解析を先頭 256K 文字で
            // 打ち切り、1MB 超の文書での highlight エラーも防ぐ（OpenSearch 2.2+）。
            'max_analyzer_offset' => 262144,
        ];
    }

    $body = [
        'from' => ($page - 1) * $perPage,
        'size' => $perPage,
        'track_total_hits' => $trackTotalHits,
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
            'fields' => $highlightFields,
        ],
    ];
    if ($docType === 'hyoka') {
        $body['_source'] = array_merge($body['_source'], miyabe_search_hyoka_source_fields());
    }
    if ($includeFacets) {
        $body['aggs'] = [
            'doc_types' => ['terms' => ['field' => 'doc_type', 'size' => 5]],
            'prefectures' => ['terms' => ['field' => 'pref_code', 'size' => 50]],
            'municipalities' => ['terms' => ['field' => 'slug', 'size' => 50]],
        ];
    }

    return [
        'index' => miyabe_search_alias_for_type($docType),
        'doc_type' => $docType,
        'query' => $query,
        'page' => $page,
        'per_page' => $perPage,
        'proximity_ranked' => $proximityRanked,
        'relevance_sorted' => $sort === 'relevance',
        // 自治体を 1 つに絞っていれば、検索語が無くても新しい順の一覧として返す
        // （自治体一覧・収集状況の「検索 N本」から来たときに空の頁にしない）。
        'scoped' => $slug !== '' || ($municipalityCode !== null && $municipalityCode !== ''),
        'query_error' => $queryError,
        'body' => $body,
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

function miyabe_search_local_detail_url(array $hit, array $source, string $query = ''): string
{
    $docType = (string)($source['doc_type'] ?? '');
    if (!in_array($docType, ['minutes', 'hyoka'], true)) {
        return (string)($source['detail_url'] ?? '');
    }

    $id = trim((string)($hit['_id'] ?? ''));
    if ($id === '') {
        return (string)($source['detail_url'] ?? $source['source_url'] ?? '');
    }

    $params = [
        'id' => $id,
        'doc_type' => $docType,
    ];
    $query = trim($query);
    if ($query !== '') {
        $params['q'] = $query;
    }
    return '/search/detail/?' . http_build_query($params);
}

function miyabe_search_api_document_url(array $hit, array $source): string
{
    $id = trim((string)($hit['_id'] ?? ''));
    if ($id === '') {
        return '';
    }

    $docType = miyabe_search_normalize_doc_type((string)($source['doc_type'] ?? 'minutes'));
    return '/api/document?' . http_build_query([
        'id' => $id,
        'doc_type' => $docType,
    ]);
}

function miyabe_search_hit_to_item(
    array $hit,
    string $query = '',
    bool $proximityRanked = false,
    bool $relevanceSorted = true
): array {
    $source = is_array($hit['_source'] ?? null) ? $hit['_source'] : [];
    $titleHighlight = miyabe_search_first_highlight($hit, 'title');
    $meetingHighlight = miyabe_search_first_highlight($hit, 'meeting_name');
    $bodyHighlight = miyabe_search_first_highlight($hit, 'body');
    // 近接優先のときの _score は関連度ではなく 0/1 の判定結果なので、
    // 関連度として読まれないよう score には載せず proximity で返す。
    // 日付順でも並べ替えの 2 段目に _score を置くので値は返ってくるが、並びとは合わないので載せない
    // （API の説明は「日付順のときは null」）。
    $item = [
        'id' => (string)($hit['_id'] ?? ''),
        'score' => ($relevanceSorted && !$proximityRanked && isset($hit['_score'])) ? (float)$hit['_score'] : null,
        'proximity' => $proximityRanked ? ((float)($hit['_score'] ?? 0) >= 1.0) : null,
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
        'detail_url' => miyabe_search_local_detail_url($hit, $source, $query),
        'api_document_url' => miyabe_search_api_document_url($hit, $source),
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
    if (($source['doc_type'] ?? '') === 'hyoka') {
        // 事務事業評価の索引は自治体名に都道府県名を付けて持っている（「北海道 札幌市」）。
        // 会議録・例規とそろえ、都道府県名と二重に出ないよう外す。
        $prefPrefix = $item['pref_name'] !== '' ? $item['pref_name'] . ' ' : '';
        if ($prefPrefix !== '' && str_starts_with($item['municipality_name'], $prefPrefix)) {
            $item['municipality_name'] = substr($item['municipality_name'], strlen($prefPrefix));
        }
        foreach (miyabe_search_hyoka_source_fields() as $field) {
            $item[$field] = in_array($field, ['fiscal_year', 'page'], true)
                ? (isset($source[$field]) ? (int)$source[$field] : null)
                : (string)($source[$field] ?? '');
        }
    }
    return $item;
}

function miyabe_search_hit_to_detail(array $hit): array
{
    // ID で 1 件を引くだけなので、_score に意味はない。
    $item = miyabe_search_hit_to_item($hit, '', false, false);
    $source = is_array($hit['_source'] ?? null) ? $hit['_source'] : [];
    $item['body'] = (string)($source['body'] ?? '');
    $item['indexed_at'] = (string)($source['indexed_at'] ?? '');
    $item['speaker'] = (string)($source['speaker'] ?? '');
    $item['speaker_role'] = (string)($source['speaker_role'] ?? '');
    return $item;
}

function miyabe_search_fetch_detail_document(string $id, string $docType): ?array
{
    $id = trim($id);
    if ($id === '' || strlen($id) > 512) {
        return null;
    }

    // ID は「種別:自治体:…」の形で、種別が頭に入っている。doc_type を付け忘れると既定の会議録の索引を
    // 探して見つからないので、ID の頭を優先する。
    if (preg_match('/^(minutes|reiki|hyoka):/', $id, $matches) === 1) {
        $docType = $matches[1];
    }
    $docType = miyabe_search_normalize_doc_type($docType);
    $filters = [
        ['ids' => ['values' => [$id]]],
    ];
    $filters[] = ['term' => ['doc_type' => $docType]];

    $index = miyabe_search_alias_for_type($docType);
    $response = miyabe_search_http_request('POST', '/' . rawurlencode($index) . '/_search', [
        'size' => 1,
        'track_total_hits' => false,
        'query' => [
            'bool' => [
                'filter' => $filters,
            ],
        ],
        '_source' => array_merge([
            'doc_type',
            'slug',
            'municipality_code',
            'pref_code',
            'pref_name',
            'municipality_name',
            'title',
            'body',
            'body_length',
            'source_url',
            'detail_url',
            'source_file',
            'source_system',
            'indexed_at',
            'updated_at',
            'sort_date',
            'assembly_name',
            'meeting_name',
            'year_label',
            'held_on',
            'speaker',
            'speaker_role',
            'local_id',
            'filename',
            'ordinance_no',
            'category',
            'promulgated_on',
            'enforced_on',
            'amended_on',
        ], $docType === 'hyoka' ? miyabe_search_hyoka_source_fields() : []),
    ]);

    $hits = is_array($response['hits']['hits'] ?? null) ? $response['hits']['hits'] : [];
    foreach ($hits as $hit) {
        if (is_array($hit)) {
            return miyabe_search_hit_to_detail($hit);
        }
    }
    return null;
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
    $missingQuery = $query === '' && !($searchRequest['scoped'] ?? false);
    if ($missingQuery || ($searchRequest['query_error'] ?? '') !== '') {
        return [
            'status' => 'query_error',
            'error' => $missingQuery ? 'q を指定してください（自治体を 1 つに絞るときは省けます）。' : $searchRequest['query_error'],
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
    $proximityRanked = (bool)($searchRequest['proximity_ranked'] ?? false);
    $relevanceSorted = (bool)($searchRequest['relevance_sorted'] ?? false);
    // 上限より先の頁は取れないので、上限に届いたら「次の頁」は無いことにする。
    $reachable = min($total, MIYABE_SEARCH_MAX_RESULT_WINDOW);

    return [
        'status' => 'ok',
        'error' => '',
        'query' => $query,
        'proximity_ranked' => $proximityRanked,
        'doc_type' => (string)$searchRequest['doc_type'],
        'index_alias' => $index,
        'page' => $page,
        'per_page' => $perPage,
        'total' => $total,
        'total_relation' => $relation,
        'has_more' => ($page * $perPage) < MIYABE_SEARCH_MAX_RESULT_WINDOW
            && ($relation === 'eq' ? ($page * $perPage) < $reachable : count($hits) >= $perPage),
        'took_ms' => (int)($response['took'] ?? 0),
        'items' => array_values(array_map(
            static fn($hit): array => is_array($hit)
                ? miyabe_search_hit_to_item($hit, $query, $proximityRanked, $relevanceSorted)
                : [],
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
