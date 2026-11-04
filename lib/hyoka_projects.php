<?php
declare(strict_types=1);

// 事務事業評価の事業ごとの索引（tools/search/build_hyoka_projects_index.py が作る）を引く。
// 事業を条件で探す（/api/hyoka-projects.php）、金額を集計する（/api/hyoka-aggregate.php）、
// 事業 1 件の全部を返す（/api/hyoka-project.php）の 3 つが使う。MCP はこの API を呼ぶだけ。
// 金額は索引を作るときに円にそろえてある。集計は、同じ事業・年度・費目の金額を 1 つだけ数える
// （索引の amounts.best。決算 → 見込 → 予算 → 計画、同じ段階なら新しい文書の値）。

require_once __DIR__ . DIRECTORY_SEPARATOR . 'opensearch_search.php';
require_once __DIR__ . DIRECTORY_SEPARATOR . 'hyoka_display.php';

const MIYABE_HYOKA_PROJECT_DIRECTIONS = ['expand', 'continue', 'maintain', 'improve', 'reduce', 'integrate', 'suspend', 'abolish', 'complete', 'other'];
const MIYABE_HYOKA_PROJECT_METHODS = ['program', 'ai', 'machine'];
// 分析の印（tools/search/hyoka_projects.py の FLAG_NAMES）。原典の値から機械で付けたもので、問題があると決めたものではない。
const MIYABE_HYOKA_PROJECT_FLAGS = ['outcome_below_half', 'expand_below_target', 'cost_up_below_target', 'no_outcome_target',
    'repeated_text', 'renamed', 'not_in_latest'];
const MIYABE_HYOKA_PROJECT_SORTS = ['relevance', 'cost_desc', 'cost_asc', 'cost_change_desc', 'outcome_ratio', 'year_desc'];
const MIYABE_HYOKA_AMOUNT_SCOPES = [
    'total', 'direct', 'personnel', 'sub_project', 'breakdown', 'other',
    'funding_national', 'funding_prefecture', 'funding_bond', 'funding_fund', 'funding_user_fee',
    'funding_other_specific', 'funding_general', 'funding_other',
];
const MIYABE_HYOKA_AMOUNT_STAGES = ['best', 'settlement', 'estimate', 'budget', 'plan'];
const MIYABE_HYOKA_AMOUNT_GROUPS = [
    'none' => '',
    'municipality' => 'amounts.slug',
    'prefecture' => 'amounts.pref_code',
    'department' => 'amounts.department',
    'direction' => 'amounts.direction',
    'year' => 'amounts.year',
];
// 一覧に返す項目。金額の明細（amounts）は大きいので、事業 1 件を引くときだけ返す。
const MIYABE_HYOKA_PROJECT_LIST_FIELDS = [
    'project_id', 'record_id', 'doc_id', 'document_id', 'document_title', 'document_fiscal_year', 'document_kind',
    'slug', 'municipality_code', 'municipality_name', 'pref_code', 'pref_name',
    'project_name', 'project_code', 'department', 'purpose', 'evaluation_year', 'rating', 'direction', 'direction_text',
    'aspects', 'method', 'page', 'location', 'source_url', 'source_page_link',
    'cost_yen', 'cost_year', 'cost_stage', 'cost_scope', 'cost_raw', 'cost_unit', 'cost_prev_yen', 'cost_change_ratio',
    'staff_fte', 'has_amounts',
    'outcome_ratio_min', 'indicator_ratio_min', 'indicators', 'flags', 'project_names', 'project_first_year', 'project_last_year',
    'text_repeat_ratio',
];

function miyabe_hyoka_projects_alias(): string
{
    return miyabe_search_env('MIYABE_HYOKA_PROJECTS_ALIAS', 'miyabe-hyoka-projects-current');
}

/** 「a,b」や配列で来る値を、空でない文字の一覧にする。 */
function miyabe_hyoka_projects_list(mixed $value, int $max = 10, int $maxChars = 40): ?array
{
    if ($value === null || $value === '') {
        return [];
    }
    $items = is_array($value) ? $value : explode(',', (string)$value);
    $out = [];
    foreach ($items as $item) {
        if (!is_scalar($item)) {
            return null;
        }
        $item = trim((string)$item);
        if ($item === '') {
            continue;
        }
        if (mb_strlen($item, 'UTF-8') > $maxChars) {
            return null;
        }
        // 鍵にすると「4」のような数字の文字が整数になり、索引の keyword と合わなくなる。
        if (!in_array($item, $out, true)) {
            $out[] = $item;
        }
    }
    return count($out) > $max ? null : $out;
}

