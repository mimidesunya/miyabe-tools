<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'reiki_search.php';

// 横断検索 UI から呼ばれる JSON API。
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');

function respond_json(array $payload, int $status = 200): never
{
    http_response_code($status);
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function request_string(string $key, string $default = ''): string
{
    $value = $_GET[$key] ?? $default;
    return is_scalar($value) ? trim((string)$value) : $default;
}

function request_int(string $key, int $default, int $min, int $max): int
{
    $value = $_GET[$key] ?? $default;
    if (!is_scalar($value) || filter_var($value, FILTER_VALIDATE_INT) === false) {
        return $default;
    }

    return max($min, min($max, (int)$value));
}

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

$readyMunicipalities = reiki_search_ready_municipalities();
$action = request_string('action');

if ($action === 'catalog') {
    $items = [];
    foreach ($readyMunicipalities as $municipality) {
        $items[] = reiki_search_public_summary($municipality);
    }
    respond_json([
        'items' => $items,
        'total' => count($items),
    ]);
}

if ($action === 'search') {
    $slug = get_slug(request_string('slug'));
    if ($slug === '' || !isset($readyMunicipalities[$slug])) {
        respond_json(['error' => '検索対象の自治体が見つかりません。'], 404);
    }

    $query = request_string('q');
    $result = reiki_search_execute(
        $readyMunicipalities[$slug],
        $query,
        request_int('page', 1, 1, 9999),
        request_int('per_page', 12, 1, 20)
    );
    // 自治体ごとの結果はネットワークエラーではなく、payload 内の status で扱う。
    respond_json($result);
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
        respond_json(['error' => '検索対象の自治体がありません。'], 400);
    }

    $query = request_string('q');
    $validationError = reiki_search_validate_query($query, $selectedMunicipalities);
    if ($validationError !== null) {
        respond_json(['error' => $validationError], 422);
    }

    $items = [];
    $perPage = request_int('per_page', 3, 1, 6);
    foreach ($selectedMunicipalities as $municipality) {
        $items[] = reiki_search_execute_preview($municipality, $query, $perPage);
    }

    respond_json([
        'items' => $items,
        'total' => count($items),
    ]);
}

if ($action === 'search_preview') {
    $slug = get_slug(request_string('slug'));
    if ($slug === '' || !isset($readyMunicipalities[$slug])) {
        respond_json(['error' => '検索対象の自治体が見つかりません。'], 404);
    }

    $query = request_string('q');
    $result = reiki_search_execute_preview(
        $readyMunicipalities[$slug],
        $query,
        request_int('per_page', 3, 1, 6)
    );
    respond_json($result);
}

respond_json(['error' => 'unknown action'], 400);
