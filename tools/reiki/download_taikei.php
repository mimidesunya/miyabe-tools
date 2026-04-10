#!/usr/bin/env php
<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'japanese_search.php';

const TAIKEI_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36';
const TAIKEI_SLEEP_USEC = 120000;
const TAIKEI_FETCH_MAX_ATTEMPTS = 4;
const TAIKEI_FETCH_RETRY_BASE_USEC = 750000;
const TAIKEI_INDEX_COMMIT_BATCH_SIZE = 25;

main($argv);

function main(array $argv): void
{
    $options = getopt('', ['slug::', 'code::', 'name::', 'source-url::', 'limit::', 'force', 'crawl-only', 'check-updates']);
    $slug = isset($options['slug']) && is_string($options['slug']) && trim($options['slug']) !== ''
        ? trim($options['slug'])
        : default_slug_for_system('taikei');
    $limit = isset($options['limit']) ? max(0, (int)$options['limit']) : 0;
    $force = array_key_exists('force', $options);
    $crawlOnly = array_key_exists('crawl-only', $options);
    $checkUpdates = array_key_exists('check-updates', $options);

    $target = load_reiki_target_from_cli(
        $slug,
        'taikei',
        [
            'code' => is_string($options['code'] ?? null) ? trim((string)$options['code']) : '',
            'name' => is_string($options['name'] ?? null) ? trim((string)$options['name']) : '',
            'source_url' => is_string($options['source-url'] ?? null) ? trim((string)$options['source-url']) : '',
        ]
    );
    $dataRoot = (string)$target['data_root'];
    $workRoot = (string)$target['work_root'];
    $sourceDir = (string)$target['source_dir'];
    $htmlDir = (string)$target['html_dir'];
    $jsonDir = (string)$target['classification_dir'];
    $imageDir = (string)$target['image_dir'];
    $markdownDir = (string)$target['markdown_dir'];
    $dbPath = (string)$target['db_path'];
    $statePath = $workRoot . DIRECTORY_SEPARATOR . 'scrape_state.json';

    ensure_dir($dataRoot);
    ensure_dir($workRoot);
    ensure_dir($sourceDir);
    ensure_dir($htmlDir);
    ensure_dir($jsonDir);
    ensure_dir($imageDir);
    ensure_dir($markdownDir);
    $indexPdo = null;
    $indexUpdatesEnabled = true;
    try {
        $indexPdo = open_ordinance_index($dbPath);
    } catch (Throwable $e) {
        // 本文取得は継続し、逐次 index だけを停止して親バッチ側の再 build に委ねる。
        fwrite(
            STDERR,
            "Warning: failed to prepare ordinances.sqlite incremental update for {$slug} "
            . '[' . get_class($e) . "] db={$dbPath}; continuing without it: {$e->getMessage()}\n"
        );
        $indexUpdatesEnabled = false;
    }

    echo "Crawling {$target['name']} reiki taxonomy...\n";
    $crawl = crawl_taxonomy((string)$target['entry_url']);
    $records = array_values($crawl['records']);
    usort($records, static fn(array $a, array $b): int => strcmp((string)$a['code'], (string)$b['code']));

    $manifestPath = $workRoot . DIRECTORY_SEPARATOR . 'source_manifest.json.gz';
    $taxonomyPath = $workRoot . DIRECTORY_SEPARATOR . 'taxonomy_pages.json.gz';
    $previousManifestBySource = index_manifest_by_source(load_json_file($manifestPath, []));
    write_json_file($taxonomyPath, array_values($crawl['pages']), true);

    echo 'Found ' . count($records) . " ordinance pages across " . count($crawl['pages']) . " taxonomy pages.\n";
    if ($crawlOnly) {
        emit_progress(count($records), count($records), $statePath);
        write_json_file($manifestPath, $records, true);
        echo "Saved crawl manifest only: {$manifestPath}\n";
        return;
    }

    $downloaded = 0;
    $checked = 0;
    $skipped = 0;
    $parsed = 0;
    $reused = 0;
    $indexPending = 0;
    $manifests = [];
    $selectedRecords = $limit > 0 ? array_slice($records, 0, $limit) : $records;
    $total = count($selectedRecords);
    emit_progress(0, $total, $statePath);

    foreach ($selectedRecords as $index => $record) {
        $sourceFileName = ordinance_file_name_from_url((string)$record['detail_url']);
        $sourcePath = $sourceDir . DIRECTORY_SEPARATOR . $sourceFileName;
        $htmlPath = $htmlDir . DIRECTORY_SEPARATOR . preg_replace('/\.html$/i', '.html', $sourceFileName);
        $markdownPath = $markdownDir . DIRECTORY_SEPARATOR . preg_replace('/\.html$/i', '.md', $sourceFileName);
        $existingSourcePath = existing_path($sourcePath);
        $storedSourcePath = $existingSourcePath ?? gzip_path($sourcePath);
        $previousManifest = $previousManifestBySource[$sourceFileName] ?? null;

        $sourceHtml = '';
        $sourceHash = $existingSourcePath !== null ? sha256_file_auto($existingSourcePath) : '';
        $sourceChanged = false;

        if (!$force && $existingSourcePath !== null && filesize($existingSourcePath) > 0 && !$checkUpdates) {
            $skipped++;
        } else {
            $fetchedHtml = fetch_url((string)$record['detail_url']);
            $fetchedHash = sha256_string($fetchedHtml);

            if (!$force && $existingSourcePath !== null && $sourceHash === $fetchedHash) {
                $storedSourcePath = $existingSourcePath;
                $sourceHash = $fetchedHash;
                $checked++;
            } else {
                $storedSourcePath = write_text_file($sourcePath, $fetchedHtml, true);
                $sourceHtml = $fetchedHtml;
                $sourceHash = $fetchedHash;
                $sourceChanged = true;
                $downloaded++;
            }

            throttled_sleep();
        }

        $storedMarkdownPath = existing_path($markdownPath);
        $needsParse = $force
            || $sourceChanged
            || !is_file($htmlPath)
            || $storedMarkdownPath === null
            || !is_array($previousManifest);

        if ($needsParse) {
            if ($sourceHtml === '') {
                $sourceHtml = read_text_file_auto($storedSourcePath);
            }

            $parsedRecord = parse_taikei_ordinance_html($sourceHtml, (string)$record['detail_url'], $record);
            write_text_file($htmlPath, $parsedRecord['clean_html']);
            $storedMarkdownPath = write_text_file($markdownPath, $parsedRecord['markdown'], true);
            unset($parsedRecord['clean_html'], $parsedRecord['markdown']);
            $manifestEntry = $parsedRecord;
            $parsed++;
        } else {
            $manifestEntry = merge_manifest_record($previousManifest, $record, $sourceFileName);
            $reused++;
        }

        $manifestEntry['source_file'] = $sourceFileName;
        $manifestEntry['stored_source_file'] = basename($storedSourcePath);
        $manifestEntry['source_sha256'] = $sourceHash !== '' ? $sourceHash : sha256_file_auto($storedSourcePath);
        $manifestEntry['checked_updates'] = $checkUpdates;
        $manifestEntry['updated_at'] = gmdate('c');
        $manifests[] = $manifestEntry;
        if ($indexUpdatesEnabled && $indexPdo instanceof PDO) {
            // 再利用した既存 HTML もここで拾うことで、DB の取りこぼしを次の完了待ちなしで埋める。
            try {
                begin_ordinance_index_batch($indexPdo);
                upsert_ordinance_index_row($indexPdo, $manifestEntry, $htmlPath, $storedMarkdownPath);
                $indexPending++;
                if ($indexPending >= TAIKEI_INDEX_COMMIT_BATCH_SIZE) {
                    commit_ordinance_index_batch($indexPdo);
                    $indexPending = 0;
                }
            } catch (Throwable $e) {
                rollback_ordinance_index_batch($indexPdo);
                fwrite(
                    STDERR,
                    "Warning: failed to incrementally update ordinances.sqlite for {$slug} {$sourceFileName} "
                    . '[' . get_class($e) . "] db={$dbPath}; disabling further incremental updates: {$e->getMessage()}\n"
                );
                $indexUpdatesEnabled = false;
                $indexPdo = null;
                $indexPending = 0;
            }
        }
        emit_progress($index + 1, $total, $statePath);

        if ((($index + 1) % 25) === 0 || ($index + 1) === $total) {
            // 中断後の補完でも detail_url や taxonomy を拾えるよう、manifest を途中でも保存する。
            write_json_file($manifestPath, $manifests, true);
            echo sprintf(
                "[%d/%d] downloaded=%d checked=%d skipped=%d parsed=%d reused=%d\n",
                $index + 1,
                $total,
                $downloaded,
                $checked,
                $skipped,
                $parsed,
                $reused
            );
        }
    }

    if ($indexUpdatesEnabled && $indexPdo instanceof PDO && $indexPending > 0) {
        commit_ordinance_index_batch($indexPdo);
    }
    write_json_file($manifestPath, $manifests, true);

    echo "\nFinished {$target['name']} scrape.\n";
    echo "  Source HTML: {$sourceDir}\n";
    echo "  Clean HTML: {$htmlDir}\n";
    echo "  SQLite: {$dbPath}\n";
    echo "  Markdown: {$markdownDir}\n";
    echo "  Manifest: {$manifestPath}\n";
    echo "  Downloaded: {$downloaded}\n";
    echo "  Checked existing: {$checked}\n";
    echo "  Skipped existing: {$skipped}\n";
    echo "  Parsed: {$parsed}\n";
    echo "  Reused manifest: {$reused}\n";
}