function miyabe_hyoka_projects_int(mixed $value, int $min, int $max): ?int
{
    if ($value === null || $value === '') {
        return null;
    }
    if (!is_scalar($value) || preg_match('/^-?\d+$/', trim((string)$value)) !== 1) {
        return PHP_INT_MIN;
    }
    $number = (int)trim((string)$value);
    return $number < $min || $number > $max ? PHP_INT_MIN : $number;
}

function miyabe_hyoka_projects_bool(mixed $value): ?bool
{
    if ($value === null || $value === '') {
        return null;
    }
    $text = is_scalar($value) ? strtolower(trim((string)$value)) : '';
    if (in_array($text, ['1', 'true', 'yes', 'on'], true)) {
        return true;
    }
    if (in_array($text, ['0', 'false', 'no', 'off'], true)) {
        return false;
    }
    return null;
}

/**
 * 事業を絞る条件（探す・集計で共通）。返すのは ['params' => …, 'error' => 断る理由（無ければ空）]。
 * 受けるのは q, slug, municipality_code, pref_code, evaluation_year, department, direction, rating, aspect,
 * method, flag, has_amounts, min_cost, max_cost, max_outcome_ratio, below_target, min_cost_change_ratio。
 */
function miyabe_hyoka_projects_filters(array $input): array
{
    $string = static fn(string $key): string => is_scalar($input[$key] ?? null) ? trim((string)$input[$key]) : '';
    $params = [
        'q' => $string('q'),
        'slug' => $string('slug'),
        'municipality_code' => '',
        'pref_code' => '',
        'department' => $string('department'),
    ];
    if (mb_strlen($params['q'], 'UTF-8') > MIYABE_SEARCH_MAX_QUERY_CHARS) {
        return ['params' => $params, 'error' => 'q は ' . MIYABE_SEARCH_MAX_QUERY_CHARS . ' 字までにしてください。'];
    }
    if ($params['slug'] !== '' && preg_match('/^[0-9]{5}-[a-z0-9-]{1,60}$/', $params['slug']) !== 1) {
        return ['params' => $params, 'error' => 'slug の形が正しくありません。'];
    }
    $code = miyabe_search_normalize_municipality_code($string('municipality_code'));
    if ($code === null) {
        return ['params' => $params, 'error' => 'municipality_code は 5 桁（検査数字つきは 6 桁）の数字にしてください。'];
    }
    $params['municipality_code'] = $code;
    $prefRaw = $string('pref_code');
    $params['pref_code'] = miyabe_search_normalize_pref_code($prefRaw);
    if ($prefRaw !== '' && $params['pref_code'] === '') {
        return ['params' => $params, 'error' => 'pref_code は 2 桁の都道府県コードにしてください。'];
    }
    if (mb_strlen($params['department'], 'UTF-8') > 100) {
        return ['params' => $params, 'error' => 'department は 100 字までにしてください。'];
    }
    $year = miyabe_hyoka_projects_int($input['evaluation_year'] ?? null, 1000, 9999);
    if ($year === PHP_INT_MIN) {
        return ['params' => $params, 'error' => 'evaluation_year は西暦 4 桁の年度にしてください。'];
    }
    $params['evaluation_year'] = $year;
    $lists = [
        'direction' => [MIYABE_HYOKA_PROJECT_DIRECTIONS, 'direction は ' . implode(' / ', MIYABE_HYOKA_PROJECT_DIRECTIONS) . ' から選んでください（カンマ区切りで複数）。'],
        'rating' => [null, 'rating は原典の評価の記載をカンマ区切りで 10 個まで（各 40 字まで）にしてください。'],
        'aspect' => [null, 'aspect は「成果:c」の形をカンマ区切りで 10 個までにしてください。'],
        'method' => [MIYABE_HYOKA_PROJECT_METHODS, 'method は program / ai / machine から選んでください。'],
        'flag' => [MIYABE_HYOKA_PROJECT_FLAGS, 'flag は ' . implode(' / ', MIYABE_HYOKA_PROJECT_FLAGS) . ' から選んでください（カンマ区切りで複数、すべてを持つ事業）。'],
    ];
    foreach ($lists as $key => [$allowed, $message]) {
        $values = miyabe_hyoka_projects_list($input[$key] ?? null);
        if ($values === null || ($allowed !== null && array_diff($values, $allowed) !== [])) {
            return ['params' => $params, 'error' => $message];
        }
        if ($key === 'aspect') {
            // 観点は索引で空白を詰めてある（「成果: c」→「成果:c」）。
            $values = array_map(static fn(string $value): string => preg_replace('/\s+/u', '', $value) ?? $value, $values);
        }
        $params[$key] = $values;
    }
    foreach (['min_cost', 'max_cost'] as $key) {
        $value = miyabe_hyoka_projects_int($input[$key] ?? null, 0, PHP_INT_MAX);
        if ($value === PHP_INT_MIN) {
            return ['params' => $params, 'error' => $key . ' は 0 以上の整数（円）にしてください。'];
        }
        $params[$key] = $value;
    }
    $params['has_amounts'] = miyabe_hyoka_projects_bool($input['has_amounts'] ?? null);
    foreach (['max_outcome_ratio' => '実績 ÷ 目標', 'min_cost_change_ratio' => '今年の費用 ÷ 前年の費用'] as $key => $meaning) {
        $ratio = $input[$key] ?? null;
        $params[$key] = null;
        if ($ratio === null || $ratio === '') {
            continue;
        }
        if (!is_scalar($ratio) || !is_numeric(trim((string)$ratio)) || (float)$ratio < 0 || (float)$ratio > 1000) {
            return ['params' => $params, 'error' => $key . ' は 0 以上の数（' . $meaning . '）にしてください。'];
        }
        $params[$key] = (float)$ratio;
    }
    if (miyabe_hyoka_projects_bool($input['below_target'] ?? null) === true && $params['max_outcome_ratio'] === null) {
        $params['max_outcome_ratio'] = 0.9999;
    }
    return ['params' => $params, 'error' => ''];
}

