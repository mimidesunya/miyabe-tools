<?php
declare(strict_types=1);

// Stable public entry point; implementation lives in the election domain.
require dirname(__DIR__, 2)
    . DIRECTORY_SEPARATOR . 'domains'
    . DIRECTORY_SEPARATOR . 'election_poster_boards'
    . DIRECTORY_SEPARATOR . 'http'
    . DIRECTORY_SEPARATOR . 'boards'
    . DIRECTORY_SEPARATOR . 'index.php';