function crawl_taxonomy(string $entryUrl): array
{
    $queue = [$entryUrl];
    $visited = [];
    $pages = [];
    $records = [];

    while ($queue !== []) {
        $url = array_shift($queue);
        if (!is_string($url) || isset($visited[$url])) {
            continue;
        }

        $visited[$url] = true;
        $html = fetch_url($url);
        $dom = create_dom($html);
        $xpath = new DOMXPath($dom);
        $currentPath = extract_taxonomy_path($xpath);

        $pages[$url] = [
            'url' => $url,
            'path' => $currentPath,
        ];

        foreach (extract_taxonomy_links($xpath, $url) as $taxonomyUrl) {
            if (!isset($visited[$taxonomyUrl])) {
                $queue[] = $taxonomyUrl;
            }
        }

        foreach (extract_ordinance_rows($xpath, $url, $currentPath) as $record) {
            $detailUrl = (string)$record['detail_url'];
            if (!isset($records[$detailUrl])) {
                $records[$detailUrl] = $record;
                continue;
            }

            $existingPaths = $records[$detailUrl]['taxonomy_paths'] ?? [];
            $paths = array_values(array_unique(array_filter(array_merge(
                is_array($existingPaths) ? $existingPaths : [],
                is_array($record['taxonomy_paths'] ?? null) ? $record['taxonomy_paths'] : []
            ))));
            $records[$detailUrl]['taxonomy_paths'] = $paths;
        }

        throttled_sleep();
    }

    return [
        'pages' => $pages,
        'records' => $records,
    ];
}

function extract_taxonomy_path(DOMXPath $xpath): string
{
    $node = $xpath->query('//table[contains(concat(" ", normalize-space(@class), " "), " scrollableA01 ")]//tbody/tr[1]/td[1]')->item(0);
    if (!$node instanceof DOMNode) {
        return '';
    }

    return normalize_whitespace($node->textContent ?? '');
}

function extract_taxonomy_links(DOMXPath $xpath, string $pageUrl): array
{
    $links = [];
    foreach ($xpath->query('//ul[@id="navigation"]//a[@href]') as $node) {
        if (!$node instanceof DOMElement) {
            continue;
        }

        $href = trim($node->getAttribute('href'));
        if ($href === '' || str_starts_with($href, 'javascript:')) {
            continue;
        }

        $absolute = resolve_url($pageUrl, $href);
        if (!str_contains($absolute, '/reiki_taikei/')) {
            continue;
        }

        if (!preg_match('/\.html?(?:[#?].*)?$/i', $absolute)) {
            continue;
        }

        $links[$absolute] = true;
    }

    return array_keys($links);
}

function extract_ordinance_rows(DOMXPath $xpath, string $pageUrl, string $currentPath): array
{
    $rows = [];
    foreach ($xpath->query('//table[contains(concat(" ", normalize-space(@class), " "), " scrollableA01 ")]//tbody/tr[position() > 1]') as $tr) {
        if (!$tr instanceof DOMElement) {
            continue;
        }

        $cells = [];
        foreach ($tr->getElementsByTagName('td') as $cell) {
            $cells[] = $cell;
        }
        if (count($cells) < 3) {
            continue;
        }

        $link = null;
        foreach ($cells[0]->getElementsByTagName('a') as $anchor) {
            if ($anchor instanceof DOMElement) {
                $link = $anchor;
                break;
            }
        }
        if (!$link instanceof DOMElement) {
            continue;
        }

        $href = trim($link->getAttribute('href'));
        if ($href === '') {
            continue;
        }

        $detailUrl = resolve_url($pageUrl, $href);
        if (!str_contains($detailUrl, '/reiki_honbun/')) {
            continue;
        }

        $title = normalize_whitespace($link->textContent ?? '');
        $date = normalize_whitespace($cells[1]->textContent ?? '');
        $date = ltrim($date, "◆ \t\n\r\0\x0B");
        $number = normalize_whitespace($cells[2]->textContent ?? '');

        $rows[] = [
            'code' => ordinance_code_from_url($detailUrl),
            'title' => $title,
            'date' => $date,
            'number' => $number,
            'detail_url' => $detailUrl,
            'source_file' => ordinance_file_name_from_url($detailUrl),
            'taxonomy_path' => $currentPath,
            'taxonomy_paths' => $currentPath !== '' ? [$currentPath] : [],
            'taxonomy_url' => $pageUrl,
        ];
    }

    return $rows;
}