function miyabe_hyoka_projects_query_clause(string $q): ?array
{
    if ($q === '') {
        return null;
    }
    return [
        'simple_query_string' => [
            'query' => miyabe_search_simple_query_syntax($q),
            'fields' => ['project_name^5', 'project_name.ngram^2', 'department^2', 'department.ngram', 'purpose', 'direction_text', 'search_text'],
            'default_operator' => 'and',
            'flags' => MIYABE_SEARCH_QUERY_FLAGS,
        ],
    ];
}

/** 事業（索引の 1 件）に掛ける条件。金額の明細ではなく事業の側の値で絞る。 */
function miyabe_hyoka_projects_filter_clauses(array $params): array
{
    $filters = [];
    if ($params['slug'] !== '') {
        $filters[] = ['term' => ['slug' => $params['slug']]];
    }
    if ($params['municipality_code'] !== '') {
        $filters[] = ['term' => ['municipality_code' => $params['municipality_code']]];
    }
    if ($params['pref_code'] !== '') {
        $filters[] = ['term' => ['pref_code' => $params['pref_code']]];
    }
    if ($params['evaluation_year'] !== null) {
        $filters[] = ['term' => ['evaluation_year' => $params['evaluation_year']]];
    }
    if ($params['department'] !== '') {
        $filters[] = ['match' => ['department' => ['query' => $params['department'], 'operator' => 'and']]];
    }
    foreach (['direction' => 'direction', 'rating' => 'rating', 'aspect' => 'aspects', 'method' => 'method'] as $key => $field) {
        if ($params[$key] !== []) {
            $filters[] = ['terms' => [$field => $params[$key]]];
        }
    }
    // 印は「どれか」ではなく「すべて」を持つ事業（目標割れ かつ 費用増、のように重ねて使う）。
    foreach ($params['flag'] as $flag) {
        $filters[] = ['term' => ['flags' => $flag]];
    }
    if ($params['has_amounts'] !== null) {
        $filters[] = ['term' => ['has_amounts' => $params['has_amounts']]];
    }
    $range = array_filter(['gte' => $params['min_cost'], 'lte' => $params['max_cost']], static fn($value): bool => $value !== null);
    if ($range !== []) {
        $filters[] = ['range' => ['cost_yen' => $range]];
    }
    if ($params['max_outcome_ratio'] !== null) {
        $filters[] = ['range' => ['outcome_ratio_min' => ['lte' => $params['max_outcome_ratio']]]];
    }
    if ($params['min_cost_change_ratio'] !== null) {
        $filters[] = ['range' => ['cost_change_ratio' => ['gte' => $params['min_cost_change_ratio']]]];
    }
    return $filters;
}

