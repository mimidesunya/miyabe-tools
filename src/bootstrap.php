<?php
declare(strict_types=1);

$projectRoot = dirname(__DIR__);
$includePaths = [
    $projectRoot . DIRECTORY_SEPARATOR . 'src',
    $projectRoot . DIRECTORY_SEPARATOR . 'lib',
];

$currentIncludePath = explode(PATH_SEPARATOR, (string)get_include_path());
foreach ($includePaths as $path) {
    if (!in_array($path, $currentIncludePath, true)) {
        $currentIncludePath[] = $path;
    }
}
set_include_path(implode(PATH_SEPARATOR, $currentIncludePath));