function parse_taikei_ordinance_html(string $html, string $sourceUrl, array $record): array
{
    $dom = create_dom($html);
    $xpath = new DOMXPath($dom);

    $title = extract_xpath_text($xpath, '//p[contains(concat(" ", normalize-space(@class), " "), " title-irregular ")]');
    if ($title === '') {
        $title = extract_xpath_text($xpath, '//title');
    }
    $title = ltrim($title, '○');
    $title = normalize_whitespace($title);

    $date = normalize_whitespace(extract_xpath_text($xpath, '//p[contains(concat(" ", normalize-space(@class), " "), " date ")]'));
    $number = normalize_whitespace(extract_xpath_text($xpath, '//p[contains(concat(" ", normalize-space(@class), " "), " number ")]'));
    $scope = normalize_whitespace(extract_xpath_text($xpath, '//div[contains(concat(" ", normalize-space(@class), " "), " from-to ")]'));
    $enactmentDate = wareki_to_seireki($date);

    $primaryInner = $xpath->query('//div[@id="primaryInner2"]')->item(0);
    $cleanHtml = '';
    $markdown = '';
    $attachmentUrls = [];

    if ($primaryInner instanceof DOMElement) {
        $contentDom = new DOMDocument('1.0', 'UTF-8');
        $wrapper = $contentDom->createElement('div');
        $wrapper->setAttribute('class', 'law-content');
        $contentDom->appendChild($wrapper);

        foreach ($primaryInner->childNodes as $child) {
            $imported = $contentDom->importNode($child, true);
            $wrapper->appendChild($imported);
        }

        $contentXpath = new DOMXPath($contentDom);

        foreach ($contentXpath->query('//script|//style|//rt') as $node) {
            if ($node instanceof DOMNode && $node->parentNode) {
                $node->parentNode->removeChild($node);
            }
        }

        foreach ($contentXpath->query('//*[contains(concat(" ", normalize-space(@class), " "), " eline ")]') as $lineNode) {
            if (!$lineNode instanceof DOMElement) {
                continue;
            }
            if (contains_xpath($contentXpath, './/p[contains(concat(" ", normalize-space(@class), " "), " title-irregular ")]', $lineNode)
                || contains_xpath($contentXpath, './/p[contains(concat(" ", normalize-space(@class), " "), " date ")]', $lineNode)
                || contains_xpath($contentXpath, './/p[contains(concat(" ", normalize-space(@class), " "), " number ")]', $lineNode)
                || contains_xpath($contentXpath, './/div[contains(concat(" ", normalize-space(@class), " "), " from-to ")]', $lineNode)
            ) {
                if ($lineNode->parentNode) {
                    $lineNode->parentNode->removeChild($lineNode);
                }
            }
        }

        foreach ($contentXpath->query('//a[@href]') as $anchor) {
            if (!$anchor instanceof DOMElement) {
                continue;
            }
            $href = trim($anchor->getAttribute('href'));
            if ($href === '' || str_starts_with($href, '#') || str_starts_with(strtolower($href), 'javascript:')) {
                continue;
            }
            $anchor->setAttribute('href', resolve_url($sourceUrl, $href));
            $anchor->setAttribute('target', '_blank');
            $anchor->setAttribute('rel', 'noopener noreferrer');
        }

        foreach ($contentXpath->query('//*[@onclick]') as $node) {
            if (!$node instanceof DOMElement) {
                continue;
            }
            $onclick = $node->getAttribute('onclick');
            if (preg_match("/fileDownloadAction2\\('([^']+)'\\)/", $onclick, $m) === 1) {
                $assetUrl = resolve_url($sourceUrl, $m[1]);
                $attachmentUrls[$assetUrl] = true;
                if (strtolower($node->tagName) === 'a') {
                    $node->setAttribute('href', $assetUrl);
                    $node->setAttribute('target', '_blank');
                    $node->setAttribute('rel', 'noopener noreferrer');
                }
            }
            $node->removeAttribute('onclick');
        }

        foreach ($contentXpath->query('//*[@tabindex]') as $node) {
            if ($node instanceof DOMElement) {
                $node->removeAttribute('tabindex');
            }
        }

        $cleanParts = [];
        $cleanParts[] = '<div class="law-title">' . h($title) . '</div>';
        if ($date !== '') {
            $dateLabel = $enactmentDate !== '' ? h($date . ' (' . $enactmentDate . ')') : h($date);
            $cleanParts[] = '<div class="law-date">' . $dateLabel . '</div>';
        }
        if ($number !== '') {
            $cleanParts[] = '<div class="law-number">' . h($number) . '</div>';
        }
        if ($scope !== '') {
            $cleanParts[] = '<div class="law-scope">' . h($scope) . '</div>';
        }
        $cleanParts[] = $contentDom->saveHTML($wrapper) ?: '<div class="law-content"></div>';
        $cleanHtml = implode("\n", $cleanParts);

        $blocks = [];
        foreach ($contentXpath->query('//div[contains(concat(" ", normalize-space(@class), " "), " eline ")]') as $lineNode) {
            if (!$lineNode instanceof DOMElement) {
                continue;
            }

            $lineText = normalize_block_text($lineNode->textContent ?? '');
            if ($lineText === '') {
                continue;
            }

            $lineAnchor = $contentXpath->query('.//a[@href]', $lineNode)->item(0);
            if ($lineAnchor instanceof DOMElement) {
                $href = trim($lineAnchor->getAttribute('href'));
                if ($href !== '' && !str_starts_with($href, '#') && !str_starts_with(strtolower($href), 'javascript:')) {
                    $lineText = '[' . $lineText . '](' . $href . ')';
                }
            }

            if ($blocks === [] || end($blocks) !== $lineText) {
                $blocks[] = $lineText;
            }
        }

        $markdownLines = ['# ' . ($title !== '' ? $title : (string)($record['title'] ?? '無題')), ''];
        if ($date !== '') {
            $dateLine = '**日付:** ' . $date;
            if ($enactmentDate !== '') {
                $dateLine .= ' (' . $enactmentDate . ')';
            }
            $markdownLines[] = $dateLine;
        }
        if ($number !== '') {
            $markdownLines[] = '**種別番号:** ' . $number;
        }
        if ($scope !== '') {
            $markdownLines[] = '**対象:** ' . $scope;
        }
        if (count($markdownLines) > 2) {
            $markdownLines[] = '';
        }
        $markdownLines[] = '---';
        $markdownLines[] = '';
        foreach ($blocks as $block) {
            $markdownLines[] = $block;
            $markdownLines[] = '';
        }
        $markdown = rtrim(implode("\n", $markdownLines)) . "\n";
    }

    return [
        'code' => (string)($record['code'] ?? ordinance_code_from_url($sourceUrl)),
        'title' => $title !== '' ? $title : (string)($record['title'] ?? ''),
        'date' => $date !== '' ? $date : (string)($record['date'] ?? ''),
        'enactment_date' => $enactmentDate,
        'number' => $number !== '' ? $number : (string)($record['number'] ?? ''),
        'scope' => $scope,
        'detail_url' => $sourceUrl,
        'source_file' => (string)($record['source_file'] ?? ordinance_file_name_from_url($sourceUrl)),
        'taxonomy_path' => (string)($record['taxonomy_path'] ?? ''),
        'taxonomy_paths' => array_values(array_unique(array_filter(is_array($record['taxonomy_paths'] ?? null) ? $record['taxonomy_paths'] : []))),
        'taxonomy_url' => (string)($record['taxonomy_url'] ?? ''),
        'attachment_urls' => array_keys($attachmentUrls),
        'clean_html' => $cleanHtml,
        'markdown' => $markdown,
    ];
}

function fetch_url(string $url): string
{
    if (extension_loaded('curl')) {
        $verifySsl = true;
        $lastStatus = 0;
        $lastError = '';

        for ($attempt = 1; $attempt <= TAIKEI_FETCH_MAX_ATTEMPTS; $attempt++) {
            [$body, $status, $error] = curl_fetch($url, $verifySsl);
            if (($body === false || $status >= 400) && $verifySsl && should_retry_insecure($error)) {
                warn_retry_insecure();
                $verifySsl = false;
                [$body, $status, $error] = curl_fetch($url, false);
            }

            if ($body !== false && $status < 400) {
                return ensure_utf8((string)$body);
            }

            $lastStatus = $status;
            $lastError = $error;
            if ($attempt >= TAIKEI_FETCH_MAX_ATTEMPTS || !should_retry_fetch($status, $error)) {
                break;
            }

            wait_for_fetch_retry($url, $attempt, $status, $error);
        }

        throw new RuntimeException("Failed to fetch {$url}: " . format_fetch_failure($lastStatus, $lastError));
    }

    $verifySsl = true;
    $lastStatus = 0;
    $lastError = '';

    for ($attempt = 1; $attempt <= TAIKEI_FETCH_MAX_ATTEMPTS; $attempt++) {
        [$body, $status, $error] = stream_fetch($url, $verifySsl);
        if ($body === false && $verifySsl && should_retry_insecure($error)) {
            warn_retry_insecure();
            $verifySsl = false;
            [$body, $status, $error] = stream_fetch($url, false);
        }

        if ($body !== false && $status < 400) {
            return ensure_utf8($body);
        }

        $lastStatus = $status;
        $lastError = $error;
        if ($attempt >= TAIKEI_FETCH_MAX_ATTEMPTS || !should_retry_fetch($status, $error)) {
            break;
        }

        wait_for_fetch_retry($url, $attempt, $status, $error);
    }

    throw new RuntimeException("Failed to fetch {$url}: " . format_fetch_failure($lastStatus, $lastError));
}

function curl_fetch(string $url, bool $verifySsl): array
{
    $ch = curl_init($url);
    if ($ch === false) {
        throw new RuntimeException("Failed to initialize curl for {$url}");
    }
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_FOLLOWLOCATION => true,
        CURLOPT_MAXREDIRS => 10,
        CURLOPT_CONNECTTIMEOUT => 30,
        CURLOPT_TIMEOUT => 120,
        CURLOPT_USERAGENT => TAIKEI_USER_AGENT,
        CURLOPT_ENCODING => '',
        CURLOPT_HTTPHEADER => ['Accept-Language: ja,en-US;q=0.9,en;q=0.8'],
        CURLOPT_SSL_VERIFYPEER => $verifySsl,
        CURLOPT_SSL_VERIFYHOST => $verifySsl ? 2 : 0,
    ]);
    $body = curl_exec($ch);
    $status = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    $error = curl_error($ch);
    curl_close($ch);
    return [$body, $status, $error];
}

function stream_fetch(string $url, bool $verifySsl): array
{
    $context = stream_context_create([
        'http' => [
            'method' => 'GET',
            'header' => implode("\r\n", [
                'User-Agent: ' . TAIKEI_USER_AGENT,
                'Accept-Language: ja,en-US;q=0.9,en;q=0.8',
            ]),
            'timeout' => 120,
            'follow_location' => 1,
        ],
        'ssl' => [
            'verify_peer' => $verifySsl,
            'verify_peer_name' => $verifySsl,
        ],
    ]);

    $body = @file_get_contents($url, false, $context);
    $headers = isset($http_response_header) && is_array($http_response_header) ? $http_response_header : [];
    $status = http_status_from_headers($headers);
    $error = '';
    if ($body === false) {
        $lastError = error_get_last();
        $error = is_array($lastError) ? (string)($lastError['message'] ?? 'unknown error') : 'unknown error';
    }

    return [$body, $status, $error];
}

function should_retry_insecure(string $error): bool
{
    if ($error === '') {
        return false;
    }
    $error = strtolower($error);
    return str_contains($error, 'certificate')
        || str_contains($error, 'issuer')
        || str_contains($error, 'ssl');
}

function should_retry_fetch(int $status, string $error): bool
{
    if (in_array($status, [0, 408, 425, 429, 500, 502, 503, 504], true)) {
        return true;
    }

    if ($error === '') {
        return false;
    }

    $error = strtolower($error);
    foreach ([
        'timed out',
        'timeout',
        'connection reset',
        'recv failure',
        'could not connect',
        'failed to connect',
        'empty reply',
        'connection aborted',
        'connection refused',
        'temporarily unavailable',
        'temporary failure',
        'server returned nothing',
        'network is unreachable',
        'http/2 stream',
    ] as $needle) {
        if (str_contains($error, $needle)) {
            return true;
        }
    }

    return false;
}