function miyabe_hyoka_projects_bool_query(array $params): array
{
    $bool = ['filter' => miyabe_hyoka_projects_filter_clauses($params)];
    $clause = miyabe_hyoka_projects_query_clause($params['q']);
    if ($clause !== null) {
        $bool['must'] = [$clause];
        // 同じ事業名が総括や施策の表の行にも出るので、事業ごとのシート（事務事業評価の文書・総費用の行がある記録）を先に出す。
        $bool['should'] = [
            ['term' => ['document_kind' => ['value' => 'project_evaluation', 'boost' => 2.0]]],
            ['exists' => ['field' => 'staff_fte', 'boost' => 1.0]],
        ];
    }
    return $bool['filter'] === [] && !isset($bool['must']) ? ['match_all' => (object)[]] : ['bool' => $bool];
}

/** 事業を探す要求。返すのは ['body' => …, 'params' => …, 'error' => …]。 */
function miyabe_hyoka_projects_search_request(array $input): array
{
    $filtered = miyabe_hyoka_projects_filters($input);
    $params = $filtered['params'];
    if ($filtered['error'] !== '') {
        return ['body' => null, 'params' => $params, 'error' => $filtered['error']];
    }
    $sort = is_scalar($input['sort'] ?? null) ? trim((string)$input['sort']) : '';
    $sort = $sort !== '' ? $sort : ($params['q'] !== '' ? 'relevance' : 'cost_desc');
    if (!in_array($sort, MIYABE_HYOKA_PROJECT_SORTS, true)) {
        return ['body' => null, 'params' => $params, 'error' => 'sort は ' . implode(' / ', MIYABE_HYOKA_PROJECT_SORTS) . ' から選んでください。'];
    }
    $page = miyabe_hyoka_projects_int($input['page'] ?? null, 1, 10000) ?? 1;
    $perPage = miyabe_hyoka_projects_int($input['per_page'] ?? null, 1, 100) ?? 20;
    if ($page === PHP_INT_MIN || $perPage === PHP_INT_MIN) {
        return ['body' => null, 'params' => $params, 'error' => 'page は 1 以上、per_page は 1〜100 にしてください。'];
    }
    if ($page * $perPage > 10000) {
        return ['body' => null, 'params' => $params, 'error' => '先頭から 10000 件目までしか取れません（page × per_page を 10000 以下に）。'];
    }
    $params += ['sort' => $sort, 'page' => $page, 'per_page' => $perPage];
    $byCost = static fn(string $order): array => ['cost_yen' => ['order' => $order, 'missing' => '_last']];
    $sortBody = match ($sort) {
        'cost_desc' => [$byCost('desc'), '_score', ['project_id' => 'asc']],
        'cost_asc' => [$byCost('asc'), '_score', ['project_id' => 'asc']],
        'cost_change_desc' => [['cost_change_ratio' => ['order' => 'desc', 'missing' => '_last']], $byCost('desc'), ['project_id' => 'asc']],
        'outcome_ratio' => [['outcome_ratio_min' => ['order' => 'asc', 'missing' => '_last']], $byCost('desc'), ['project_id' => 'asc']],
        'year_desc' => [['evaluation_year' => ['order' => 'desc', 'missing' => '_last']], $byCost('desc'), ['project_id' => 'asc']],
        default => ['_score', $byCost('desc'), ['project_id' => 'asc']],
    };
    return [
        'body' => [
            'from' => ($page - 1) * $perPage,
            'size' => $perPage,
            'track_total_hits' => 10000,
            'query' => miyabe_hyoka_projects_bool_query($params),
            'sort' => $sortBody,
            '_source' => MIYABE_HYOKA_PROJECT_LIST_FIELDS,
        ],
        'params' => $params,
        'error' => '',
    ];
}

