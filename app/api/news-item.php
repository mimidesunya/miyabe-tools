<?php
declare(strict_types=1);

// 自治体のお知らせ 1 件。リンク先のページの本文と、添付の PDF の文字を返す。

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'news_api.php';

news_api_handle('news_api_item', 'news-item');
