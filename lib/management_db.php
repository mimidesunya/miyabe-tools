<?php
declare(strict_types=1);

// PostgreSQL-backed management metadata store.
// Source documents stay on disk and full-text search stays in OpenSearch; this
// DB keeps small, indexed operational state for lists and dashboards.

function management_db_url(): string
{
    $url = trim((string)(getenv('MANAGEMENT_DATABASE_URL') ?: getenv('DATABASE_URL') ?: ''));
    return $url !== '' ? $url : 'pgsql://miyabe:miyabe@postgres:5432/miyabe_management';
}

function management_db_parse_url(string $url): ?array
{
    $parts = parse_url($url);
    if (!is_array($parts)) {
        return null;
    }
    $scheme = strtolower((string)($parts['scheme'] ?? ''));
    if (!in_array($scheme, ['pgsql', 'postgres', 'postgresql'], true)) {
        return null;
    }
    $host = (string)($parts['host'] ?? 'postgres');
    $port = (int)($parts['port'] ?? 5432);
    $path = trim((string)($parts['path'] ?? ''), '/');
    $dbname = $path !== '' ? rawurldecode($path) : 'miyabe_management';
    $query = [];
    parse_str((string)($parts['query'] ?? ''), $query);
    return [
        'dsn' => sprintf('pgsql:host=%s;port=%d;dbname=%s', $host, $port, $dbname),
        'user' => rawurldecode((string)($parts['user'] ?? 'miyabe')),
        'password' => rawurldecode((string)($parts['pass'] ?? 'miyabe')),
        'options' => $query,
    ];
}

function management_db_pdo(): ?PDO
{
    static $pdo = false;
    if ($pdo instanceof PDO) {
        return $pdo;
    }
    if ($pdo === null) {
        return null;
    }
    if (!class_exists(PDO::class) || !in_array('pgsql', PDO::getAvailableDrivers(), true)) {
        $pdo = null;
        return null;
    }

    $config = management_db_parse_url(management_db_url());
    if (!is_array($config)) {
        $pdo = null;
        return null;
    }

    try {
        $pdo = new PDO((string)$config['dsn'], (string)$config['user'], (string)$config['password'], [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_TIMEOUT => 2,
            PDO::ATTR_PERSISTENT => false,
        ]);
        management_db_migrate($pdo);
        return $pdo;
    } catch (Throwable $error) {
        error_log('[management_db] unavailable: ' . $error->getMessage());
        $pdo = null;
        return null;
    }
}

function management_db_json_encode(mixed $value): string
{
    $encoded = json_encode($value, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE);
    return is_string($encoded) ? $encoded : 'null';
}

function management_db_json_decode(mixed $value): mixed
{
    if (!is_string($value) || $value === '') {
        return null;
    }
    $decoded = json_decode($value, true);
    return json_last_error() === JSON_ERROR_NONE ? $decoded : null;
}

