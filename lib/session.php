<?php
declare(strict_types=1);

// Backward-compatible adapter. Election poster-board code should require the
// domain runtime directly and use the poster_boards_* names.
require_once dirname(__DIR__)
    . DIRECTORY_SEPARATOR . 'domains'
    . DIRECTORY_SEPARATOR . 'election_poster_boards'
    . DIRECTORY_SEPARATOR . 'php'
    . DIRECTORY_SEPARATOR . 'runtime.php';

function current_user(): ?array
{
    return poster_boards_current_user();
}

function require_login(): void
{
    poster_boards_require_login();
}

function is_admin(?array $user = null): bool
{
    return poster_boards_is_admin($user);
}

function open_pdo(string $path): PDO
{
    return poster_boards_open_pdo($path);
}

function open_users_pdo(): PDO
{
    return poster_boards_open_users_pdo();
}

function open_boards_pdo(string $slug): PDO
{
    return poster_boards_open_boards_pdo($slug);
}

function open_tasks_pdo(string $slug): PDO
{
    return poster_boards_open_tasks_pdo($slug);
}

function open_boards_with_tasks_pdo(string $slug): PDO
{
    return poster_boards_open_boards_with_tasks_pdo($slug);
}

function upsert_user(PDO $pdo, array $sessionUser, bool $isAttached = true): int
{
    return poster_boards_upsert_user($pdo, $sessionUser, $isAttached);
}
