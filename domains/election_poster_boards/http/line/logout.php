<?php
require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'php' . DIRECTORY_SEPARATOR . 'runtime.php';
session_destroy();
header('Location: /');
exit;