function format_fetch_failure(int $status, string $error): string
{
    $detail = trim($error);
    if ($detail !== '') {
        return "HTTP {$status} {$detail}";
    }
    return "HTTP {$status}";
}

function wait_for_fetch_retry(string $url, int $attempt, int $status, string $error): void
{
    $delayUsec = fetch_retry_delay_usec($attempt);
    fwrite(
        STDERR,
        sprintf(
            "Warning: transient fetch failure for %s (%s); retrying in %.2fs [attempt %d/%d].\n",
            $url,
            format_fetch_failure($status, $error),
            $delayUsec / 1000000,
            $attempt + 1,
            TAIKEI_FETCH_MAX_ATTEMPTS
        )
    );
    usleep($delayUsec);
}

function fetch_retry_delay_usec(int $attempt): int
{
    return min(5_000_000, TAIKEI_FETCH_RETRY_BASE_USEC * max(1, $attempt));
}

function warn_retry_insecure(): void
{
    static $warned = false;
    if ($warned) {
        return;
    }
    fwrite(STDERR, "Warning: SSL certificate verification failed in this environment; retrying without local CA verification.\n");
    $warned = true;
}

function http_status_from_headers(array $headers): int
{
    foreach ($headers as $header) {
        if (!is_string($header)) {
            continue;
        }
        if (preg_match('/^HTTP\/\S+\s+(\d{3})/i', $header, $matches) === 1) {
            return (int)$matches[1];
        }
    }
    return 0;
}

function create_dom(string $html): DOMDocument
{
    $dom = new DOMDocument('1.0', 'UTF-8');
    libxml_use_internal_errors(true);
    $dom->loadHTML('<?xml encoding="utf-8" ?>' . $html, LIBXML_HTML_NOIMPLIED | LIBXML_HTML_NODEFDTD);
    libxml_clear_errors();
    return $dom;
}

function extract_xpath_text(DOMXPath $xpath, string $query): string
{
    $node = $xpath->query($query)->item(0);
    if (!$node instanceof DOMNode) {
        return '';
    }
    return normalize_whitespace($node->textContent ?? '');
}

function contains_xpath(DOMXPath $xpath, string $query, DOMNode $context): bool
{
    $result = $xpath->query($query, $context);
    return $result instanceof DOMNodeList && $result->length > 0;
}

function normalize_whitespace(string $text): string
{
    $text = str_replace(["\r\n", "\r"], "\n", $text);
    $text = str_replace("\xc2\xa0", ' ', $text);
    $text = preg_replace('/[ \t]+/u', ' ', $text) ?? $text;
    $text = preg_replace('/\n{2,}/u', "\n", $text) ?? $text;
    return trim($text);
}

function normalize_block_text(string $text): string
{
    $text = str_replace(["\r\n", "\r"], "\n", $text);
    $text = str_replace("\xc2\xa0", ' ', $text);
    $text = preg_replace('/[ \t]+/u', ' ', $text) ?? $text;
    $text = preg_replace('/\n+/u', "\n", $text) ?? $text;
    $text = preg_replace('/ *\n */u', "\n", $text) ?? $text;
    return trim($text);
}

function ensure_utf8(string $text): string
{
    if (mb_check_encoding($text, 'UTF-8')) {
        return $text;
    }
    return mb_convert_encoding($text, 'UTF-8', 'SJIS-win,CP932,EUC-JP,ISO-2022-JP,UTF-8');
}

function wareki_to_seireki(string $wareki): string
{
    $normalized = strtr($wareki, [
        '０' => '0', '１' => '1', '２' => '2', '３' => '3', '４' => '4',
        '５' => '5', '６' => '6', '７' => '7', '８' => '8', '９' => '9',
        '元' => '1',
    ]);
    if (preg_match('/(明治|大正|昭和|平成|令和)\s*(\d+)年\s*(\d+)月\s*(\d+)日/u', $normalized, $m) !== 1) {
        return '';
    }

    $baseYears = [
        '明治' => 1867,
        '大正' => 1911,
        '昭和' => 1925,
        '平成' => 1988,
        '令和' => 2018,
    ];
    $era = $m[1];
    $year = $baseYears[$era] + (int)$m[2];
    return sprintf('%04d-%02d-%02d', $year, (int)$m[3], (int)$m[4]);
}

function ordinance_code_from_url(string $url): string
{
    $path = parse_url($url, PHP_URL_PATH);
    if (!is_string($path)) {
        return '';
    }
    $stem = pathinfo($path, PATHINFO_FILENAME);
    return is_string($stem) ? $stem : '';
}

function ordinance_file_name_from_url(string $url): string
{
    $code = ordinance_code_from_url($url);
    if ($code === '') {
        return 'unknown_j.html';
    }
    return $code . '_j.html';
}

function resolve_url(string $baseUrl, string $relative): string
{
    if ($relative === '') {
        return $baseUrl;
    }
    if (preg_match('#^https?://#i', $relative) === 1) {
        return $relative;
    }
    if (str_starts_with($relative, '//')) {
        $scheme = (string)(parse_url($baseUrl, PHP_URL_SCHEME) ?: 'https');
        return $scheme . ':' . $relative;
    }

    $base = parse_url($baseUrl);
    if (!is_array($base)) {
        return $relative;
    }
    $scheme = (string)($base['scheme'] ?? 'https');
    $host = (string)($base['host'] ?? '');
    $port = isset($base['port']) ? ':' . $base['port'] : '';
    $basePath = (string)($base['path'] ?? '/');

    if (str_starts_with($relative, '/')) {
        return $scheme . '://' . $host . $port . normalize_url_path($relative);
    }

    $dir = preg_replace('#/[^/]*$#', '/', $basePath);
    if (!is_string($dir) || $dir === '') {
        $dir = '/';
    }
    return $scheme . '://' . $host . $port . normalize_url_path($dir . $relative);
}

function normalize_url_path(string $path): string
{
    $query = '';
    $fragment = '';

    $hashPos = strpos($path, '#');
    if ($hashPos !== false) {
        $fragment = substr($path, $hashPos);
        $path = substr($path, 0, $hashPos);
    }
    $queryPos = strpos($path, '?');
    if ($queryPos !== false) {
        $query = substr($path, $queryPos);
        $path = substr($path, 0, $queryPos);
    }

    $parts = [];
    foreach (explode('/', str_replace('\\', '/', $path)) as $part) {
        if ($part === '' || $part === '.') {
            continue;
        }
        if ($part === '..') {
            array_pop($parts);
            continue;
        }
        $parts[] = $part;
    }

    return '/' . implode('/', $parts) . $query . $fragment;
}

function project_root(): string
{
    return dirname(__DIR__, 2);
}

function normalize_relative_path(string $relative): string
{
    return trim(str_replace(['/', '\\'], DIRECTORY_SEPARATOR, $relative), DIRECTORY_SEPARATOR);
}

function build_data_path(string $relative): string
{
    $normalized = normalize_relative_path($relative);
    $dataRoot = project_root() . DIRECTORY_SEPARATOR . 'data';
    if ($normalized === '') {
        return $dataRoot;
    }
    return $dataRoot . DIRECTORY_SEPARATOR . $normalized;
}

function build_work_path(string $relative): string
{
    $normalized = normalize_relative_path($relative);
    $workRoot = project_root() . DIRECTORY_SEPARATOR . 'work';
    if ($normalized === '') {
        return $workRoot;
    }
    return $workRoot . DIRECTORY_SEPARATOR . $normalized;
}

function load_project_config(): array
{
    static $config = null;
    if (is_array($config)) {
        return $config;
    }

    $dataRoot = project_root() . DIRECTORY_SEPARATOR . 'data';
    $candidates = [
        $dataRoot . DIRECTORY_SEPARATOR . 'config.json',
        $dataRoot . DIRECTORY_SEPARATOR . 'config.example.json',
    ];
    foreach ($candidates as $candidate) {
        if (!is_file($candidate)) {
            continue;
        }

        $decoded = json_decode((string)file_get_contents($candidate), true);
        $config = is_array($decoded) ? $decoded : [];
        return $config;
    }

    $config = [];
    return $config;
}

function sanitize_slug_token(string $value): string
{
    $token = strtolower(trim($value));
    $token = preg_replace('/[^a-z0-9-]+/', '-', $token) ?? '';
    $token = trim($token, '-');
    return preg_replace('/-{2,}/', '-', $token) ?? '';
}

