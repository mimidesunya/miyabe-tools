<?php
declare(strict_types=1);

// 自治体のお知らせの全文検索（題名・概要・ページの本文・添付の PDF の文字）。自治体・都道府県・期間で絞る。

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'news_search.php';

news_search_handle();
