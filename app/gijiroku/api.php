<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'gijiroku_api.php';

// 横断検索 UI から呼ばれる JSON API。

function request_slug_list(): array
{
    $raw = $_GET['slugs'] ?? '';
    if (!is_scalar($raw)) {
        return [];
    }

    $values = preg_split('/\s*,\s*/', trim((string)$raw), -1, PREG_SPLIT_NO_EMPTY);
    if ($values === false) {
        return [];
    }

    $slugs = [];
    foreach ($values as $value) {
        $slug = get_slug(trim((string)$value));
        if ($slug === '' || preg_match('/^[a-z0-9_-]+$/', $slug) !== 1) {
            continue;
        }
        $slugs[$slug] = $slug;
    }

    return array_values($slugs);
}

$readyMunicipalities = gijiroku_search_ready_municipalities();
$action = gijiroku_api_request_string('action');

if ($action === 'catalog') {
    $items = [];
    foreach ($readyMunicipalities as $municipality) {
        $items[] = gijiroku_search_public_summary($municipality);
    }
    gijiroku_api_respond_json([
        'items' => $items,
        'total' => count($items),
    ]);
}

if ($action === 'search') {
    $slug = gijiroku_api_requested_slug();
    if ($slug === '' || !isset($readyMunicipalities[$slug])) {
        gijiroku_api_respond_json(['error' => '検索対象の自治体が見つかりません。'], 404);
    }

    $query = gijiroku_api_request_string('q');
    $result = gijiroku_search_execute(
        $readyMunicipalities[$slug],
        $query,
        gijiroku_api_request_int('page', 1, 1, 9999),
        gijiroku_api_request_int('per_page', 12, 1, 20)
    );
    // 自治体ごとの検索結果は、ネットワークエラーではなく payload 内の status で扱う。
    gijiroku_api_respond_json($result);
}

if ($action === 'search_batch') {
    $slugs = request_slug_list();
    if ($slugs === []) {
        $slugs = array_keys($readyMunicipalities);
    }

    $selectedMunicipalities = [];
    foreach ($slugs as $slug) {
        if (isset($readyMunicipalities[$slug])) {
            $selectedMunicipalities[$slug] = $readyMunicipalities[$slug];
        }
    }

    if ($selectedMunicipalities === []) {
        gijiroku_api_respond_json(['error' => '検索対象の自治体がありません。'], 400);
    }

    $query = gijiroku_api_request_string('q');
    $validationError = gijiroku_search_validate_query($query, $selectedMunicipalities);
    if ($validationError !== null) {
        gijiroku_api_respond_json(['error' => $validationError], 422);
    }

    $items = [];
    $perPage = gijiroku_api_request_int('per_page', 3, 1, 6);
    foreach ($selectedMunicipalities as $municipality) {
        $items[] = gijiroku_search_execute_preview($municipality, $query, $perPage);
    }

    gijiroku_api_respond_json([
        'items' => $items,
        'total' => count($items),
    ]);
}

if ($action === 'search_preview') {
    $slug = gijiroku_api_requested_slug();
    if ($slug === '' || !isset($readyMunicipalities[$slug])) {
        gijiroku_api_respond_json(['error' => '検索対象の自治体が見つかりません。'], 404);
    }

    $query = gijiroku_api_request_string('q');
    $result = gijiroku_search_execute_preview(
        $readyMunicipalities[$slug],
        $query,
        gijiroku_api_request_int('per_page', 3, 1, 100)
    );
    gijiroku_api_respond_json($result);
}

gijiroku_api_respond_json(['error' => 'unknown action'], 400);