function load_municipality_master_index(): array
{
    static $index = null;
    if (is_array($index)) {
        return $index;
    }

    $path = project_root() . DIRECTORY_SEPARATOR . 'data' . DIRECTORY_SEPARATOR . 'municipalities' . DIRECTORY_SEPARATOR . 'municipality_master.tsv';
    if (!is_file($path)) {
        throw new RuntimeException("Missing municipality master: {$path}");
    }

    $handle = fopen($path, 'rb');
    if ($handle === false) {
        throw new RuntimeException("Failed to open {$path}");
    }

    $index = [];
    $header = fgetcsv($handle, 0, "\t");
    if (!is_array($header)) {
        fclose($handle);
        return $index;
    }

    $header = array_map(
        static fn($value): string => trim((string)$value, "\xEF\xBB\xBF \t\n\r\0\x0B"),
        $header
    );

    while (($row = fgetcsv($handle, 0, "\t")) !== false) {
        if (!is_array($row) || count($row) === 0) {
            continue;
        }

        $assoc = [];
        foreach ($header as $offset => $column) {
            if ($column === '') {
                continue;
            }
            $assoc[$column] = isset($row[$offset]) ? trim((string)$row[$offset]) : '';
        }

        $code = trim((string)($assoc['jis_code'] ?? ''));
        if ($code === '') {
            continue;
        }
        $index[$code] = [
            'entity_type' => trim((string)($assoc['entity_type'] ?? '')),
            'name' => trim((string)($assoc['name'] ?? '')),
            'name_kana' => trim((string)($assoc['name_kana'] ?? '')),
            'full_name' => trim((string)($assoc['full_name'] ?? '')),
            'name_romaji' => trim((string)($assoc['name_romaji'] ?? '')),
        ];
    }

    fclose($handle);
    return $index;
}

function implicit_municipality_slug(string $code, array $masterEntry = []): string
{
    $normalizedCode = preg_replace('/[^0-9]/', '', $code) ?? '';
    if ($normalizedCode === '') {
        $normalizedCode = '00000';
    }

    $token = sanitize_slug_token((string)($masterEntry['name_romaji'] ?? ''));
    if ($token === '') {
        $token = 'municipality';
    }
    return $normalizedCode . '-' . $token;
}

function load_local_reiki_url_index(): array
{
    static $index = null;
    if (is_array($index)) {
        return $index;
    }

    $path = project_root() . DIRECTORY_SEPARATOR . 'data' . DIRECTORY_SEPARATOR . 'municipalities' . DIRECTORY_SEPARATOR . 'reiki_system_urls.tsv';
    if (!is_file($path)) {
        throw new RuntimeException("Missing local reiki URL list: {$path}");
    }

    $handle = fopen($path, 'rb');
    if ($handle === false) {
        throw new RuntimeException("Failed to open {$path}");
    }

    $index = [];
    $header = fgetcsv($handle, 0, "\t");
    if (!is_array($header)) {
        fclose($handle);
        return $index;
    }

    $header = array_map(
        static fn($value): string => trim((string)$value, "\xEF\xBB\xBF \t\n\r\0\x0B"),
        $header
    );

    while (($row = fgetcsv($handle, 0, "\t")) !== false) {
        if (!is_array($row) || count($row) === 0) {
            continue;
        }

        $assoc = [];
        foreach ($header as $offset => $column) {
            if ($column === '') {
                continue;
            }
            $assoc[$column] = isset($row[$offset]) ? trim((string)$row[$offset]) : '';
        }

        $code = trim((string)($assoc['jis_code'] ?? ''));
        if ($code === '') {
            continue;
        }
        $index[$code] = $assoc;
    }

    fclose($handle);
    return $index;
}

function build_reiki_target_entry(string $slug, array $entry, array $urlEntry, array $masterEntry): array
{
    $code = trim((string)($entry['code'] ?? $masterEntry['code'] ?? ''));
    $systemType = trim((string)($urlEntry['system_type'] ?? ''));
    // 例規スクレイパの保存先は slug から一意に決め、自治体ごとの path override は持たない。
    $sourceDirRelative = "reiki/{$slug}/source";
    $htmlDirRelative = "reiki/{$slug}/html";
    $classificationDirRelative = "reiki/{$slug}/json";
    $imageDirRelative = "reiki/{$slug}/images";
    $markdownDirRelative = "reiki/{$slug}/markdown";
    $dbPathRelative = "reiki/{$slug}/ordinances.sqlite";

    $sourceDir = build_work_path($sourceDirRelative);
    $name = trim((string)($entry['name'] ?? $masterEntry['name'] ?? $slug)) ?: $slug;

    return [
        'slug' => $slug,
        'name' => $name,
        'name_kana' => trim((string)($entry['name_kana'] ?? $masterEntry['name_kana'] ?? '')),
        'full_name' => trim((string)($entry['full_name'] ?? $masterEntry['full_name'] ?? $name)) ?: $name,
        'name_romaji' => trim((string)($entry['name_romaji'] ?? $masterEntry['name_romaji'] ?? '')),
        'code' => $code,
        'system_type' => $systemType,
        'source_url' => trim((string)($urlEntry['url'] ?? '')),
        'entry_url' => derive_taikei_entry_url(trim((string)($urlEntry['url'] ?? ''))),
        'data_root' => build_data_path("reiki/{$slug}"),
        'work_root' => dirname($sourceDir),
        'source_dir' => $sourceDir,
        'html_dir' => build_data_path($htmlDirRelative),
        'classification_dir' => build_data_path($classificationDirRelative),
        'image_dir' => build_data_path($imageDirRelative),
        'markdown_dir' => build_work_path($markdownDirRelative),
        'db_path' => build_data_path($dbPathRelative),
    ];
}

function iter_reiki_targets(?string $expectedSystem = null, bool $configuredOnly = false): array
{
    // $configuredOnly は旧 CLI 互換の引数として受けるが、現在は全国マスタをそのまま使う。
    $targets = [];
    $urlIndex = load_local_reiki_url_index();
    $masterIndex = load_municipality_master_index();

    foreach ($urlIndex as $code => $urlEntry) {
        $systemType = trim((string)($urlEntry['system_type'] ?? ''));
        if ($expectedSystem !== null && $systemType !== $expectedSystem) {
            continue;
        }

        $sourceUrl = trim((string)($urlEntry['url'] ?? ''));
        if ($sourceUrl === '') {
            continue;
        }

        $slug = implicit_municipality_slug($code, $masterIndex[$code] ?? []);
        $entry = ['code' => $code];
        $targets[] = build_reiki_target_entry($slug, $entry, $urlEntry, $masterIndex[$code] ?? []);
    }

    return $targets;
}

function reiki_target_matches_slug(array $target, string $slug): bool
{
    $candidate = trim($slug);
    if ($candidate === '') {
        return false;
    }

    $targetSlug = trim((string)($target['slug'] ?? ''));
    $code = trim((string)($target['code'] ?? ''));
    $nameRomaji = sanitize_slug_token((string)($target['name_romaji'] ?? ''));
    $aliases = [$targetSlug];
    if ($code !== '') {
        $aliases[] = $code;
    }
    if ($nameRomaji !== '') {
        $aliases[] = $nameRomaji;
        if ($code !== '') {
            $aliases[] = $code . '-' . $nameRomaji;
        }
    }

    return in_array($candidate, $aliases, true);
}

function load_reiki_target(string $slug, string $expectedSystem): array
{
    foreach (iter_reiki_targets($expectedSystem, false) as $target) {
        if (reiki_target_matches_slug($target, $slug)) {
            return $target;
        }
    }
    throw new RuntimeException("Municipality slug not found: {$slug}");
}

function load_reiki_target_from_cli(string $slug, string $expectedSystem, array $overrides): array
{
    $code = trim((string)($overrides['code'] ?? ''));
    $nameOverride = trim((string)($overrides['name'] ?? ''));
    $sourceUrlOverride = trim((string)($overrides['source_url'] ?? ''));

    if ($code === '' && $nameOverride === '' && $sourceUrlOverride === '') {
        return load_reiki_target($slug, $expectedSystem);
    }

    if ($code === '' || $sourceUrlOverride === '') {
        throw new RuntimeException('--code と --source-url を一緒に指定してください。');
    }

    $entry = [];

    $urlIndex = load_local_reiki_url_index();
    $urlEntry = $urlIndex[$code] ?? null;
    if (!is_array($urlEntry)) {
        throw new RuntimeException("Municipality code {$code} is missing from data/municipalities/reiki_system_urls.tsv");
    }

    $systemType = trim((string)($urlEntry['system_type'] ?? ''));
    if ($systemType !== $expectedSystem) {
        throw new RuntimeException(
            "Municipality slug {$slug} uses system_type={$systemType}, expected {$expectedSystem}"
        );
    }

    $masterEntry = load_municipality_master_index()[$code] ?? [];
    if ($nameOverride !== '') {
        $entry['name'] = $nameOverride;
    }
    $entry['code'] = $code;
    $urlEntry['url'] = $sourceUrlOverride;
    return build_reiki_target_entry($slug, $entry, $urlEntry, $masterEntry);
}

