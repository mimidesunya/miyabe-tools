<?php
declare(strict_types=1);

$query = ['doc_type' => 'minutes'];

$slug = trim((string)($_GET['slug'] ?? ''));
if ($slug !== '') {
    $query['slug'] = $slug;
}

$q = trim((string)($_GET['q'] ?? ''));
if ($q !== '') {
    $query['q'] = $q;
}

foreach (['pref_code', 'start_year', 'end_year', 'sort'] as $key) {
    if (isset($_GET[$key]) && is_scalar($_GET[$key]) && trim((string)$_GET[$key]) !== '') {
        $query[$key] = trim((string)$_GET[$key]);
    }
}

header('Location: /search/?' . http_build_query($query), true, 302);
exit;
