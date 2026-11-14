<?php
declare(strict_types=1);

// 自治体のお知らせの一覧。自治体（slug・municipality_code・名前）か都道府県と、期間・言葉で絞る。新しい順。

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'news_api.php';

news_api_handle('news_api_list', 'news');