function default_slug_for_system(string $expectedSystem): string
{
    $config = load_project_config();
    $preferredSlug = trim((string)($config['DEFAULT_SLUG'] ?? ''));
    if ($preferredSlug !== '') {
        try {
            return (string)load_reiki_target($preferredSlug, $expectedSystem)['slug'];
        } catch (Throwable) {
        }
    }

    $allTargets = iter_reiki_targets($expectedSystem, false);
    if ($allTargets !== []) {
        return (string)($allTargets[0]['slug'] ?? '');
    }

    throw new RuntimeException("No municipality found for system_type={$expectedSystem}");
}

function derive_taikei_entry_url(string $sourceUrl): string
{
    $sourceUrl = trim($sourceUrl);
    if ($sourceUrl === '') {
        throw new RuntimeException('Missing taikei source URL.');
    }

    $path = parse_url($sourceUrl, PHP_URL_PATH);
    if (!is_string($path) || $path === '') {
        return resolve_url($sourceUrl, 'reiki_taikei/taikei_default.html');
    }

    $lowerPath = strtolower($path);
    if (str_contains($lowerPath, '/reiki_taikei/')) {
        return $sourceUrl;
    }
    if (
        str_ends_with($lowerPath, '/')
        || preg_match('#/(reiki_menu|index)\.html?$#i', $path) === 1
    ) {
        return resolve_url($sourceUrl, 'reiki_taikei/taikei_default.html');
    }

    return $sourceUrl;
}

function gzip_path(string $path): string
{
    return str_ends_with(strtolower($path), '.gz') ? $path : $path . '.gz';
}

function logical_path(string $path): string
{
    if (!str_ends_with(strtolower($path), '.gz')) {
        return $path;
    }
    return substr($path, 0, -3);
}

function existing_path(string $path): ?string
{
    $candidates = [];
    if (str_ends_with(strtolower($path), '.gz')) {
        $candidates[] = $path;
        $candidates[] = logical_path($path);
    } else {
        $candidates[] = gzip_path($path);
        $candidates[] = $path;
    }

    foreach ($candidates as $candidate) {
        if (is_file($candidate)) {
            return $candidate;
        }
    }

    return null;
}

function read_file_bytes_auto(string $path): string
{
    $raw = file_get_contents($path);
    if (!is_string($raw)) {
        throw new RuntimeException("Failed to read {$path}");
    }
    if (!str_ends_with(strtolower($path), '.gz')) {
        return $raw;
    }

    $decoded = gzdecode($raw);
    if (!is_string($decoded)) {
        throw new RuntimeException("Failed to decode gzip file: {$path}");
    }
    return $decoded;
}

function read_text_file_auto(string $path): string
{
    return ensure_utf8(read_file_bytes_auto($path));
}

function write_text_file(string $path, string $content, bool $compress = false): string
{
    $finalPath = $compress ? gzip_path($path) : $path;
    ensure_dir(dirname($finalPath));

    if ($compress) {
        $encoded = gzencode($content, 6, ZLIB_ENCODING_GZIP);
        if (!is_string($encoded)) {
            throw new RuntimeException("Failed to gzip content for {$finalPath}");
        }
        file_put_contents($finalPath, $encoded);

        $plainPath = logical_path($finalPath);
        if ($plainPath !== $finalPath && is_file($plainPath)) {
            unlink($plainPath);
        }
    } else {
        file_put_contents($finalPath, $content);

        $gzPath = gzip_path($finalPath);
        if ($gzPath !== $finalPath && is_file($gzPath)) {
            unlink($gzPath);
        }
    }

    return $finalPath;
}

function sha256_string(string $content): string
{
    return hash('sha256', $content);
}

function sha256_file_auto(string $path): string
{
    return sha256_string(read_file_bytes_auto($path));
}

function load_json_file(string $path, array $default = []): array
{
    $existingPath = existing_path($path);
    if ($existingPath === null) {
        return $default;
    }

    $decoded = json_decode(read_text_file_auto($existingPath), true);
    return is_array($decoded) ? $decoded : $default;
}

function index_manifest_by_source(array $records): array
{
    $indexed = [];
    foreach ($records as $record) {
        if (!is_array($record)) {
            continue;
        }

        $sourceFile = trim((string)($record['source_file'] ?? ''));
        if ($sourceFile === '') {
            $storedSourceFile = trim((string)($record['stored_source_file'] ?? ''));
            if ($storedSourceFile !== '') {
                $sourceFile = basename(logical_path($storedSourceFile));
            }
        }
        if ($sourceFile === '') {
            continue;
        }

        $indexed[$sourceFile] = $record;
    }

    return $indexed;
}

function merge_manifest_record(array $manifestRecord, array $crawlRecord, string $sourceFile): array
{
    $merged = $manifestRecord;
    $merged['code'] = (string)($crawlRecord['code'] ?? $merged['code'] ?? '');
    $merged['title'] = trim((string)($merged['title'] ?? '')) !== ''
        ? (string)$merged['title']
        : (string)($crawlRecord['title'] ?? '');
    $merged['date'] = trim((string)($merged['date'] ?? '')) !== ''
        ? (string)$merged['date']
        : (string)($crawlRecord['date'] ?? '');
    $merged['number'] = trim((string)($merged['number'] ?? '')) !== ''
        ? (string)$merged['number']
        : (string)($crawlRecord['number'] ?? '');
    $merged['detail_url'] = (string)($crawlRecord['detail_url'] ?? $merged['detail_url'] ?? '');
    $merged['taxonomy_url'] = (string)($crawlRecord['taxonomy_url'] ?? $merged['taxonomy_url'] ?? '');
    $merged['taxonomy_path'] = (string)($crawlRecord['taxonomy_path'] ?? $merged['taxonomy_path'] ?? '');

    $existingPaths = is_array($merged['taxonomy_paths'] ?? null) ? $merged['taxonomy_paths'] : [];
    $currentPaths = is_array($crawlRecord['taxonomy_paths'] ?? null) ? $crawlRecord['taxonomy_paths'] : [];
    $merged['taxonomy_paths'] = array_values(array_unique(array_filter(array_merge($existingPaths, $currentPaths))));
    $merged['source_file'] = $sourceFile;

    return $merged;
}

function write_json_file(string $path, array $data, bool $compress = false): string
{
    $json = json_encode(
        $data,
        JSON_PRETTY_PRINT
        | JSON_UNESCAPED_UNICODE
        | JSON_UNESCAPED_SLASHES
        | JSON_INVALID_UTF8_SUBSTITUTE
    );
    if (!is_string($json)) {
        throw new RuntimeException('Failed to encode JSON for ' . $path . ': ' . json_last_error_msg());
    }
    return write_text_file($path, $json, $compress);
}

function open_ordinance_index(string $dbPath): ?PDO
{
    static $warnedUnavailable = false;

    if (!class_exists(PDO::class) || !in_array('sqlite', PDO::getAvailableDrivers(), true)) {
        if (!$warnedUnavailable) {
            fwrite(STDERR, "Warning: PDO SQLite is not available; skipped ordinance DB updates.\n");
            $warnedUnavailable = true;
        }
        return null;
    }

    ensure_dir(dirname($dbPath));
    $pdo = open_sqlite_pdo($dbPath);
    if (!ordinance_index_schema_compatible($pdo)) {
        // 旧 taikei の簡易 DB は列が足りないので、途中更新へ切り替える前に空で作り直す。
        $pdo = null;
        if (is_file($dbPath)) {
            unlink($dbPath);
        }
        $pdo = open_sqlite_pdo($dbPath);
    }
    ensure_ordinance_index_schema($pdo);
    return $pdo;
}

function open_sqlite_pdo(string $dbPath): PDO
{
    $pdo = new PDO('sqlite:' . $dbPath);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $pdo->exec('PRAGMA journal_mode = WAL');
    $pdo->exec('PRAGMA synchronous = NORMAL');
    // 親バッチの再 build や検索 read と競合しても、すぐ失敗せず少し待つ。
    $pdo->exec('PRAGMA busy_timeout = 30000');
    $pdo->exec('PRAGMA temp_store = MEMORY');
    $pdo->exec('PRAGMA cache_size = -32768');
    return $pdo;
}

function begin_ordinance_index_batch(PDO $pdo): void
{
    if (!$pdo->inTransaction()) {
        $pdo->beginTransaction();
    }
}

function commit_ordinance_index_batch(PDO $pdo): void
{
    if ($pdo->inTransaction()) {
        $pdo->commit();
    }
}

function rollback_ordinance_index_batch(PDO $pdo): void
{
    if ($pdo->inTransaction()) {
        $pdo->rollBack();
    }
}

