<?php
declare(strict_types=1);

// Copper PDF の CTIP2 ドライバ（zamasoft/cti-php, Apache-2.0）をそのまま同梱している。
// composer を入れていないので、PSR-4 の代わりに最小の読み込みをここで行う。
// 取得元: https://github.com/zamasoftnet/cti.php

spl_autoload_register(static function (string $class): void {
    if (!str_starts_with($class, 'CTI\\')) {
        return;
    }
    $relative = str_replace('\\', DIRECTORY_SEPARATOR, substr($class, 4));
    $path = __DIR__ . DIRECTORY_SEPARATOR . 'CTI' . DIRECTORY_SEPARATOR . $relative . '.php';
    if (is_file($path)) {
        require_once $path;
    }
});

// composer.json の "files" 相当。関数定義なので先に読み込む。
require_once __DIR__ . DIRECTORY_SEPARATOR . 'Helpers.php';
require_once __DIR__ . DIRECTORY_SEPARATOR . 'CTIP2.php';
require_once __DIR__ . DIRECTORY_SEPARATOR . 'DriverManager.php';
