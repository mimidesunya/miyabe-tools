<?php
declare(strict_types=1);

require_once dirname(__DIR__, 3) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'gijiroku_api.php';

$query = gijiroku_api_request_string('q');
if ($query === '') {
    $query = gijiroku_api_request_string('name');
}

gijiroku_api_respond_json(gijiroku_api_catalog_payload($query));