function ordinance_index_schema_compatible(PDO $pdo): bool
{
    try {
        $rows = $pdo->query('PRAGMA table_info(ordinances)');
        if (!$rows instanceof PDOStatement) {
            return false;
        }
        $columns = [];
        foreach ($rows->fetchAll(PDO::FETCH_ASSOC) as $row) {
            $name = trim((string)($row['name'] ?? ''));
            if ($name !== '') {
                $columns[$name] = true;
            }
        }
    } catch (Throwable) {
        return false;
    }

    if ($columns === []) {
        return true;
    }

    $required = [
        'id',
        'filename',
        'title',
        'reading_kana',
        'sortable_kana',
        'primary_class',
        'secondary_tags',
        'necessity_score',
        'fiscal_impact_score',
        'regulatory_burden_score',
        'policy_effectiveness_score',
        'lens_tags',
        'lens_a_stance',
        'lens_b_stance',
        'combined_stance',
        'combined_reason',
        'document_type',
        'responsible_department',
        'reason',
        'enactment_date',
        'analyzed_at',
        'updated_at',
        'source_url',
        'source_file',
        'taxonomy_path',
        'taxonomy_paths',
        'content_text',
        'content_length',
    ];
    foreach ($required as $column) {
        if (!isset($columns[$column])) {
            return false;
        }
    }

    return true;
}

function ordinance_index_fts_columns(PDO $pdo): array
{
    try {
        $rows = $pdo->query('PRAGMA table_info(ordinances_fts)');
        if (!$rows instanceof PDOStatement) {
            return [];
        }
        return array_values(array_map(
            static fn(array $row): string => trim((string)($row['name'] ?? '')),
            $rows->fetchAll(PDO::FETCH_ASSOC)
        ));
    } catch (Throwable) {
        return [];
    }
}

function ordinance_index_fts_schema_matches(PDO $pdo): bool
{
    return ordinance_index_fts_columns($pdo) === [
        'title_terms',
        'reading_terms',
        'content_terms',
        'department_terms',
        'combined_reason_terms',
        'reason_terms',
        'secondary_terms',
        'lens_terms',
        'taxonomy_terms',
    ];
}

function ensure_ordinance_index_schema(PDO $pdo): void
{
    $pdo->exec(<<<'SQL'
CREATE TABLE IF NOT EXISTS ordinances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    reading_kana TEXT,
    sortable_kana TEXT,
    primary_class TEXT,
    secondary_tags TEXT,
    necessity_score INTEGER,
    fiscal_impact_score REAL,
    regulatory_burden_score REAL,
    policy_effectiveness_score REAL,
    lens_tags TEXT,
    lens_a_stance TEXT,
    lens_b_stance TEXT,
    combined_stance TEXT,
    combined_reason TEXT,
    document_type TEXT,
    responsible_department TEXT,
    reason TEXT,
    enactment_date TEXT,
    analyzed_at TEXT,
    updated_at TEXT,
    source_url TEXT,
    source_file TEXT,
    taxonomy_path TEXT,
    taxonomy_paths TEXT,
    content_text TEXT NOT NULL,
    content_length INTEGER NOT NULL DEFAULT 0
)
SQL);
    $pdo->exec('CREATE INDEX IF NOT EXISTS idx_ordinances_sortable_kana ON ordinances(sortable_kana)');
    $pdo->exec('CREATE INDEX IF NOT EXISTS idx_ordinances_class ON ordinances(primary_class)');
    $pdo->exec('CREATE INDEX IF NOT EXISTS idx_ordinances_necessity ON ordinances(necessity_score)');
    $pdo->exec('CREATE INDEX IF NOT EXISTS idx_ordinances_date ON ordinances(enactment_date)');
    $pdo->exec('CREATE INDEX IF NOT EXISTS idx_ordinances_combined_stance ON ordinances(combined_stance)');
    $pdo->exec('CREATE INDEX IF NOT EXISTS idx_ordinances_document_type ON ordinances(document_type)');
    if (!ordinance_index_fts_schema_matches($pdo)) {
        $pdo->exec('DROP TABLE IF EXISTS ordinances_fts');
        $pdo->exec(<<<'SQL'
CREATE VIRTUAL TABLE IF NOT EXISTS ordinances_fts USING fts5(
    title_terms,
    reading_terms,
    content_terms,
    department_terms,
    combined_reason_terms,
    reason_terms,
    secondary_terms,
    lens_terms,
    taxonomy_terms,
    tokenize = 'unicode61'
)
SQL);
        rebuild_ordinance_index_fts($pdo);
        return;
    }

    $pdo->exec(<<<'SQL'
CREATE VIRTUAL TABLE IF NOT EXISTS ordinances_fts USING fts5(
    title_terms,
    reading_terms,
    content_terms,
    department_terms,
    combined_reason_terms,
    reason_terms,
    secondary_terms,
    lens_terms,
    taxonomy_terms,
    tokenize = 'unicode61'
)
SQL);
}

function rebuild_ordinance_index_fts(PDO $pdo): void
{
    $stmt = $pdo->query(<<<'SQL'
SELECT
    id,
    title,
    reading_kana,
    content_text,
    responsible_department,
    combined_reason,
    reason,
    secondary_tags,
    lens_tags,
    taxonomy_path
FROM ordinances
SQL);
    if (!$stmt instanceof PDOStatement) {
        return;
    }

    foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
        $terms = japanese_search_document_terms_map([
            'title_terms' => (string)($row['title'] ?? ''),
            'reading_terms' => (string)($row['reading_kana'] ?? ''),
            'content_terms' => (string)($row['content_text'] ?? ''),
            'department_terms' => (string)($row['responsible_department'] ?? ''),
            'combined_reason_terms' => (string)($row['combined_reason'] ?? ''),
            'reason_terms' => (string)($row['reason'] ?? ''),
            'secondary_terms' => (string)($row['secondary_tags'] ?? ''),
            'lens_terms' => (string)($row['lens_tags'] ?? ''),
            'taxonomy_terms' => (string)($row['taxonomy_path'] ?? ''),
        ]);
        insert_ordinance_fts_row($pdo, (int)($row['id'] ?? 0), $terms);
    }
}

function insert_ordinance_fts_row(PDO $pdo, int $rowId, array $terms): void
{
    $stmt = $pdo->prepare(<<<'SQL'
INSERT INTO ordinances_fts (
    rowid, title_terms, reading_terms, content_terms, department_terms,
    combined_reason_terms, reason_terms, secondary_terms, lens_terms, taxonomy_terms
) VALUES (
    :rowid, :title_terms, :reading_terms, :content_terms, :department_terms,
    :combined_reason_terms, :reason_terms, :secondary_terms, :lens_terms, :taxonomy_terms
)
SQL);
    $stmt->execute([
        ':rowid' => $rowId,
        ':title_terms' => (string)($terms['title_terms'] ?? ''),
        ':reading_terms' => (string)($terms['reading_terms'] ?? ''),
        ':content_terms' => (string)($terms['content_terms'] ?? ''),
        ':department_terms' => (string)($terms['department_terms'] ?? ''),
        ':combined_reason_terms' => (string)($terms['combined_reason_terms'] ?? ''),
        ':reason_terms' => (string)($terms['reason_terms'] ?? ''),
        ':secondary_terms' => (string)($terms['secondary_terms'] ?? ''),
        ':lens_terms' => (string)($terms['lens_terms'] ?? ''),
        ':taxonomy_terms' => (string)($terms['taxonomy_terms'] ?? ''),
    ]);
}