/** 金額を集計する要求。year（または group_by=year の from_year・to_year）、scope、stage、group_by、limit、top。 */
function miyabe_hyoka_projects_aggregate_request(array $input): array
{
    $filtered = miyabe_hyoka_projects_filters($input);
    $params = $filtered['params'];
    if ($filtered['error'] !== '') {
        return ['body' => null, 'params' => $params, 'error' => $filtered['error']];
    }
    $string = static fn(string $key, string $default): string => is_scalar($input[$key] ?? null) && trim((string)$input[$key]) !== ''
        ? trim((string)$input[$key]) : $default;
    $scope = $string('scope', 'total');
    $stage = $string('stage', 'best');
    $groupBy = $string('group_by', 'none');
    if (!in_array($scope, MIYABE_HYOKA_AMOUNT_SCOPES, true)) {
        return ['body' => null, 'params' => $params, 'error' => 'scope は ' . implode(' / ', MIYABE_HYOKA_AMOUNT_SCOPES) . ' から選んでください。'];
    }
    if (!in_array($stage, MIYABE_HYOKA_AMOUNT_STAGES, true)) {
        return ['body' => null, 'params' => $params, 'error' => 'stage は ' . implode(' / ', MIYABE_HYOKA_AMOUNT_STAGES) . ' から選んでください。'];
    }
    if (!array_key_exists($groupBy, MIYABE_HYOKA_AMOUNT_GROUPS)) {
        return ['body' => null, 'params' => $params, 'error' => 'group_by は ' . implode(' / ', array_keys(MIYABE_HYOKA_AMOUNT_GROUPS)) . ' から選んでください。'];
    }
    $year = miyabe_hyoka_projects_int($input['year'] ?? null, 1000, 9999);
    $fromYear = miyabe_hyoka_projects_int($input['from_year'] ?? null, 1000, 9999);
    $toYear = miyabe_hyoka_projects_int($input['to_year'] ?? null, 1000, 9999);
    $limit = miyabe_hyoka_projects_int($input['limit'] ?? null, 1, 200) ?? 20;
    $top = miyabe_hyoka_projects_int($input['top'] ?? null, 0, 20) ?? 5;
    if (in_array(PHP_INT_MIN, [$year, $fromYear, $toYear], true)) {
        return ['body' => null, 'params' => $params, 'error' => 'year・from_year・to_year は西暦 4 桁の年度にしてください。'];
    }
    if ($limit === PHP_INT_MIN || $top === PHP_INT_MIN) {
        return ['body' => null, 'params' => $params, 'error' => 'limit は 1〜200、top は 0〜20 にしてください。'];
    }
    if ($groupBy === 'year') {
        if ($year !== null) {
            $fromYear = $toYear = $year;
        }
    } elseif ($year === null) {
        // 年度をまたいで足すと、同じ事業の毎年の金額を足した数になり、意味が無い。
        return ['body' => null, 'params' => $params, 'error' => 'year（金額の年度）を指定してください。年度ごとの推移は group_by=year と from_year・to_year で出せます。'];
    }
    if ($fromYear !== null && $toYear !== null && $fromYear > $toYear) {
        return ['body' => null, 'params' => $params, 'error' => 'from_year は to_year 以下にしてください。'];
    }
    $params += ['year' => $year, 'from_year' => $fromYear, 'to_year' => $toYear, 'scope' => $scope, 'stage' => $stage,
        'group_by' => $groupBy, 'limit' => $limit, 'top' => $top];

    $factFilters = [['term' => ['amounts.scope' => $scope]]];
    if ($stage === 'best') {
        $factFilters[] = ['term' => ['amounts.best' => true]];
    } else {
        $factFilters[] = ['term' => ['amounts.stage' => $stage]];
        $factFilters[] = ['term' => ['amounts.stage_best' => true]];
    }
    if ($year !== null && $groupBy !== 'year') {
        $factFilters[] = ['term' => ['amounts.year' => $year]];
    } else {
        $range = array_filter(['gte' => $fromYear, 'lte' => $toYear], static fn($value): bool => $value !== null);
        if ($range !== []) {
            $factFilters[] = ['range' => ['amounts.year' => $range]];
        }
    }
    $picked = [
        'sum' => ['sum' => ['field' => 'amounts.yen']],
        'municipalities' => ['cardinality' => ['field' => 'amounts.slug']],
    ];
    $field = MIYABE_HYOKA_AMOUNT_GROUPS[$groupBy];
    if ($field !== '') {
        $groups = ['terms' => [
            'field' => $field,
            'size' => $limit,
            'order' => $groupBy === 'year' ? ['_key' => 'asc'] : ['sum' => 'desc'],
        ], 'aggs' => ['sum' => ['sum' => ['field' => 'amounts.yen']]]];
        if ($groupBy === 'municipality') {
            $groups['aggs']['label'] = ['terms' => ['field' => 'amounts.municipality_name', 'size' => 1]];
        }
        $picked['groups'] = $groups;
    }
    if ($top > 0) {
        $picked['top'] = ['top_hits' => ['size' => $top, 'sort' => [['amounts.yen' => ['order' => 'desc']]]]];
    }
    $bool = miyabe_hyoka_projects_bool_query($params);
    return [
        'body' => [
            'size' => 0,
            'track_total_hits' => true,
            'query' => $bool,
            'aggs' => [
                'facts' => [
                    'nested' => ['path' => 'amounts'],
                    'aggs' => ['picked' => ['filter' => ['bool' => ['filter' => $factFilters]], 'aggs' => $picked]],
                ],
            ],
        ],
        'params' => $params,
        'error' => '',
    ];
}