function management_db_migrate(PDO $pdo): void
{
    static $done = false;
    if ($done) {
        return;
    }
    $pdo->exec(<<<'SQL'
CREATE TABLE IF NOT EXISTS homepage_payload_meta (
    id smallint PRIMARY KEY CHECK (id = 1),
    generated_at text NOT NULL DEFAULT '',
    municipality_count integer NOT NULL DEFAULT 0,
    prefectures jsonb NOT NULL DEFAULT '[]'::jsonb,
    feature_summaries jsonb NOT NULL DEFAULT '[]'::jsonb,
    task_state_summaries jsonb NOT NULL DEFAULT '[]'::jsonb,
    running_tasks jsonb NOT NULL DEFAULT '[]'::jsonb,
    payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS homepage_municipality_cards (
    slug text PRIMARY KEY,
    name text NOT NULL DEFAULT '',
    prefecture_code text NOT NULL DEFAULT '',
    prefecture_label text NOT NULL DEFAULT '',
    sort_key text NOT NULL DEFAULT '',
    card_json jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_homepage_municipality_cards_prefecture
    ON homepage_municipality_cards (prefecture_code, sort_key);

CREATE TABLE IF NOT EXISTS management_task_statuses (
    task_key text PRIMARY KEY,
    running boolean NOT NULL DEFAULT false,
    heartbeat_at text NOT NULL DEFAULT '',
    updated_at_text text NOT NULL DEFAULT '',
    status_json jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
SQL);
    $done = true;
}

function management_db_store_homepage_payload(array $payload): void
{
    $pdo = management_db_pdo();
    if (!$pdo instanceof PDO) {
        return;
    }

    $municipalities = is_array($payload['municipalities'] ?? null) ? $payload['municipalities'] : [];
    try {
        $pdo->beginTransaction();
        $meta = $pdo->prepare(<<<'SQL'
INSERT INTO homepage_payload_meta (
    id, generated_at, municipality_count, prefectures, feature_summaries,
    task_state_summaries, running_tasks, payload_json, updated_at
) VALUES (
    1, :generated_at, :municipality_count, CAST(:prefectures AS jsonb), CAST(:feature_summaries AS jsonb),
    CAST(:task_state_summaries AS jsonb), CAST(:running_tasks AS jsonb), CAST(:payload_json AS jsonb), now()
)
ON CONFLICT (id) DO UPDATE SET
    generated_at = EXCLUDED.generated_at,
    municipality_count = EXCLUDED.municipality_count,
    prefectures = EXCLUDED.prefectures,
    feature_summaries = EXCLUDED.feature_summaries,
    task_state_summaries = EXCLUDED.task_state_summaries,
    running_tasks = EXCLUDED.running_tasks,
    payload_json = EXCLUDED.payload_json,
    updated_at = now()
SQL);
        $payloadWithoutCards = $payload;
        unset($payloadWithoutCards['municipalities']);
        $meta->execute([
            ':generated_at' => (string)($payload['generated_at'] ?? ''),
            ':municipality_count' => (int)($payload['municipality_count'] ?? 0),
            ':prefectures' => management_db_json_encode($payload['prefectures'] ?? []),
            ':feature_summaries' => management_db_json_encode($payload['feature_summaries'] ?? []),
            ':task_state_summaries' => management_db_json_encode($payload['task_state_summaries'] ?? []),
            ':running_tasks' => management_db_json_encode($payload['running_tasks'] ?? []),
            ':payload_json' => management_db_json_encode($payloadWithoutCards),
        ]);

        $pdo->exec('DELETE FROM homepage_municipality_cards');
        $cardInsert = $pdo->prepare(<<<'SQL'
INSERT INTO homepage_municipality_cards (
    slug, name, prefecture_code, prefecture_label, sort_key, card_json, updated_at
) VALUES (
    :slug, :name, :prefecture_code, :prefecture_label, :sort_key, CAST(:card_json AS jsonb), now()
)
SQL);
        foreach ($municipalities as $card) {
            if (!is_array($card)) {
                continue;
            }
            $slug = trim((string)($card['slug'] ?? ''));
            if ($slug === '') {
                continue;
            }
            $cardInsert->execute([
                ':slug' => $slug,
                ':name' => (string)($card['name'] ?? ''),
                ':prefecture_code' => (string)($card['prefecture_code'] ?? ''),
                ':prefecture_label' => (string)($card['prefecture_label'] ?? ''),
                ':sort_key' => $slug,
                ':card_json' => management_db_json_encode($card),
            ]);
        }
        $pdo->commit();
    } catch (Throwable $error) {
        if ($pdo->inTransaction()) {
            $pdo->rollBack();
        }
        error_log('[management_db] homepage payload store failed: ' . $error->getMessage());
    }
}

function management_db_store_task_status(string $taskKey, array $status): void
{
    $taskKey = trim($taskKey);
    if ($taskKey === '') {
        return;
    }
    $pdo = management_db_pdo();
    if (!$pdo instanceof PDO) {
        return;
    }
    try {
        $stmt = $pdo->prepare(<<<'SQL'
INSERT INTO management_task_statuses (
    task_key, running, heartbeat_at, updated_at_text, status_json, updated_at
) VALUES (
    :task_key, :running, :heartbeat_at, :updated_at_text, CAST(:status_json AS jsonb), now()
)
ON CONFLICT (task_key) DO UPDATE SET
    running = EXCLUDED.running,
    heartbeat_at = EXCLUDED.heartbeat_at,
    updated_at_text = EXCLUDED.updated_at_text,
    status_json = EXCLUDED.status_json,
    updated_at = now()
SQL);
        $stmt->execute([
            ':task_key' => $taskKey,
            ':running' => (bool)($status['running'] ?? false) ? 'true' : 'false',
            ':heartbeat_at' => (string)($status['heartbeat_at'] ?? ''),
            ':updated_at_text' => (string)($status['updated_at'] ?? ''),
            ':status_json' => management_db_json_encode($status),
        ]);
    } catch (Throwable $error) {
        error_log('[management_db] task status store failed: ' . $error->getMessage());
    }
}

function management_db_homepage_payload(?string $prefecture): ?array
{
    $pdo = management_db_pdo();
    if (!$pdo instanceof PDO) {
        return null;
    }

    try {
        $metaRow = $pdo->query('SELECT * FROM homepage_payload_meta WHERE id = 1')->fetch();
        if (!is_array($metaRow)) {
            return null;
        }
        $prefectures = management_db_json_decode($metaRow['prefectures'] ?? '') ?: [];
        $selectedCode = function_exists('homepage_normalize_prefecture_filter')
            ? homepage_normalize_prefecture_filter($prefecture, is_array($prefectures) ? $prefectures : [])
            : '';
        $selectedName = '';
        foreach ((is_array($prefectures) ? $prefectures : []) as $option) {
            if (is_array($option) && (string)($option['code'] ?? '') === $selectedCode) {
                $selectedName = (string)($option['name'] ?? '');
                break;
            }
        }

        if ($selectedCode !== '') {
            $stmt = $pdo->prepare(
                'SELECT card_json FROM homepage_municipality_cards WHERE prefecture_code = :prefecture_code ORDER BY sort_key'
            );
            $stmt->execute([':prefecture_code' => $selectedCode]);
        } else {
            $stmt = $pdo->query('SELECT card_json FROM homepage_municipality_cards ORDER BY sort_key');
        }

        $cards = [];
        while ($row = $stmt->fetch()) {
            $card = management_db_json_decode($row['card_json'] ?? '');
            if (is_array($card)) {
                $cards[] = $card;
            }
        }
        if ($cards === []) {
            return null;
        }

        return [
            'generated_at' => (string)($metaRow['generated_at'] ?? ''),
            'municipality_count' => (int)($metaRow['municipality_count'] ?? count($cards)),
            'display_municipality_count' => count($cards),
            'prefectures' => is_array($prefectures) ? $prefectures : [],
            'selected_prefecture_code' => $selectedCode,
            'selected_prefecture_name' => $selectedName,
            'feature_summaries' => management_db_json_decode($metaRow['feature_summaries'] ?? '') ?: [],
            'task_state_summaries' => management_db_json_decode($metaRow['task_state_summaries'] ?? '') ?: [],
            'running_tasks' => management_db_json_decode($metaRow['running_tasks'] ?? '') ?: [],
            'municipalities' => $cards,
        ];
    } catch (Throwable $error) {
        error_log('[management_db] homepage payload fetch failed: ' . $error->getMessage());
        return null;
    }
}