function upsert_ordinance_index_row(PDO $pdo, array $record, string $htmlPath, ?string $markdownPath): void
{
    $storedHtmlPath = existing_path($htmlPath) ?? $htmlPath;
    if (!is_file($storedHtmlPath)) {
        return;
    }

    $cleanHtml = read_text_file_auto($storedHtmlPath);
    $markdown = is_string($markdownPath) && is_file($markdownPath) ? read_text_file_auto($markdownPath) : '';
    $contentText = ordinance_index_content_text($cleanHtml, $markdown);
    if ($contentText === '') {
        return;
    }

    $sourceFile = trim((string)($record['source_file'] ?? ''));
    if ($sourceFile === '') {
        return;
    }

    $filename = (string)pathinfo($sourceFile, PATHINFO_FILENAME);
    $title = html_entity_decode((string)($record['title'] ?? ''), ENT_QUOTES | ENT_HTML5, 'UTF-8');
    if ($title === '') {
        $title = $filename;
    }
    $number = html_entity_decode((string)($record['number'] ?? ''), ENT_QUOTES | ENT_HTML5, 'UTF-8');
    $date = trim((string)($record['enactment_date'] ?? ''));
    $taxonomyPaths = array_values(array_filter(is_array($record['taxonomy_paths'] ?? null) ? $record['taxonomy_paths'] : []));
    $updatedAt = ordinance_index_updated_at($storedHtmlPath, $markdownPath);
    $terms = japanese_search_document_terms_map([
        'title_terms' => $title,
        'reading_terms' => $title,
        'content_terms' => $contentText,
        'department_terms' => '',
        'combined_reason_terms' => '',
        'reason_terms' => '',
        'secondary_terms' => '',
        'lens_terms' => '',
        'taxonomy_terms' => trim((string)($record['taxonomy_path'] ?? '')),
    ]);

    $params = [
        ':filename' => $filename,
        ':title' => $title,
        ':reading_kana' => $title,
        ':sortable_kana' => sortable_title($title),
        ':primary_class' => '',
        ':secondary_tags' => '',
        ':necessity_score' => -1,
        ':fiscal_impact_score' => 0,
        ':regulatory_burden_score' => 0,
        ':policy_effectiveness_score' => 0,
        ':lens_tags' => '',
        ':lens_a_stance' => '',
        ':lens_b_stance' => '',
        ':combined_stance' => '',
        ':combined_reason' => '',
        ':document_type' => detect_document_type($number, $title),
        ':responsible_department' => '',
        ':reason' => '',
        ':enactment_date' => $date !== '' ? $date : null,
        ':analyzed_at' => '',
        ':updated_at' => $updatedAt,
        ':source_url' => trim((string)($record['detail_url'] ?? '')),
        ':source_file' => $sourceFile,
        ':taxonomy_path' => trim((string)($record['taxonomy_path'] ?? '')),
        ':taxonomy_paths' => implode(',', $taxonomyPaths),
        ':content_text' => $contentText,
        ':content_length' => text_length($contentText),
    ];

    $select = $pdo->prepare('SELECT id FROM ordinances WHERE filename = :filename');
    $select->execute([':filename' => $filename]);
    $rowId = $select->fetchColumn();

    if ($rowId === false) {
        $stmt = $pdo->prepare(<<<'SQL'
INSERT INTO ordinances (
    filename, title, reading_kana, sortable_kana, primary_class, secondary_tags,
    necessity_score, fiscal_impact_score, regulatory_burden_score, policy_effectiveness_score,
    lens_tags, lens_a_stance, lens_b_stance, combined_stance, combined_reason,
    document_type, responsible_department, reason, enactment_date, analyzed_at,
    updated_at, source_url, source_file, taxonomy_path, taxonomy_paths, content_text, content_length
) VALUES (
    :filename, :title, :reading_kana, :sortable_kana, :primary_class, :secondary_tags,
    :necessity_score, :fiscal_impact_score, :regulatory_burden_score, :policy_effectiveness_score,
    :lens_tags, :lens_a_stance, :lens_b_stance, :combined_stance, :combined_reason,
    :document_type, :responsible_department, :reason, :enactment_date, :analyzed_at,
    :updated_at, :source_url, :source_file, :taxonomy_path, :taxonomy_paths, :content_text, :content_length
)
SQL);
        $stmt->execute($params);
        $rowId = (int)$pdo->lastInsertId();
    } else {
        $rowId = (int)$rowId;
        $pdo->prepare('DELETE FROM ordinances_fts WHERE rowid = :rowid')->execute([':rowid' => $rowId]);
        $stmt = $pdo->prepare(<<<'SQL'
UPDATE ordinances
   SET filename = :filename,
       title = :title,
       reading_kana = :reading_kana,
       sortable_kana = :sortable_kana,
       primary_class = :primary_class,
       secondary_tags = :secondary_tags,
       necessity_score = :necessity_score,
       fiscal_impact_score = :fiscal_impact_score,
       regulatory_burden_score = :regulatory_burden_score,
       policy_effectiveness_score = :policy_effectiveness_score,
       lens_tags = :lens_tags,
       lens_a_stance = :lens_a_stance,
       lens_b_stance = :lens_b_stance,
       combined_stance = :combined_stance,
       combined_reason = :combined_reason,
       document_type = :document_type,
       responsible_department = :responsible_department,
       reason = :reason,
       enactment_date = :enactment_date,
       analyzed_at = :analyzed_at,
       updated_at = :updated_at,
       source_url = :source_url,
       source_file = :source_file,
       taxonomy_path = :taxonomy_path,
       taxonomy_paths = :taxonomy_paths,
       content_text = :content_text,
       content_length = :content_length
 WHERE id = :rowid
SQL);
        $stmt->execute($params + [':rowid' => $rowId]);
    }

    insert_ordinance_fts_row($pdo, $rowId, $terms);
}

function sortable_title(string $title): string
{
    $normalized = preg_replace('/\s+/u', '', trim($title));
    return $normalized === null ? trim($title) : $normalized;
}

function detect_document_type(string $number, string $title): string
{
    $candidates = [$number, $title];
    foreach ($candidates as $candidate) {
        if (preg_match('/条例/u', $candidate)) {
            return '条例';
        }
        if (preg_match('/規則/u', $candidate)) {
            return '規則';
        }
        if (preg_match('/(規程|訓令)/u', $candidate)) {
            return '規程';
        }
        if (preg_match('/要綱/u', $candidate)) {
            return '要綱';
        }
    }

    return 'その他';
}

function ordinance_index_content_text(string $cleanHtml, string $markdown): string
{
    $text = clean_html_to_text($cleanHtml);
    if ($text === '' && $markdown !== '') {
        $text = markdown_to_text_for_index($markdown);
    }
    return trim($text);
}

function clean_html_to_text(string $html): string
{
    $text = preg_replace('/<script[\s\S]*?<\/script>/iu', '', $html) ?? $html;
    $text = preg_replace('/<style[\s\S]*?<\/style>/iu', '', $text) ?? $text;
    $text = preg_replace('/<br\s*\/?>/iu', "\n", $text) ?? $text;
    $text = preg_replace('/<\/(div|p|li|tr|table|section|article|h[1-6])>/iu', "\n", $text) ?? $text;
    $text = strip_tags($text);
    $text = html_entity_decode($text, ENT_QUOTES | ENT_HTML5, 'UTF-8');
    $text = str_replace(["\r\n", "\r"], "\n", $text);
    $text = preg_replace('/\n{3,}/u', "\n\n", $text) ?? $text;
    return normalize_whitespace($text);
}

function markdown_to_text_for_index(string $markdown): string
{
    $text = preg_replace('/!\[[^\]]*\]\([^)]+\)/u', '', $markdown) ?? $markdown;
    $text = preg_replace('/\[([^\]]+)\]\([^)]+\)/u', '$1', $text) ?? $text;
    $text = preg_replace('/^[#>*`\-\+\s]+/mu', '', $text) ?? $text;
    $text = preg_replace('/\*{1,2}([^*]+)\*{1,2}/u', '$1', $text) ?? $text;
    $text = preg_replace('/`([^`]+)`/u', '$1', $text) ?? $text;
    $text = str_replace(["\r\n", "\r"], "\n", $text);
    $text = preg_replace('/\n{3,}/u', "\n\n", $text) ?? $text;
    return normalize_whitespace($text);
}

function ordinance_index_updated_at(string $htmlPath, ?string $markdownPath): string
{
    $mtimes = [];
    if (is_file($htmlPath)) {
        $mtime = filemtime($htmlPath);
        if (is_int($mtime) || is_float($mtime)) {
            $mtimes[] = (int)$mtime;
        }
    }
    if (is_string($markdownPath) && is_file($markdownPath)) {
        $mtime = filemtime($markdownPath);
        if (is_int($mtime) || is_float($mtime)) {
            $mtimes[] = (int)$mtime;
        }
    }
    $ts = $mtimes !== [] ? max($mtimes) : time();
    return gmdate('Y-m-d H:i:s', $ts);
}

function text_length(string $text): int
{
    return function_exists('mb_strlen') ? (int)mb_strlen($text, 'UTF-8') : strlen($text);
}

function ensure_dir(string $path): void
{
    if (!is_dir($path) && !mkdir($path, 0777, true) && !is_dir($path)) {
        throw new RuntimeException("Failed to create directory: {$path}");
    }
}

function throttled_sleep(): void
{
    usleep(TAIKEI_SLEEP_USEC);
}

function emit_progress(int $current, int $total, string $statePath = ''): void
{
    if ($statePath !== '') {
        write_progress_state($statePath, $current, $total);
    }
    echo sprintf("[PROGRESS] unit=ordinance current=%d total=%d\n", max(0, $current), max(0, $total));
    flush();
}

function write_progress_state(string $path, int $current, int $total): void
{
    $payload = [
        'version' => 1,
        'progress_current' => max(0, $current),
        'progress_total' => max(0, $total),
        'progress_unit' => 'ordinance',
    ];
    $tempPath = $path . '.tmp';
    $json = json_encode($payload, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    if (!is_string($json)) {
        return;
    }
    ensure_dir(dirname($path));
    file_put_contents($tempPath, $json . "\n");
    rename($tempPath, $path);
}

function h(string $value): string
{
    return htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}
