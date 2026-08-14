<?php
declare(strict_types=1);

function expect_value(bool $condition, string $message): void
{
    if (!$condition) {
        throw new RuntimeException($message);
    }
}

$root = dirname(__DIR__, 3);
require_once $root . '/lib/municipalities.php';
require_once dirname(__DIR__) . '/php/municipality_feature.php';

$feature = poster_boards_normalize_municipality_feature(
    '99999-test-shi',
    '99999-test-shi',
    'テスト市',
    []
);
expect_value($feature['url'] === '/boards/99999-test-shi/', 'public board URL changed');
expect_value($feature['list_url'] === '/boards/list.php?slug=99999-test-shi', 'list URL changed');
expect_value(
    str_replace('\\', '/', (string)$feature['db_path_rel']) === 'boards/99999-test-shi/boards.sqlite',
    'board DB path changed'
);
expect_value(str_contains($feature['title'], '選挙ポスター掲示場'), 'domain title is ambiguous');

require_once $root . '/lib/session.php';
expect_value(function_exists('poster_boards_current_user'), 'domain auth API is missing');
expect_value(function_exists('current_user'), 'legacy session adapter is missing');
expect_value(
    current_user() === poster_boards_current_user(),
    'legacy session adapter diverged from domain runtime'
);

echo "election poster boards domain boundary: OK\n";