function miyabe_hyoka_projects_detail_url(string $documentId): string
{
    return $documentId === '' ? '' : '/search/detail/?' . http_build_query(['id' => $documentId, 'doc_type' => 'hyoka']);
}

/** 索引の 1 件を、一覧に返す形にする。 */
function miyabe_hyoka_projects_hit_to_item(array $hit): array
{
    $source = is_array($hit['_source'] ?? null) ? $hit['_source'] : [];
    $item = [];
    foreach (MIYABE_HYOKA_PROJECT_LIST_FIELDS as $field) {
        $item[$field] = $source[$field] ?? null;
    }
    $item['id'] = (string)($source['project_id'] ?? ($hit['_id'] ?? ''));
    $item['score'] = isset($hit['_score']) && is_numeric($hit['_score']) ? (float)$hit['_score'] : null;
    $item['detail_url'] = miyabe_hyoka_projects_detail_url((string)($source['document_id'] ?? ''));
    $item['api_project_url'] = '/api/hyoka-project.php?' . http_build_query(['id' => $item['id']]);
    foreach (['aspects', 'indicators', 'flags', 'project_names'] as $field) {
        $item[$field] = is_array($item[$field]) ? $item[$field] : [];
    }
    return $item;
}

function miyabe_hyoka_projects_execute_search(array $input): array
{
    $request = miyabe_hyoka_projects_search_request($input);
    $params = $request['params'];
    if ($request['error'] !== '') {
        return ['status' => 'query_error', 'error' => $request['error']];
    }
    $alias = miyabe_hyoka_projects_alias();
    $response = miyabe_search_http_request('POST', '/' . rawurlencode($alias) . '/_search', $request['body']);
    $hits = is_array($response['hits']['hits'] ?? null) ? $response['hits']['hits'] : [];
    $total = (int)($response['hits']['total']['value'] ?? 0);
    $relation = (string)($response['hits']['total']['relation'] ?? 'eq');
    return [
        'status' => 'ok',
        'error' => '',
        'query' => $params['q'],
        'index_alias' => $alias,
        'sort' => $params['sort'],
        'page' => $params['page'],
        'per_page' => $params['per_page'],
        'total' => $total,
        'total_relation' => $relation,
        'has_more' => $params['page'] * $params['per_page'] < $total,
        'took_ms' => (int)($response['took'] ?? 0),
        'items' => array_map('miyabe_hyoka_projects_hit_to_item', $hits),
    ];
}

