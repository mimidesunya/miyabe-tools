<?php
declare(strict_types=1);

function health_env(string $key, string $default = ''): string
{
    $value = getenv($key);
    if ($value === false || trim((string)$value) === '') {
        return $default;
    }
    return trim((string)$value);
}

function health_ok(array $extra = []): array
{
    return array_merge(['status' => 'ok'], $extra);
}

function health_ng(string $message, array $extra = []): array
{
    return array_merge(['status' => 'ng', 'message' => $message], $extra);
}

function health_parse_http_status(array $headers): int
{
    $status = 0;
    foreach ($headers as $header) {
        if (preg_match('/^HTTP\/\S+\s+(\d{3})\b/', (string)$header, $matches) === 1) {
            $status = (int)$matches[1];
        }
    }
    return $status;
}

function health_check_php(): array
{
    return health_ok([
        'version' => PHP_VERSION,
        'sapi' => PHP_SAPI,
    ]);
}

function health_check_data(): array
{
    $dataRoot = dirname(__DIR__) . DIRECTORY_SEPARATOR . 'data';
    $masterPath = $dataRoot . DIRECTORY_SEPARATOR . 'municipalities' . DIRECTORY_SEPARATOR . 'municipality_master.tsv';
    if (!is_dir($dataRoot)) {
        return health_ng('data directory is missing');
    }
    if (!is_readable($dataRoot)) {
        return health_ng('data directory is not readable');
    }
    if (!is_file($masterPath) || !is_readable($masterPath)) {
        return health_ng('municipality master is not readable');
    }
    return health_ok([
        'municipality_master_mtime' => date(DATE_ATOM, (int)filemtime($masterPath)),
    ]);
}

function health_management_db_config(): ?array
{
    $url = health_env('MANAGEMENT_DATABASE_URL', health_env('DATABASE_URL', 'pgsql://miyabe:miyabe@postgres:5432/miyabe_management'));
    $parts = parse_url($url);
    if (!is_array($parts)) {
        return null;
    }
    $scheme = strtolower((string)($parts['scheme'] ?? ''));
    if (!in_array($scheme, ['pgsql', 'postgres', 'postgresql'], true)) {
        return null;
    }
    $path = trim((string)($parts['path'] ?? ''), '/');
    return [
        'dsn' => sprintf(
            'pgsql:host=%s;port=%d;dbname=%s',
            (string)($parts['host'] ?? 'postgres'),
            (int)($parts['port'] ?? 5432),
            $path !== '' ? rawurldecode($path) : 'miyabe_management'
        ),
        'user' => rawurldecode((string)($parts['user'] ?? 'miyabe')),
        'password' => rawurldecode((string)($parts['pass'] ?? 'miyabe')),
    ];
}

function health_check_postgres(): array
{
    if (!class_exists(PDO::class) || !in_array('pgsql', PDO::getAvailableDrivers(), true)) {
        return health_ng('pdo_pgsql is unavailable');
    }

    $config = health_management_db_config();
    if (!is_array($config)) {
        return health_ng('management database URL is invalid');
    }

    $started = microtime(true);
    try {
        $pdo = new PDO((string)$config['dsn'], (string)$config['user'], (string)$config['password'], [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_TIMEOUT => 2,
            PDO::ATTR_PERSISTENT => false,
        ]);
        $result = $pdo->query('SELECT 1')->fetchColumn();
        if ((string)$result !== '1') {
            return health_ng('unexpected SELECT 1 result');
        }
        return health_ok([
            'latency_ms' => (int)round((microtime(true) - $started) * 1000),
        ]);
    } catch (Throwable $error) {
        return health_ng('management database is unavailable', [
            'detail' => $error->getMessage(),
            'latency_ms' => (int)round((microtime(true) - $started) * 1000),
        ]);
    }
}

function health_check_opensearch(): array
{
    $baseUrl = rtrim(health_env('OPENSEARCH_URL', 'http://opensearch:9200'), '/');
    if ($baseUrl === '') {
        return health_ng('OpenSearch URL is not configured');
    }

    $headers = ['Accept: application/json'];
    $user = health_env('OPENSEARCH_USER');
    $password = health_env('OPENSEARCH_PASSWORD');
    if ($user !== '' || $password !== '') {
        $headers[] = 'Authorization: Basic ' . base64_encode($user . ':' . $password);
    }

    $contextOptions = [
        'http' => [
            'method' => 'GET',
            'header' => implode("\r\n", $headers),
            'ignore_errors' => true,
            'timeout' => 3,
        ],
    ];
    $insecureDev = strtolower(health_env('OPENSEARCH_INSECURE_DEV', 'false'));
    if (str_starts_with(strtolower($baseUrl), 'https://') && in_array($insecureDev, ['1', 'true', 'yes', 'on'], true)) {
        $contextOptions['ssl'] = [
            'verify_peer' => false,
            'verify_peer_name' => false,
        ];
    }

    $started = microtime(true);
    $http_response_header = [];
    $response = @file_get_contents($baseUrl . '/_cluster/health?local=true&timeout=2s', false, stream_context_create($contextOptions));
    $httpStatus = health_parse_http_status($http_response_header);
    $latencyMs = (int)round((microtime(true) - $started) * 1000);
    if (!is_string($response)) {
        return health_ng('OpenSearch is unreachable', [
            'http_status' => $httpStatus,
            'latency_ms' => $latencyMs,
        ]);
    }

    $data = json_decode($response, true);
    if (!is_array($data)) {
        return health_ng('OpenSearch health response is not JSON', [
            'http_status' => $httpStatus,
            'latency_ms' => $latencyMs,
        ]);
    }
    if ($httpStatus < 200 || $httpStatus >= 300) {
        return health_ng('OpenSearch returned HTTP ' . $httpStatus, [
            'http_status' => $httpStatus,
            'latency_ms' => $latencyMs,
        ]);
    }

    $clusterStatus = strtolower((string)($data['status'] ?? ''));
    if (!in_array($clusterStatus, ['green', 'yellow'], true) || ($data['timed_out'] ?? false) === true) {
        return health_ng('OpenSearch cluster is not healthy', [
            'cluster_status' => $clusterStatus,
            'timed_out' => (bool)($data['timed_out'] ?? false),
            'http_status' => $httpStatus,
            'latency_ms' => $latencyMs,
        ]);
    }

    return health_ok([
        'cluster_status' => $clusterStatus,
        'number_of_nodes' => (int)($data['number_of_nodes'] ?? 0),
        'http_status' => $httpStatus,
        'latency_ms' => $latencyMs,
    ]);
}

$checks = [
    'php' => health_check_php(),
    'data' => health_check_data(),
    'postgres' => health_check_postgres(),
    'opensearch' => health_check_opensearch(),
];

$healthy = true;
foreach ($checks as $check) {
    if (($check['status'] ?? '') !== 'ok') {
        $healthy = false;
        break;
    }
}

$payload = [
    'status' => $healthy ? 'ok' : 'ng',
    'service' => 'miyabe-tools',
    'checked_at' => date(DATE_ATOM),
    'checks' => $checks,
];

header('Content-Type: application/json; charset=UTF-8');
header('Cache-Control: no-store, max-age=0');
header('X-Robots-Tag: noindex, nofollow, noarchive');

// The watchdog expects a JSON body even when an internal dependency is down.
http_response_code(200);
echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE) . "\n";
