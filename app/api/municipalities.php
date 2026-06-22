<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'municipalities.php';

function municipalities_api_respond_json(array $payload, int $status = 200): never
{
    http_response_code($status);
    header('Content-Type: application/json; charset=UTF-8');
    header('Cache-Control: public, max-age=300');
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE);
    exit;
}

try {
    $prefectures = [];
    foreach (municipality_prefecture_names() as $code => $name) {
        $prefectures[] = ['code' => (string)$code, 'name' => (string)$name];
    }

    $municipalities = [];
    foreach (municipality_catalog() as $slug => $entry) {
        if (!is_array($entry)) {
            continue;
        }
        $publicSlug = municipality_public_slug((string)($entry['public_slug'] ?? $slug));
        $name = trim((string)($entry['name'] ?? ''));
        if ($publicSlug === '' || $name === '') {
            continue;
        }
        $prefCode = trim((string)($entry['pref_code'] ?? ''));
        if ($prefCode === '') {
            $prefCode = municipality_prefecture_code_from_code((string)($entry['code'] ?? ''));
        }
        $prefName = trim((string)($entry['pref_name'] ?? ''));
        if ($prefName === '') {
            $prefName = municipality_prefecture_name_from_code($prefCode);
        }
        $municipalities[] = [
            'slug' => $publicSlug,
            'code' => trim((string)($entry['code'] ?? '')),
            'name' => $name,
            'nameKana' => trim((string)($entry['name_kana'] ?? '')),
            'fullName' => trim((string)($entry['full_name'] ?? '')),
            'prefCode' => $prefCode,
            'prefName' => $prefName,
            'label' => $prefName !== '' ? "{$name}（{$prefName}）" : $name,
            'sort' => sprintf('%02d-%s', (int)$prefCode, $name),
        ];
    }
    usort($municipalities, static fn(array $a, array $b): int => strcmp((string)$a['sort'], (string)$b['sort']));

    municipalities_api_respond_json([
        'status' => 'ok',
        'prefectures' => $prefectures,
        'municipalities' => array_map(static fn(array $municipality): array => [
            'slug' => (string)$municipality['slug'],
            'code' => (string)$municipality['code'],
            'name' => (string)$municipality['name'],
            'nameKana' => (string)$municipality['nameKana'],
            'fullName' => (string)$municipality['fullName'],
            'prefCode' => (string)$municipality['prefCode'],
            'prefName' => (string)$municipality['prefName'],
            'label' => (string)$municipality['label'],
        ], $municipalities),
    ]);
} catch (Throwable $error) {
    error_log('[api/municipalities] ' . $error->getMessage());
    municipalities_api_respond_json([
        'status' => 'error',
        'error' => '自治体一覧の取得に失敗しました。',
        'municipalities' => [],
    ], 500);
}