function miyabe_hyoka_projects_execute_aggregate(array $input): array
{
    $request = miyabe_hyoka_projects_aggregate_request($input);
    $params = $request['params'];
    if ($request['error'] !== '') {
        return ['status' => 'query_error', 'error' => $request['error']];
    }
    $alias = miyabe_hyoka_projects_alias();
    $response = miyabe_search_http_request('POST', '/' . rawurlencode($alias) . '/_search', $request['body']);
    $picked = $response['aggregations']['facts']['picked'] ?? [];
    $groups = [];
    foreach (($picked['groups']['buckets'] ?? []) as $bucket) {
        if (!is_array($bucket)) {
            continue;
        }
        $key = (string)($bucket['key_as_string'] ?? $bucket['key'] ?? '');
        $label = (string)($bucket['label']['buckets'][0]['key'] ?? '');
        if ($params['group_by'] === 'prefecture') {
            $label = miyabe_hyoka_projects_pref_name($key);
        }
        $groups[] = ['key' => $key, 'label' => $label !== '' ? $label : $key,
            'yen' => (int)round((float)($bucket['sum']['value'] ?? 0)), 'projects' => (int)($bucket['doc_count'] ?? 0)];
    }
    $top = [];
    foreach (($picked['top']['hits']['hits'] ?? []) as $hit) {
        $fact = is_array($hit['_source'] ?? null) ? $hit['_source'] : [];
        $top[] = [
            'id' => (string)($fact['project_id'] ?? ($hit['_id'] ?? '')),
            'project_name' => (string)($fact['project_name'] ?? ''),
            'municipality_name' => (string)($fact['municipality_name'] ?? ''),
            'department' => (string)($fact['department'] ?? ''),
            'year' => (int)($fact['year'] ?? 0),
            'stage' => (string)($fact['stage'] ?? ''),
            'label' => (string)($fact['label'] ?? ''),
            'raw' => (string)($fact['raw'] ?? ''),
            'unit' => (string)($fact['unit'] ?? ''),
            'yen' => (int)($fact['yen'] ?? 0),
        ];
    }
    $sumGroups = array_sum(array_column($groups, 'yen'));
    $totalYen = (int)round((float)($picked['sum']['value'] ?? 0));
    return [
        'status' => 'ok',
        'error' => '',
        'query' => $params['q'],
        'index_alias' => $alias,
        'year' => $params['year'],
        'from_year' => $params['from_year'],
        'to_year' => $params['to_year'],
        'scope' => $params['scope'],
        'stage' => $params['stage'],
        'group_by' => $params['group_by'],
        'matched_records' => (int)($response['hits']['total']['value'] ?? 0),
        'total' => [
            'yen' => $totalYen,
            'projects' => (int)($picked['doc_count'] ?? 0),
            'municipalities' => (int)($picked['municipalities']['value'] ?? 0),
        ],
        'groups' => $groups,
        // 上位 limit 件に入らなかったグループの合計。
        'other_groups_yen' => $params['group_by'] !== 'none' ? max(0, $totalYen - $sumGroups) : 0,
        'top_projects' => $top,
        'took_ms' => (int)($response['took'] ?? 0),
        'notes' => [
            '事務事業評価に載った事業の金額だけの合計で、自治体の予算・決算の総額ではありません。',
            '同じ事業・年度・費目の金額は 1 つだけ数えます（stage=best は決算・見込・予算・計画の順、同じ段階なら新しい文書の値）。',
            '金額は原典の単位（千円など）を円にした値です。単位が読めない金額と、事業ごとの欄に分けていない文書の事業は入りません。',
        ],
    ];
}

function miyabe_hyoka_projects_pref_name(string $code): string
{
    static $names = null;
    if ($names === null) {
        $names = [];
        $path = dirname(__DIR__) . DIRECTORY_SEPARATOR . 'data' . DIRECTORY_SEPARATOR . 'municipalities' . DIRECTORY_SEPARATOR . 'municipality_master.tsv';
        $lines = is_file($path) ? (file($path, FILE_IGNORE_NEW_LINES) ?: []) : [];
        $header = $lines !== [] ? explode("\t", (string)array_shift($lines)) : [];
        foreach ($lines as $line) {
            $row = array_combine($header, array_pad(explode("\t", $line), count($header), '')) ?: [];
            if (($row['entity_type'] ?? '') === 'prefecture') {
                $names[(string)$row['pref_code']] = (string)$row['pref_name'];
            }
        }
    }
    return $names[$code] ?? '';
}

const MIYABE_HYOKA_PROJECT_SHEET_PAGES = 5;

/**
 * 事業 1 件の全部。索引の値（円にそろえた金額の明細、指標の比）、控えの記録（原典の欄と表のまま）、
 * 求めがあればその事業の頁の表を文字にしたもの（include_sheet、max_sheet_chars）。
 */
function miyabe_hyoka_projects_execute_get(array $input): array
{
    $id = is_scalar($input['id'] ?? null) ? trim((string)$input['id']) : '';
    $parsed = miyabe_hyoka_projects_parse_id($id);
    if ($parsed === null) {
        return ['status' => 'query_error', 'error' => 'id には search_hyoka_projects の結果の id（hyoka-project:…）を指定してください。'];
    }
    $includeSheet = miyabe_hyoka_projects_bool($input['include_sheet'] ?? null) ?? true;
    $maxChars = miyabe_hyoka_projects_int($input['max_sheet_chars'] ?? null, 0, 100000) ?? 20000;
    if ($maxChars === PHP_INT_MIN) {
        return ['status' => 'query_error', 'error' => 'max_sheet_chars は 0〜100000 にしてください。'];
    }
    $alias = miyabe_hyoka_projects_alias();
    $response = miyabe_search_http_request('POST', '/' . rawurlencode($alias) . '/_search', [
        'size' => 1,
        'track_total_hits' => false,
        'query' => ['ids' => ['values' => [$id]]],
    ]);
    $hit = $response['hits']['hits'][0] ?? null;
    if (!is_array($hit)) {
        return ['status' => 'not_found', 'error' => '事業が見つかりませんでした。'];
    }
    $source = is_array($hit['_source'] ?? null) ? $hit['_source'] : [];
    $project = miyabe_hyoka_projects_hit_to_item($hit);
    $project['amounts'] = array_values(array_map(static fn(array $fact): array => [
        'year' => (int)($fact['year'] ?? 0), 'stage' => (string)($fact['stage'] ?? ''), 'scope' => (string)($fact['scope'] ?? ''),
        'label' => (string)($fact['label'] ?? ''), 'raw' => (string)($fact['raw'] ?? ''), 'unit' => (string)($fact['unit'] ?? ''),
        'yen' => (int)($fact['yen'] ?? 0), 'best' => (bool)($fact['best'] ?? false),
    ], array_filter(is_array($source['amounts'] ?? null) ? $source['amounts'] : [], 'is_array')));
    unset($project['score']);

    $documentKey = 'hyoka:' . $parsed['slug'] . ':' . $parsed['doc_id'];
    $found = miyabe_hyoka_display_find_record($documentKey, $parsed['record_id']);
    $record = $found['record'] ?? null;
    $sheet = [];
    $truncated = false;
    if ($includeSheet && is_array($record)) {
        $pages = array_values(array_filter(array_map('intval', is_array($record['pages'] ?? null) ? $record['pages'] : [(int)($record['page'] ?? 0)]),
            static fn(int $page): bool => $page > 0));
        $pages = array_slice($pages, 0, MIYABE_HYOKA_PROJECT_SHEET_PAGES);
        $left = $maxChars;
        foreach ($pages as $index => $page) {
            if ($left <= 0) {
                $truncated = true;
                break;
            }
            $loaded = miyabe_hyoka_display_sheet_page($documentKey, $page);
            if ($loaded === null) {
                continue;
            }
            $text = miyabe_hyoka_display_sheet_page_text($loaded['page']);
            if (mb_strlen($text, 'UTF-8') > $left) {
                $text = mb_substr($text, 0, $left, 'UTF-8');
                $truncated = true;
            }
            $sheet[] = ['page' => $page, 'text' => $text];
            $left -= mb_strlen($text, 'UTF-8');
        }
    }
    return [
        'status' => 'ok',
        'error' => '',
        'project' => $project,
        // 控えの記録。欄名・値は原典の升目の原文のまま（金額の表は千円などの原典の単位）。
        'record' => is_array($record) ? array_intersect_key($record, array_flip([
            'fields', 'raw_fields', 'amounts', 'funding', 'staff', 'indicators', 'evaluations', 'checks', 'page', 'pages', 'sheet', 'cells',
        ])) : null,
        'sheet' => $sheet,
        'sheet_truncated' => $truncated,
    ];
}

/**
 * 事業の ID「hyoka-project:<slug>:<doc_id>:<record_id>」を分ける。形が崩れていれば null。
 * 記録の ID は機械の抽出だと「hyoka:<24 桁>」で、`:` を含む。
 */
function miyabe_hyoka_projects_parse_id(string $id): ?array
{
    if (!preg_match('/^hyoka-project:([0-9]{5}-[a-z0-9-]+):([0-9a-f]{16}):([A-Za-z0-9._:-]{1,120})$/', $id, $match)) {
        return null;
    }
    return ['slug' => $match[1], 'doc_id' => $match[2], 'record_id' => $match[3]];
}
