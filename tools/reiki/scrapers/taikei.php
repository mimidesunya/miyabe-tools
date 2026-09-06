#!/usr/bin/env php
<?php
declare(strict_types=1);

// taikei / g-reiki 系の例規集システム向けスクレイパ。
//
// これらの provider は分類ツリーと例規ごとの HTML ページを公開している。
// まず再開・更新確認用の crawl manifest を保持し、その後、各例規を他の
// 例規集スクレイパと同じ source/html/markdown/json ディレクトリ構成へ正規化する。
const TAIKEI_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36';
// 例規 1 件ごとの間隔。0.12 秒（毎秒 8 件）では 429 を招き、取得が
// 途中で止まっていた。取得元は自治体の本番サイトなので余裕を持たせる。
const TAIKEI_SLEEP_USEC = 600000;
// 429 を一度でも受けた取得元は、そのあと間隔を広げて続ける。
const TAIKEI_SLEEP_AFTER_RATE_LIMIT_USEC = 3000000;
const TAIKEI_FETCH_MAX_ATTEMPTS = 4;
const TAIKEI_FETCH_RETRY_BASE_USEC = 750000;
// 429 用。通常の一時エラーより長く空け、上限も高くする。
const TAIKEI_FETCH_RATE_LIMIT_BASE_USEC = 20000000;
const TAIKEI_FETCH_RATE_LIMIT_MAX_USEC = 120000000;
const TAIKEI_FETCH_RATE_LIMIT_MAX_ATTEMPTS = 8;
const TAIKEI_LIKE_SYSTEM_TYPES = ['taikei' => true, 'g-reiki' => true];
// 原典が同じでも変換規則を直せば成果物は変わる。パーサを変更したときは
// この値を必ず上げ、保存済み source に新しい規則を適用させる。
const TAIKEI_PARSER_VERSION = 2;
// 一覧に現れない本文改正も拾うため、個票を最後に実照会した時刻で巡回する。
const TAIKEI_VALIDATION_INTERVAL_SECONDS = 90 * 86400;

// 読み込んだだけで走り出さないようにする。テストから関数だけを使いたい。
if (PHP_SAPI === 'cli' && isset($argv[0]) && realpath($argv[0]) === realpath(__FILE__)) {
    main($argv);
}

function main(array $argv): void
{
    $options = getopt('', ['slug::', 'system-type::', 'code::', 'name::', 'source-url::', 'limit::', 'force', 'crawl-only', 'check-updates']);
    $systemType = cli_option_value($options, $argv, 'system-type', 'taikei');
    if (!isset(TAIKEI_LIKE_SYSTEM_TYPES[$systemType])) {
        throw new RuntimeException("Unsupported taikei-like system_type: {$systemType}");
    }
    $slugOption = cli_option_value($options, $argv, 'slug');
    $slug = $slugOption !== '' ? $slugOption : default_slug_for_system($systemType);
    $limitOption = cli_option_value($options, $argv, 'limit');
    $limit = $limitOption !== '' ? max(0, (int)$limitOption) : 0;
    $force = cli_has_flag($options, $argv, 'force');
    $crawlOnly = cli_has_flag($options, $argv, 'crawl-only');
    $checkUpdates = cli_has_flag($options, $argv, 'check-updates');

    $target = load_reiki_target_from_cli(
        $slug,
        $systemType,
        [
            'code' => cli_option_value($options, $argv, 'code'),
            'name' => cli_option_value($options, $argv, 'name'),
            'source_url' => cli_option_value($options, $argv, 'source-url'),
        ]
    );
    // 取得の速度はホスト単位で覚える。531 自治体が 1 ホストを共有しており、
    // 自治体ごとに覚え直すと毎回 429 に当ててから遅くすることになる。
    current_source_host((string)$target['source_url']);
    $dataRoot = (string)$target['data_root'];
    $workRoot = (string)$target['work_root'];
    $sourceDir = (string)$target['source_dir'];
    $htmlDir = (string)$target['html_dir'];
    $jsonDir = (string)$target['classification_dir'];
    $imageDir = (string)$target['image_dir'];
    $markdownDir = (string)$target['markdown_dir'];
    $statePath = $workRoot . DIRECTORY_SEPARATOR . 'scrape_state.json';

    ensure_dir($dataRoot);
    ensure_dir($workRoot);
    ensure_dir($sourceDir);
    ensure_dir($htmlDir);
    ensure_dir($jsonDir);
    ensure_dir($imageDir);
    ensure_dir($markdownDir);
    echo "Crawling {$target['name']} reiki taxonomy...\n";
    // 走査する取得元が決まってから、目次の入口を確かめる。台帳を読むだけの
    // 場面で解決すると、全国分の入口ページへ一斉に当たることになる。
    $crawl = crawl_taxonomy(derive_taikei_entry_url((string)$target['source_url']));
    $records = array_values($crawl['records']);
    usort($records, static fn(array $a, array $b): int => strcmp((string)$a['code'], (string)$b['code']));

    $manifestPath = $workRoot . DIRECTORY_SEPARATOR . 'source_manifest.json.gz';
    // 走っている最中の一覧は正本と分ける。25 件ごとに正本を上書きすると、
    // 途中で死んだときに短い一覧がそのまま残る。
    $partialManifestPath = $workRoot . DIRECTORY_SEPARATOR . 'source_manifest.partial.json.gz';
    $coveragePath = $workRoot . DIRECTORY_SEPARATOR . 'source_coverage.json';
    $taxonomyPath = $workRoot . DIRECTORY_SEPARATOR . 'taxonomy_pages.json.gz';
    try {
        $previousManifestRecords = load_json_file($manifestPath, []);
    } catch (RuntimeException $e) {
        // 壊れた gzip を読めずに毎回落ちると、その自治体は永久に取れない（浦添市）。
        // 退避して、今回は前回の一覧なしで取り直す。
        $corruptPath = $manifestPath . '.corrupt-' . date('Ymd_His');
        @rename($manifestPath, $corruptPath);
        fwrite(STDERR, "[WARN] previous manifest unreadable; moved to {$corruptPath}: {$e->getMessage()}\n");
        $previousManifestRecords = [];
    }
    $previousManifestBySource = index_manifest_by_source($previousManifestRecords);
    $catalogVersion = catalog_version_from_pages($crawl['pages']);
    $previousCatalogVersion = first_manifest_catalog_version($previousManifestRecords);
    if ($catalogVersion !== '') {
        echo "[INFO] catalog content current: {$catalogVersion}\n";
    } else {
        echo "[INFO] catalog content current: not found\n";
    }
    $catalogChanged = null;
    if ($catalogVersion !== '') {
        $catalogChanged = $previousCatalogVersion === '' || $previousCatalogVersion !== $catalogVersion;
    }
    if ($previousCatalogVersion !== '' && $catalogVersion !== '') {
        $statusLabel = $catalogChanged ? 'changed' : 'unchanged';
        echo "[INFO] catalog content current {$statusLabel}: previous={$previousCatalogVersion} current={$catalogVersion}\n";
    }
    write_json_file($taxonomyPath, array_values($crawl['pages']), true);

    echo 'Found ' . count($records) . " ordinance pages across " . count($crawl['pages']) . " taxonomy pages.\n";
    // 0 件で成功にしない。取得元の作りが変わって目次を辿れなくなっても、
    // 成功として記録されると 30 日ごとに同じ 0 件を繰り返すだけで、
    // 誰も気づかない。能代市はこの形で例規が 1 件も無いまま残っていた。
    // d1-law 側は同じ状況で落ちるようにしてある。
    if (count($records) === 0) {
        throw new RuntimeException(
            'No ordinance pages were collected; refusing to mark the target as '
            . 'successfully scraped.'
        );
    }
    if ($crawlOnly) {
        emit_progress(count($records), count($records), $statePath);
        write_json_file($manifestPath, $records, true);
        echo "Saved crawl manifest only: {$manifestPath}\n";
        return;
    }

    $planState = build_source_plan(
        $records,
        $sourceDir,
        $htmlDir,
        $markdownDir,
        $previousManifestBySource
    );
    // 一覧差分は「問い合わせるか」の唯一の条件にしない。差分が見えた個票を
    // 先に確認しつつ、一覧が不変でも期限が来た個票を同じ巡回で確認する。
    $plans = prioritize_source_plans($planState['plans'], $checkUpdates);
    if ($limit > 0) {
        $plans = array_slice($plans, 0, $limit);
    }
    $total = count($plans);
    $workMode = assign_work_mode($plans, $force, $checkUpdates, $catalogChanged);
    $incompleteCount = (int)$workMode['incomplete_count'];
    $listedChangeCount = (int)$workMode['listed_change_count'];
    $validationDueCount = (int)$workMode['validation_due_count'];
    $parserRefreshCount = (int)$workMode['parser_refresh_count'];
    $resumeMode = (bool)$workMode['resume_mode'];
    $updateMode = (bool)$workMode['update_mode'];
    $workCount = (int)$workMode['work_count'];

    if ($force) {
        echo "[MODE] force rebuild: {$total}/{$total}\n";
    } elseif ($resumeMode) {
        echo "[MODE] resume missing ordinances only: {$incompleteCount}/{$total}\n";
    }
    if (!$force && $updateMode) {
        echo "[MODE] update validation: listed={$listedChangeCount} due={$validationDueCount} total={$workMode['validation_count']}/{$total}\n";
    }
    if (!$force && $parserRefreshCount > 0) {
        echo "[MODE] rebuild saved sources for parser generation: {$parserRefreshCount}/{$total}\n";
    }
    if (!$force && $workCount === 0) {
        echo $checkUpdates
            ? "[MODE] complete; no ordinance validation is due.\n"
            : "[MODE] complete; no update check requested.\n";
    }

    $progressBase = (int)$workMode['progress_base'];
    emit_progress($progressBase, $total, $statePath);

    $downloaded = 0;
    $checked = 0;
    $skipped = 0;
    $parsed = 0;
    $reused = 0;
    $parserGenerationRefreshed = 0;
    $failed = 0;
    $manifests = [];
    $processedWork = 0;

    foreach ($plans as $index => $plan) {
        $record = $plan['record'];
        $sourceFileName = (string)$plan['source_file_name'];
        $sourcePath = (string)$plan['source_path'];
        $htmlPath = (string)$plan['html_path'];
        $existingSourcePath = is_string($plan['existing_source_path']) ? $plan['existing_source_path'] : null;
        $storedSourcePath = $existingSourcePath ?? gzip_path($sourcePath);
        $previousManifest = is_array($plan['previous_manifest'] ?? null) ? $plan['previous_manifest'] : null;
        $shouldWork = (bool)($plan['should_work'] ?? false);
        $shouldFetch = (bool)($plan['should_fetch'] ?? false);

        $sourceHtml = '';
        $sourceHash = (string)($plan['source_sha256'] ?? '');
        $sourceChanged = false;
        $validationResponse = null;
        $validatedAt = null;

        if (!$shouldWork) {
            $skipped++;
            $manifestEntry = merge_manifest_record($previousManifest ?? [], $record, $sourceFileName);
        } else {
            if ($shouldFetch) {
                // 1 件が取れないだけで走査全体を落とすと、その回で取れたはずの
                // 残りも取り逃がす。取得済みは manifest に残し、失敗した例規は
                // 次回の resume で拾い直す。
                try {
                    $requestHeaders = (!$force && $existingSourcePath !== null)
                        ? taikei_conditional_request_headers($previousManifest)
                        : [];
                    $validationResponse = fetch_url_response((string)$record['detail_url'], $requestHeaders);
                    $validatedAt = gmdate('c');
                } catch (RuntimeException $exception) {
                    $failed++;
                    fwrite(STDERR, sprintf(
                        "Warning: skipping %s: %s\n",
                        (string)$record['detail_url'],
                        $exception->getMessage()
                    ));
                    throttled_sleep();
                    continue;
                }

                if ((bool)($validationResponse['not_modified'] ?? false)) {
                    if ($existingSourcePath === null) {
                        $failed++;
                        fwrite(STDERR, sprintf(
                            "Warning: received HTTP 304 without a saved source for %s\n",
                            (string)$record['detail_url']
                        ));
                        throttled_sleep();
                        continue;
                    }
                    $storedSourcePath = $existingSourcePath;
                    $checked++;
                } else {
                    $fetchedHtml = (string)($validationResponse['body'] ?? '');
                    try {
                        $changedByHash = taikei_source_changed($sourceHash, $fetchedHtml, $force);
                    } catch (RuntimeException $exception) {
                        $failed++;
                        fwrite(STDERR, sprintf(
                            "Warning: skipping %s: %s\n",
                            (string)$record['detail_url'],
                            $exception->getMessage()
                        ));
                        throttled_sleep();
                        continue;
                    }
                    $fetchedHash = sha256_string($fetchedHtml);
                    if ($existingSourcePath !== null && !$changedByHash) {
                        // validator を返さない取得元でも、本文 hash が同じなら保存済み
                        // source を正本のまま使える。
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
                }

                throttled_sleep();
            } else {
                // パーサ世代だけが進んだ場合は取得元へ問い合わせず、保存済み
                // source を変換する。個票を見ていないので検証時刻は進めない。
                $skipped++;
            }

            $needsParse = $force
                || $sourceChanged
                || (bool)($plan['needs_parse'] ?? false);

            if ($needsParse) {
                $parsedRecord = parse_and_store_taikei_source(
                    $record,
                    $storedSourcePath,
                    $htmlPath,
                    (string)$plan['markdown_path'],
                    $sourceHtml
                );
                // 304 で再変換した場合にも、前回の validator と検証履歴を失わない。
                $manifestEntry = merge_manifest_record(
                    array_merge($previousManifest ?? [], $parsedRecord),
                    $record,
                    $sourceFileName
                );
                $parsed++;
                if ((bool)($plan['needs_parser_refresh'] ?? false)) {
                    $parserGenerationRefreshed++;
                }
            } else {
                $manifestEntry = merge_manifest_record($previousManifest ?? [], $record, $sourceFileName);
                $reused++;
            }
        }

        $manifestEntry['source_file'] = $sourceFileName;
        $manifestEntry['stored_source_file'] = basename($storedSourcePath);
        $sourceHash = $sourceHash !== '' ? $sourceHash : sha256_file_auto($storedSourcePath);
        $manifestEntry = finalize_taikei_manifest(
            $manifestEntry,
            $sourceHash,
            $validationResponse,
            $validatedAt
        );
        $manifestEntry['catalog_content_current'] = $catalogVersion;
        $manifestEntry['checked_updates'] = $checkUpdates;
        $manifestEntry['updated_at'] = gmdate('c');
        $manifests[] = $manifestEntry;
        if ($shouldWork) {
            $processedWork++;
            emit_progress($progressBase + $processedWork, $total, $statePath);
        }

        if ((($index + 1) % 25) === 0 || ($index + 1) === $total) {
            // 中断後の補完でも detail_url や taxonomy を拾えるよう、途中でも保存する。
            // 正本ではなく途中経過へ書く（正本を縮めないため）。
            write_json_file($partialManifestPath, $manifests, true);
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

    // 問い合わせの優先順は状態で変わるが、正本 manifest の並びは source 名で
    // 安定させ、毎周期の無意味な全行差分を作らない。
    usort($manifests, static fn(array $a, array $b): int => strcmp(
        (string)($a['source_file'] ?? ''),
        (string)($b['source_file'] ?? '')
    ));

    // 目録に並んだ件数が、この取得元の申告そのもの。失敗した分は取れていない。
    $declaredTotal = count($records);
    $walkComplete = $failed === 0 && $limit <= 0 && $total === $declaredTotal;
    $existingManifest = load_json_file($manifestPath, []);
    $previousManifestCount = count($existingManifest);
    // 前回より減っていたら上書きしない。取り切れた走査でも 2 割超は拒む。
    // 走査が短く終わった実行が正本を置き換えると、ディスクに残るファイルが
    // 一斉に孤児になる。
    $largeDrop = $previousManifestCount > 0
        && count($manifests) < $previousManifestCount * 0.8;
    $manifestWritten = true;
    if ($previousManifestCount > count($manifests) && (!$walkComplete || $largeDrop)) {
        $manifestWritten = false;
        write_json_file($workRoot . DIRECTORY_SEPARATOR . 'source_manifest.shrunk.json.gz', $manifests, true);
        fwrite(STDERR, sprintf(
            'Warning: 今回の走査は %d件で、前回の %d件より少ないため上書きしません。'
            . '今回の分は source_manifest.shrunk.json.gz に残しました。' . PHP_EOL,
            count($manifests),
            $previousManifestCount
        ));
    } else {
        write_json_file($manifestPath, $manifests, true);
    }
    @unlink($partialManifestPath);

    // 失敗した分は取れていない。n/n で終えるとキューが完了と読む。
    emit_progress(max(0, $total - $failed), $total, $statePath);
    write_json_file($coveragePath, [
        'version' => 2,
        'kind' => 'catalog',
        'declares' => true,
        'observed_at' => date('Ymd_His'),
        'declared_total' => $declaredTotal,
        'limited' => $limit > 0,
        'collected' => max(0, $total - $failed),
        'failed' => $failed,
        'manifest_shrunk' => !$manifestWritten,
        'manifest_previous' => $previousManifestCount,
        'complete' => $walkComplete && $manifestWritten,
    ], false);
    echo "\nFinished {$target['name']} scrape.\n";
    echo "  Source HTML: {$sourceDir}\n";
    echo "  Clean HTML: {$htmlDir}\n";
    echo "  Markdown: {$markdownDir}\n";
    echo "  Manifest: {$manifestPath}\n";
    echo "  Downloaded: {$downloaded}\n";
    echo "  Checked existing: {$checked}\n";
    echo "  Skipped existing: {$skipped}\n";
    echo "  Parsed: {$parsed}\n";
    echo "  Parser generation refreshed: {$parserGenerationRefreshed}\n";
    echo "  Reused manifest: {$reused}\n";
    echo "  Failed: {$failed}\n";
    if ($parserGenerationRefreshed > 0) {
        echo "[ACTION] Saved sources were regenerated; enqueue the OpenSearch index update.\n";
    }
}

function cli_option_value(array $options, array $argv, string $name, string $default = ''): string
{
    $value = $options[$name] ?? null;
    if (is_string($value) && trim($value) !== '') {
        return trim($value);
    }

    $flag = '--' . $name;
    $prefix = $flag . '=';
    $count = count($argv);
    for ($i = 1; $i < $count; $i++) {
        $arg = (string)$argv[$i];
        if (str_starts_with($arg, $prefix)) {
            return trim(substr($arg, strlen($prefix)));
        }
        if ($arg === $flag && isset($argv[$i + 1])) {
            $next = (string)$argv[$i + 1];
            if (!str_starts_with($next, '--')) {
                return trim($next);
            }
        }
    }

    return $default;
}

function cli_has_flag(array $options, array $argv, string $name): bool
{
    if (array_key_exists($name, $options)) {
        return true;
    }

    $flag = '--' . $name;
    foreach ($argv as $arg) {
        if ((string)$arg === $flag) {
            return true;
        }
    }
    return false;
}

function crawl_taxonomy(string $entryUrl): array
{
    $queue = [$entryUrl];
    $visited = [];
    $pages = [];
    $records = [];
    // 開けなかった目次ページ。その先の例規はまるごと見えなくなる。
    $missed = [];

    while ($queue !== []) {
        $url = array_shift($queue);
        if (!is_string($url) || isset($visited[$url])) {
            continue;
        }

        $visited[$url] = true;
        try {
            $html = fetch_url($url);
        } catch (RuntimeException $exception) {
            // 目次の枝が 1 本開けないだけで自治体をまるごと落とさない。
            // 与那国町は空白入りのリンク 1 本で例規 0 件になっていた。
            // 数えておいて、最後に 0 件なら失敗として扱う。
            $missed[] = $url;
            fwrite(STDERR, "[WARN] taxonomy page unavailable: {$url}
");
            continue;
        }
        $dom = create_dom($html);
        $xpath = new DOMXPath($dom);
        $currentPath = extract_taxonomy_path($xpath);

        $pages[$url] = [
            'url' => $url,
            'path' => $currentPath,
            'catalog_content_current' => extract_catalog_version($html),
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

    if ($missed !== []) {
        echo '[WARN] ' . count($missed) . " taxonomy pages could not be opened.
";
    }

    return [
        'pages' => $pages,
        'records' => $records,
        'missed' => $missed,
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

// 取得元のリンクに余分な空白が混ざることがある。与那国町の体系目次は
// `r_taikei_13_08_01_ 1.html` と書くが、実体は空白の無い名前である。
// そのまま辿ると 404 になり、その枝の先が丸ごと見えなくなる。
// 符号化された `%20` は本物なので触らない。生の空白だけ落とす。
function strip_href_whitespace(string $href): string
{
    return (string)preg_replace('/\s+/u', '', trim($href));
}

function extract_taxonomy_links(DOMXPath $xpath, string $pageUrl): array
{
    $links = [];
    // 入口が `reiki_menu.html` の自治体がある。体系目次はそこからのリンクで、
    // `ul#navigation` には入っていない。能代市はこれで 1 ページも辿れず、
    // 例規 0 件のまま成功として記録されていた。
    // 枝がどの入れ物に入っているかは取得元ごとに違う（`ul#navigation` の
    // ことも、五十音目次のように別の並びのこともある）。入れ物を決め打ち
    // せず、**行き先で選ぶ**。下の絞り込みが目次だけを通す。
    foreach ($xpath->query('//a[@href]') as $node) {
        if (!$node instanceof DOMElement) {
            continue;
        }

        $href = strip_href_whitespace($node->getAttribute('href'));
        if ($href === '' || str_starts_with($href, 'javascript:')) {
            continue;
        }

        $absolute = resolve_url($pageUrl, $href);
        // 登録されている入口が五十音順目次のことがある（矢祭町・廿日市市）。
        // 体系目次の枝しか辿らないと、そこから 1 歩も進めず 0 件で終わる。
        // どちらの目次からも辿れるようにする。
        if (!str_contains($absolute, '/reiki_taikei/') && !str_contains($absolute, '/reiki_kana/')) {
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

function parse_and_store_taikei_source(
    array $record,
    string $storedSourcePath,
    string $htmlPath,
    string $markdownPath,
    string $sourceHtml = ''
): array {
    if ($sourceHtml === '') {
        $sourceHtml = read_text_file_auto($storedSourcePath);
    }
    $parsedRecord = parse_taikei_ordinance_html(
        $sourceHtml,
        (string)$record['detail_url'],
        $record
    );
    write_text_file($htmlPath, $parsedRecord['clean_html']);
    write_text_file($markdownPath, $parsedRecord['markdown'], true);
    unset($parsedRecord['clean_html'], $parsedRecord['markdown']);
    return $parsedRecord;
}

function fetch_url(string $url): string
{
    $response = fetch_url_response($url);
    if (!is_string($response['body'] ?? null)) {
        throw new RuntimeException("Unexpected empty response for {$url}");
    }
    return (string)$response['body'];
}

function fetch_url_response(string $url, array $requestHeaders = []): array
{
    $requestHeaders = normalize_http_request_headers($requestHeaders);
    if (extension_loaded('curl')) {
        $verifySsl = true;
        $lastStatus = 0;
        $lastError = '';

        for ($attempt = 1; $attempt <= TAIKEI_FETCH_RATE_LIMIT_MAX_ATTEMPTS; $attempt++) {
            [$body, $status, $error, $responseHeaders] = curl_fetch($url, $verifySsl, $requestHeaders);
            if (($body === false || $status >= 400) && $verifySsl && should_retry_insecure($error)) {
                warn_retry_insecure();
                $verifySsl = false;
                [$body, $status, $error, $responseHeaders] = curl_fetch($url, false, $requestHeaders);
            }

            if ($status === 304 || ($body !== false && $status < 400)) {
                return build_fetch_response($body, $status, $responseHeaders);
            }

            $lastStatus = $status;
            $lastError = $error;
            if ($attempt >= fetch_max_attempts($status) || !should_retry_fetch($status, $error)) {
                break;
            }

            wait_for_fetch_retry($url, $attempt, $status, $error);
        }

        throw new RuntimeException("Failed to fetch {$url}: " . format_fetch_failure($lastStatus, $lastError));
    }

    $verifySsl = true;
    $lastStatus = 0;
    $lastError = '';

    for ($attempt = 1; $attempt <= TAIKEI_FETCH_RATE_LIMIT_MAX_ATTEMPTS; $attempt++) {
        [$body, $status, $error, $responseHeaders] = stream_fetch($url, $verifySsl, $requestHeaders);
        if ($body === false && $status !== 304 && $verifySsl && should_retry_insecure($error)) {
            warn_retry_insecure();
            $verifySsl = false;
            [$body, $status, $error, $responseHeaders] = stream_fetch($url, false, $requestHeaders);
        }

        if ($status === 304 || ($body !== false && $status < 400)) {
            return build_fetch_response($body, $status, $responseHeaders);
        }

        $lastStatus = $status;
        $lastError = $error;
        if ($attempt >= fetch_max_attempts($status) || !should_retry_fetch($status, $error)) {
            break;
        }

        wait_for_fetch_retry($url, $attempt, $status, $error);
    }

    throw new RuntimeException("Failed to fetch {$url}: " . format_fetch_failure($lastStatus, $lastError));
}

function normalize_http_request_headers(array $headers): array
{
    $normalized = [];
    foreach ($headers as $header) {
        if (!is_string($header)) {
            continue;
        }
        $header = trim($header);
        if ($header === '') {
            continue;
        }
        if (str_contains($header, "\r") || str_contains($header, "\n")) {
            throw new InvalidArgumentException('HTTP request header must not contain newlines.');
        }
        $normalized[] = $header;
    }
    return $normalized;
}

function build_fetch_response(string|false $body, int $status, array $responseHeaders): array
{
    $notModified = $status === 304;
    return [
        'body' => $notModified ? null : ensure_utf8((string)$body),
        'status' => $status,
        'not_modified' => $notModified,
        'etag' => response_header_value($responseHeaders, 'ETag'),
        'last_modified' => response_header_value($responseHeaders, 'Last-Modified'),
    ];
}

function curl_fetch(string $url, bool $verifySsl, array $requestHeaders = []): array
{
    $ch = curl_init($url);
    if ($ch === false) {
        throw new RuntimeException("Failed to initialize curl for {$url}");
    }
    $responseHeaders = [];
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_FOLLOWLOCATION => true,
        CURLOPT_MAXREDIRS => 10,
        CURLOPT_CONNECTTIMEOUT => 30,
        CURLOPT_TIMEOUT => 120,
        CURLOPT_USERAGENT => TAIKEI_USER_AGENT,
        CURLOPT_ENCODING => '',
        CURLOPT_HTTPHEADER => array_merge(
            ['Accept-Language: ja,en-US;q=0.9,en;q=0.8'],
            $requestHeaders
        ),
        CURLOPT_HEADERFUNCTION => static function ($handle, string $line) use (&$responseHeaders): int {
            $length = strlen($line);
            $trimmed = rtrim($line, "\r\n");
            // redirect や proxy 応答を越えた最終 response の validator だけを使う。
            if (preg_match('/^HTTP\/\S+\s+\d{3}/i', $trimmed) === 1) {
                $responseHeaders = [$trimmed];
            } elseif ($trimmed !== '') {
                $responseHeaders[] = $trimmed;
            }
            return $length;
        },
        CURLOPT_SSL_VERIFYPEER => $verifySsl,
        CURLOPT_SSL_VERIFYHOST => $verifySsl ? 2 : 0,
    ]);
    $body = curl_exec($ch);
    $status = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    $error = curl_error($ch);
    curl_close($ch);
    return [$body, $status, $error, $responseHeaders];
}

function stream_fetch(string $url, bool $verifySsl, array $requestHeaders = []): array
{
    $headers = array_merge([
        'User-Agent: ' . TAIKEI_USER_AGENT,
        'Accept-Language: ja,en-US;q=0.9,en;q=0.8',
    ], $requestHeaders);
    $context = stream_context_create([
        'http' => [
            'method' => 'GET',
            'header' => implode("\r\n", $headers),
            'timeout' => 120,
            'follow_location' => 1,
            'ignore_errors' => true,
        ],
        'ssl' => [
            'verify_peer' => $verifySsl,
            'verify_peer_name' => $verifySsl,
        ],
    ]);

    $body = @file_get_contents($url, false, $context);
    $responseHeaders = isset($http_response_header) && is_array($http_response_header)
        ? final_http_header_lines($http_response_header)
        : [];
    $status = http_status_from_headers($responseHeaders);
    $error = '';
    if ($body === false) {
        $lastError = error_get_last();
        $error = is_array($lastError) ? (string)($lastError['message'] ?? 'unknown error') : 'unknown error';
    }

    return [$body, $status, $error, $responseHeaders];
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
    $delayUsec = fetch_retry_delay_usec($attempt, $status);
    fwrite(
        STDERR,
        sprintf(
            "Warning: transient fetch failure for %s (%s); retrying in %.2fs [attempt %d/%d].\n",
            $url,
            format_fetch_failure($status, $error),
            $delayUsec / 1000000,
            $attempt + 1,
            fetch_max_attempts($status)
        )
    );
    usleep($delayUsec);
}

// 429 は待てば通ることが多い。通常の一時エラーより粘って、取得全体が
// 途中の 1 ページで止まらないようにする。
function fetch_max_attempts(int $status): int
{
    return $status === 429 ? TAIKEI_FETCH_RATE_LIMIT_MAX_ATTEMPTS : TAIKEI_FETCH_MAX_ATTEMPTS;
}


function fetch_retry_delay_usec(int $attempt, int $status = 0): int
{
    // 429 は「速すぎる」と取得元が言っている合図なので、通信のゆらぎより
    // 長く待つ。数秒で戻ると同じ制限に当たり直し、途中で例外になって
    // 取得が丸ごと止まる（長浜市が 535/1440 で落ちた）。
    if ($status === 429) {
        rate_limited_seen(true);
        return min(TAIKEI_FETCH_RATE_LIMIT_MAX_USEC, TAIKEI_FETCH_RATE_LIMIT_BASE_USEC * max(1, $attempt));
    }
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
    $status = 0;
    foreach ($headers as $header) {
        if (!is_string($header)) {
            continue;
        }
        if (preg_match('/^HTTP\/\S+\s+(\d{3})/i', $header, $matches) === 1) {
            $status = (int)$matches[1];
        }
    }
    return $status;
}

function final_http_header_lines(array $headers): array
{
    $final = [];
    foreach ($headers as $header) {
        if (!is_string($header)) {
            continue;
        }
        $header = rtrim($header, "\r\n");
        if (preg_match('/^HTTP\/\S+\s+\d{3}/i', $header) === 1) {
            $final = [$header];
        } elseif ($header !== '') {
            $final[] = $header;
        }
    }
    return $final;
}

function response_header_value(array $headers, string $name): string
{
    foreach (array_reverse($headers) as $header) {
        if (!is_string($header)) {
            continue;
        }
        if (preg_match('/^' . preg_quote($name, '/') . '\s*:\s*(.*)$/i', $header, $matches) === 1) {
            return trim((string)$matches[1]);
        }
    }
    return '';
}

function create_dom(string $html): DOMDocument
{
    // 本文は取得時に UTF-8 へ直してある。ところがページ先頭の XML 宣言は
    // `encoding="Shift_JIS"` と名乗ったままのことがあり、libxml はそちらを
    // 信じて読む。結果、DOM が空になりリンクが 1 本も取れない。能代市は
    // これで体系目次を辿れず、例規 0 件のまま成功として記録されていた。
    // 先頭の XML 宣言は落としてから渡す。
    $html = preg_replace('/^\s*<\?xml.*?\?>/i', '', $html, 1) ?? $html;
    // meta の charset も取得元の宣言のまま残る。libxml は宣言を信じるので、
    // UTF-8 に直した本文を Shift_JIS として読んでしまう。両方 utf-8 にする。
    $html = preg_replace('/charset\s*=\s*["\']?[A-Za-z0-9_-]+/i', 'charset=utf-8', $html) ?? $html;
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
        $parts[] = encode_url_path_segment($part);
    }

    return '/' . implode('/', $parts) . $query . $fragment;
}

function encode_url_path_segment(string $segment): string
{
    $decoded = rawurldecode($segment);
    return rawurlencode($decoded);
}

function project_root(): string
{
    return dirname(__DIR__, 3);
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

function taikei_sanitize_slug_token(string $value): string
{
    $token = strtolower(trim($value));
    $token = preg_replace('/[^a-z0-9-]+/', '-', $token) ?? '';
    $token = trim($token, '-');
    return preg_replace('/-{2,}/', '-', $token) ?? '';
}

function taikei_load_municipality_master_index(): array
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

function taikei_implicit_municipality_slug(string|int $code, array $masterEntry = []): string
{
    $normalizedCode = preg_replace('/[^0-9]/', '', (string)$code) ?? '';
    if ($normalizedCode === '') {
        $normalizedCode = '00000';
    }

    $token = taikei_sanitize_slug_token((string)($masterEntry['name_romaji'] ?? ''));
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
        'entry_url' => derive_taikei_entry_url(trim((string)($urlEntry['url'] ?? '')), false),
        'data_root' => build_data_path("reiki/{$slug}"),
        'work_root' => dirname($sourceDir),
        'source_dir' => $sourceDir,
        'html_dir' => build_data_path($htmlDirRelative),
        'classification_dir' => build_data_path($classificationDirRelative),
        'image_dir' => build_data_path($imageDirRelative),
        'markdown_dir' => build_work_path($markdownDirRelative),
    ];
}

function iter_reiki_targets(?string $expectedSystem = null, bool $configuredOnly = false): array
{
    // $configuredOnly は旧 CLI 互換の引数として受けるが、現在は全国マスタをそのまま使う。
    $targets = [];
    $urlIndex = load_local_reiki_url_index();
    $masterIndex = taikei_load_municipality_master_index();

    foreach ($urlIndex as $code => $urlEntry) {
        $systemType = trim((string)($urlEntry['system_type'] ?? ''));
        if ($expectedSystem !== null && $systemType !== $expectedSystem) {
            continue;
        }

        $sourceUrl = trim((string)($urlEntry['url'] ?? ''));
        if ($sourceUrl === '') {
            continue;
        }

        // 閲覧にログインが要る取得元などは、走査しても取れないと分かっている。
        // 毎回試して失敗させると「取得エラー」として記録が積み上がる。
        $crawlStatus = trim((string)($urlEntry['crawl_status'] ?? ''));
        if ($crawlStatus !== '' && $crawlStatus !== 'enabled') {
            continue;
        }

        $slug = taikei_implicit_municipality_slug($code, $masterIndex[$code] ?? []);
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
    $nameRomaji = taikei_sanitize_slug_token((string)($target['name_romaji'] ?? ''));
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

    $masterEntry = taikei_load_municipality_master_index()[$code] ?? [];
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

// 中継ページに並ぶ「体系目次」「五十音順目次」から、走査の入口を選ぶ。
// 体系目次を優先する（分野ごとに辿れて、重複が少ない）。
function find_reiki_index_link(string $sourceUrl, int $depth = 2): string
{
    try {
        $html = fetch_url($sourceUrl);
    } catch (Throwable $exception) {
        return '';
    }
    $best = '';
    $relay = '';
    if (preg_match_all('#<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>#si', $html, $matches, PREG_SET_ORDER) < 1) {
        return '';
    }
    foreach ($matches as $match) {
        $text = trim(preg_replace('#\s+#u', ' ', strip_tags($match[2])));
        $href = resolve_url($sourceUrl, html_entity_decode($match[1], ENT_QUOTES, 'UTF-8'));
        if ($href === '') {
            continue;
        }
        // 行き先が目次そのものなら、リンクの文言は要らない。廿日市市は
        // 画像リンクで文言が空のまま、別ホストの目次を指している。
        if (preg_match('#/reiki_(?:taikei|kana)/[a-z0-9_]*default\.html?$#i', $href) === 1) {
            return $href;
        }
        if ($text === '') {
            continue;
        }
        if (str_contains($text, '体系')) {
            return $href;
        }
        if ($best === '' && (str_contains($text, '目次') || str_contains($text, '五十音'))) {
            $best = $href;
        }
        // 案内ページが目次を直接指していることがある（廿日市市は別ホストの
        // `.../reiki_taikei/taikei_default.html` へ絶対 URL で送る）。
        // 文言に「目次」が無くても、行き先の形で分かる。
        if ($best === '' && preg_match('#/reiki_(?:taikei|kana)/[a-z0-9_]*default\.html?$#i', $href) === 1) {
            $best = $href;
        }
        // 自治体サイトの案内ページが、例規サービス側の入口を指しているだけの
        // ことがある。釧路町は `reiki.html` から `g-reiki.net/.../reiki_menu.html`
        // へ送っている。1 段だけ見ていたので目次に届かなかった。
        if ($relay === '' && $href !== $sourceUrl && preg_match('#/reiki[_-]?menu\.html?$#i', $href) === 1) {
            $relay = $href;
        }
    }
    if ($best !== '') {
        return $best;
    }
    if ($relay !== '' && $depth > 1) {
        return find_reiki_index_link($relay, $depth - 1);
    }
    return '';
}


function derive_taikei_entry_url(string $sourceUrl, bool $mayFetch = true): string
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
    // 既に目次を指しているならそのまま使う。体系でも五十音でもよい。
    if (str_contains($lowerPath, '/reiki_taikei/') || str_contains($lowerPath, '/reiki_kana/')) {
        return $sourceUrl;
    }
    // 体系目次と五十音目次への入口だけを置く中継ページがある。例規への
    // リンクを持たないので、そのまま走査すると 0 件で終わる（四日市市・
    // 春日井市・京都市など）。綴りは reiki_menu / reiki-menu / reiki.html
    // と揺れるうえ目次の場所も取得元によって違うので、まずページに書かれた
    // 目次リンクを使い、読めないときだけ既定の場所へ読み替える。
    // ここへ来た時点で、登録されている URL は目次ではない。案内ページか、
    // 自治体サイトの普通のページである。**URL の形で見分けるのをやめる。**
    // 廿日市市は `/soshiki/2/15161.html` から別ホストの目次へ送っていて、
    // `reiki.html` の形を探す限り永久に見つからなかった。ページを開いて
    // 目次へのリンクを探す。
    //
    // 台帳を読むだけの場面（対象一覧の組み立て）では取りに行かない。
    // 全国分の入口ページを一度に叩くことになり 429 を招く。
    $mokuji = $mayFetch ? find_reiki_index_link($sourceUrl) : '';
    return $mokuji !== '' ? $mokuji : resolve_url($sourceUrl, 'reiki_taikei/taikei_default.html');
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

function archive_existing_file(string $path, string $reason = 'replace'): ?string
{
    $resolved = realpath($path);
    if (!is_string($resolved) || !is_file($resolved)) {
        return null;
    }
    $normalized = str_replace('\\', '/', $resolved);
    if (str_contains($normalized, '/_archive/')) {
        return null;
    }

    if (preg_match('~^(.*?/(?:reiki|gijiroku)/[^/]+)/(.*)$~', $normalized, $matches) === 1) {
        $base = $matches[1];
        $relative = $matches[2];
    } else {
        $base = dirname($normalized);
        $relative = basename($normalized);
    }

    $micro = microtime(true);
    $stamp = date('Ymd_His', (int)$micro) . sprintf('_%06d', (int)(($micro - floor($micro)) * 1000000));
    $safeReason = preg_replace('/[^A-Za-z0-9_-]+/', '_', $reason) ?: 'replace';
    $destination = $base . '/_archive/' . $stamp . '_' . $safeReason . '/' . $relative;
    ensure_dir(dirname($destination));
    if (!@copy($resolved, $destination)) {
        fwrite(STDERR, "[WARN] failed to archive old file before {$reason}: {$resolved}\n");
        return null;
    }
    @touch($destination, (int)filemtime($resolved));
    return $destination;
}

function write_text_file(string $path, string $content, bool $compress = false): string
{
    $finalPath = $compress ? gzip_path($path) : $path;
    ensure_dir(dirname($finalPath));

    $existingPath = existing_path($path);
    $archivedExisting = null;
    if ($existingPath !== null) {
        try {
            if (read_file_bytes_auto($existingPath) !== $content) {
                archive_existing_file($existingPath, 'overwrite');
                $archivedExisting = realpath($existingPath) ?: $existingPath;
            }
        } catch (Throwable) {
            archive_existing_file($existingPath, 'overwrite');
            $archivedExisting = realpath($existingPath) ?: $existingPath;
        }
    }

    if ($compress) {
        $encoded = gzencode($content, 6, ZLIB_ENCODING_GZIP);
        if (!is_string($encoded)) {
            throw new RuntimeException("Failed to gzip content for {$finalPath}");
        }
        write_file_atomically($finalPath, $encoded);

        $plainPath = logical_path($finalPath);
        if ($plainPath !== $finalPath && is_file($plainPath)) {
            $resolvedPlainPath = realpath($plainPath) ?: $plainPath;
            if ($archivedExisting !== $resolvedPlainPath) {
                archive_existing_file($plainPath, 'delete');
            }
            unlink($plainPath);
        }
    } else {
        write_file_atomically($finalPath, $content);

        $gzPath = gzip_path($finalPath);
        if ($gzPath !== $finalPath && is_file($gzPath)) {
            $resolvedGzPath = realpath($gzPath) ?: $gzPath;
            if ($archivedExisting !== $resolvedGzPath) {
                archive_existing_file($gzPath, 'delete');
            }
            unlink($gzPath);
        }
    }

    return $finalPath;
}

function sha256_string(string $content): string
{
    return hash('sha256', $content);
}

function taikei_source_changed(string $sourceHash, string $fetchedHtml, bool $force = false): bool
{
    // 空の 200 応答は「本文が削除された」と断定できない。既存 source を
    // 上書きすると次の周期でも壊れた成果物を正本として使うため、失敗に戻す。
    if (trim($fetchedHtml) === '') {
        throw new RuntimeException('Received an empty ordinance response.');
    }
    if ($force || $sourceHash === '') {
        return true;
    }
    return !hash_equals($sourceHash, sha256_string($fetchedHtml));
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

function extract_catalog_version(string $html): string
{
    $text = html_entity_decode(strip_tags($html), ENT_QUOTES | ENT_HTML5, 'UTF-8');
    $text = normalize_whitespace($text);
    if (preg_match('/内容現在\s*(?:[：:]\s*)?((?:明治|大正|昭和|平成|令和)[0-9０-９元]+年[0-9０-９]+月[0-9０-９]+日)/u', $text, $matches) === 1) {
        return trim((string)$matches[1]);
    }
    return '';
}

function catalog_version_from_pages(array $pages): string
{
    foreach ($pages as $page) {
        if (!is_array($page)) {
            continue;
        }
        $value = trim((string)($page['catalog_content_current'] ?? ''));
        if ($value !== '') {
            return $value;
        }
    }
    return '';
}

function first_manifest_catalog_version(array $records): string
{
    foreach ($records as $record) {
        if (!is_array($record)) {
            continue;
        }
        $value = trim((string)($record['catalog_content_current'] ?? ''));
        if ($value !== '') {
            return $value;
        }
    }
    return '';
}

function taikei_validation_due(?array $manifestRecord, ?int $now = null): bool
{
    $lastValidatedAt = trim((string)($manifestRecord['last_validated_at'] ?? ''));
    if ($lastValidatedAt === '') {
        return true;
    }
    $lastValidated = strtotime($lastValidatedAt);
    if ($lastValidated === false) {
        return true;
    }
    return ($now ?? time()) - $lastValidated >= TAIKEI_VALIDATION_INTERVAL_SECONDS;
}

function taikei_conditional_request_headers(?array $manifestRecord): array
{
    if ($manifestRecord === null) {
        return [];
    }
    $headers = [];
    foreach ([
        'source_etag' => 'If-None-Match',
        'source_last_modified' => 'If-Modified-Since',
    ] as $key => $name) {
        $value = trim((string)($manifestRecord[$key] ?? ''));
        // remote 由来の値を次の HTTP header へ渡すので、改行を含む値は再利用しない。
        if ($value === '' || str_contains($value, "\r") || str_contains($value, "\n")) {
            continue;
        }
        $headers[] = $name . ': ' . $value;
    }
    return $headers;
}

function finalize_taikei_manifest(
    array $manifestEntry,
    string $sourceHash,
    ?array $validationResponse,
    ?string $validatedAt
): array {
    $manifestEntry['source_sha256'] = $sourceHash;
    $manifestEntry['parser_version'] = TAIKEI_PARSER_VERSION;
    if ($validationResponse === null) {
        // source を再変換しただけでは、原典を見ていない時刻を検証済みにしない。
        return $manifestEntry;
    }

    $manifestEntry['last_validated_at'] = $validatedAt ?? gmdate('c');
    $notModified = (bool)($validationResponse['not_modified'] ?? false);
    foreach ([
        'etag' => 'source_etag',
        'last_modified' => 'source_last_modified',
    ] as $responseKey => $manifestKey) {
        $value = trim((string)($validationResponse[$responseKey] ?? ''));
        if ($value !== '') {
            $manifestEntry[$manifestKey] = $value;
        } elseif (!$notModified) {
            // 200 応答で消えた validator を送り続けず、次回は本文 hash で確かめる。
            unset($manifestEntry[$manifestKey]);
        }
    }
    return $manifestEntry;
}

function listed_metadata_changed(?array $manifestRecord, array $crawlRecord): bool
{
    if ($manifestRecord === null) {
        return true;
    }

    foreach (['title', 'date', 'number'] as $key) {
        $current = normalize_whitespace((string)($crawlRecord[$key] ?? ''));
        if ($current === '') {
            continue;
        }
        // parse 済みの題名等は個票由来なので、一覧の表記と恒常的に違うことがある。
        // 一覧同士を比較しないと、毎周期すべてを「差分あり」にしてしまう。
        $listedKey = 'listed_' . $key;
        $previousValue = array_key_exists($listedKey, $manifestRecord)
            ? $manifestRecord[$listedKey]
            : ($manifestRecord[$key] ?? '');
        $previous = normalize_whitespace((string)$previousValue);
        if ($previous === '' || $previous !== $current) {
            return true;
        }
    }

    return false;
}

function build_source_plan(
    array $records,
    string $sourceDir,
    string $htmlDir,
    string $markdownDir,
    array $previousManifestBySource
): array {
    $plans = [];
    $incompleteCount = 0;
    $listedChangeCount = 0;
    foreach ($records as $record) {
        if (!is_array($record)) {
            continue;
        }
        $sourceFileName = ordinance_file_name_from_url((string)$record['detail_url']);
        $sourcePath = $sourceDir . DIRECTORY_SEPARATOR . $sourceFileName;
        $htmlPath = $htmlDir . DIRECTORY_SEPARATOR . preg_replace('/\.html$/i', '.html', $sourceFileName);
        $markdownPath = $markdownDir . DIRECTORY_SEPARATOR . preg_replace('/\.html$/i', '.md', $sourceFileName);
        $existingSourcePath = existing_path($sourcePath);
        $storedMarkdownPath = existing_path($markdownPath);
        $previousManifest = $previousManifestBySource[$sourceFileName] ?? null;
        $hasSource = $existingSourcePath !== null && filesize($existingSourcePath) > 0;
        $sourceHash = $hasSource ? sha256_file_auto($existingSourcePath) : '';
        $manifestSourceHash = trim((string)($previousManifest['source_sha256'] ?? ''));
        $hasManifest = is_array($previousManifest);
        $needsSource = !$hasSource;
        $needsParserRefresh = $hasSource && (
            !$hasManifest
            || (int)($previousManifest['parser_version'] ?? 0) !== TAIKEI_PARSER_VERSION
        );
        $needsParse = $hasSource && (
            !is_file($htmlPath)
            || $storedMarkdownPath === null
            || !$hasManifest
            || $manifestSourceHash === ''
            || $manifestSourceHash !== $sourceHash
            || $needsParserRefresh
        );
        $isIncomplete = $needsSource
            || !is_file($htmlPath)
            || $storedMarkdownPath === null
            || !$hasManifest;
        $listedMetadataChanged = listed_metadata_changed(
            is_array($previousManifest) ? $previousManifest : null,
            $record
        );
        $validationDue = $hasSource && taikei_validation_due(
            is_array($previousManifest) ? $previousManifest : null
        );
        if ($isIncomplete) {
            $incompleteCount++;
        }
        if ($listedMetadataChanged) {
            $listedChangeCount++;
        }
        $plans[] = [
            'record' => $record,
            'source_file_name' => $sourceFileName,
            'source_path' => $sourcePath,
            'html_path' => $htmlPath,
            'markdown_path' => $markdownPath,
            'existing_source_path' => $existingSourcePath,
            'stored_markdown_path' => $storedMarkdownPath,
            'previous_manifest' => $previousManifest,
            'source_sha256' => $sourceHash,
            'needs_source' => $needsSource,
            'needs_parse' => $needsParse,
            'needs_parser_refresh' => $needsParserRefresh,
            'is_incomplete' => $isIncomplete,
            'listed_metadata_changed' => $listedMetadataChanged,
            'validation_due' => $validationDue,
        ];
    }

    return [
        'plans' => $plans,
        'incomplete_count' => $incompleteCount,
        'listed_change_count' => $listedChangeCount,
    ];
}

function prioritize_source_plans(array $plans, bool $checkUpdates): array
{
    if (!$checkUpdates) {
        return $plans;
    }
    foreach ($plans as $index => &$plan) {
        $plan['_stable_order'] = $index;
    }
    unset($plan);
    usort($plans, static function (array $left, array $right): int {
        $priority = static function (array $plan): int {
            if ((bool)($plan['listed_metadata_changed'] ?? false)) {
                return 0;
            }
            if ((bool)($plan['needs_source'] ?? false)) {
                return 1;
            }
            if ((bool)($plan['needs_parse'] ?? false)) {
                return 2;
            }
            if ((bool)($plan['validation_due'] ?? false)) {
                return 3;
            }
            return 4;
        };
        $compared = $priority($left) <=> $priority($right);
        return $compared !== 0
            ? $compared
            : (int)($left['_stable_order'] ?? 0) <=> (int)($right['_stable_order'] ?? 0);
    });
    foreach ($plans as &$plan) {
        unset($plan['_stable_order']);
    }
    unset($plan);
    return $plans;
}

function assign_work_mode(array &$plans, bool $force, bool $checkUpdates, ?bool $catalogChanged = null): array
{
    $total = count($plans);
    $incompleteCount = 0;
    foreach ($plans as $plan) {
        if ((bool)($plan['is_incomplete'] ?? false)) {
            $incompleteCount++;
        }
    }

    $resumeMode = !$force && $incompleteCount > 0;
    $workCount = 0;
    $validationCount = 0;
    $validationDueCount = 0;
    $listedChangeCount = 0;
    $parserRefreshCount = 0;
    foreach ($plans as $index => $plan) {
        $listedMetadataChanged = (bool)($plan['listed_metadata_changed'] ?? false);
        $validationDue = (bool)($plan['validation_due'] ?? false);
        $needsSource = (bool)($plan['needs_source'] ?? false);
        $needsParse = (bool)($plan['needs_parse'] ?? false);
        $shouldValidate = !$force && $checkUpdates && ($listedMetadataChanged || $validationDue);
        $shouldFetch = $force || $needsSource || $shouldValidate;
        $shouldWork = $shouldFetch || $needsParse;
        $plans[$index]['should_validate'] = $shouldValidate;
        $plans[$index]['should_fetch'] = $shouldFetch;
        $plans[$index]['should_work'] = $shouldWork;
        if ($listedMetadataChanged) {
            $listedChangeCount++;
        }
        if ($validationDue) {
            $validationDueCount++;
        }
        if ((bool)($plan['needs_parser_refresh'] ?? false)) {
            $parserRefreshCount++;
        }
        if ($shouldValidate) {
            $validationCount++;
        }
        if ($shouldWork) {
            $workCount++;
        }
    }
    $updateMode = !$force && $checkUpdates && $validationCount > 0;

    return [
        'total' => $total,
        'incomplete_count' => $incompleteCount,
        'resume_mode' => $resumeMode,
        'update_mode' => $updateMode,
        'catalog_changed' => $catalogChanged,
        'work_count' => $workCount,
        'validation_count' => $validationCount,
        'validation_due_count' => $validationDueCount,
        'listed_change_count' => $listedChangeCount,
        'parser_refresh_count' => $parserRefreshCount,
        'progress_base' => max(0, $total - $workCount),
    ];
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
    foreach (['title', 'date', 'number'] as $key) {
        $merged['listed_' . $key] = (string)($crawlRecord[$key] ?? '');
    }
    $merged['detail_url'] = (string)($crawlRecord['detail_url'] ?? $merged['detail_url'] ?? '');
    $merged['taxonomy_url'] = (string)($crawlRecord['taxonomy_url'] ?? $merged['taxonomy_url'] ?? '');
    $merged['taxonomy_path'] = (string)($crawlRecord['taxonomy_path'] ?? $merged['taxonomy_path'] ?? '');

    $existingPaths = is_array($merged['taxonomy_paths'] ?? null) ? $merged['taxonomy_paths'] : [];
    $currentPaths = is_array($crawlRecord['taxonomy_paths'] ?? null) ? $crawlRecord['taxonomy_paths'] : [];
    $merged['taxonomy_paths'] = array_values(array_unique(array_filter(array_merge($existingPaths, $currentPaths))));
    $merged['source_file'] = $sourceFile;

    return $merged;
}

// 同じディレクトリに書いてから rename で差し替える。途中で死ぬと 0 バイトの
// 正本が残り、索引がその自治体を永久に読めなくなる（浦添市の
// source_manifest.json.gz が 0 バイトのまま 3 か月あった）。
function write_file_atomically(string $finalPath, string $bytes): void
{
    $temporary = $finalPath . '.tmp';
    if (file_put_contents($temporary, $bytes) === false) {
        throw new RuntimeException("Failed to write {$temporary}");
    }
    if (!rename($temporary, $finalPath)) {
        @unlink($temporary);
        throw new RuntimeException("Failed to replace {$finalPath}");
    }
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

function ensure_dir(string $path): void
{
    if (!is_dir($path) && !mkdir($path, 0777, true) && !is_dir($path)) {
        throw new RuntimeException("Failed to create directory: {$path}");
    }
}

function throttled_sleep(): void
{
    usleep(rate_limited_seen() ? TAIKEI_SLEEP_AFTER_RATE_LIMIT_USEC : TAIKEI_SLEEP_USEC);
}


// 429 を受けたかどうかを覚えておき、以後の間隔を決めるのに使う。
//
// 自治体 1 件ごとに別プロセスなので、プロセス内だけで覚えていると
// 次の自治体がまた全速で始めて 429 を受け直す。取得元は 531 自治体で
// 1 つのホストを共有しており（www1.g-reiki.net）、上限もホスト単位なので、
// 覚える単位もホストにする。2026-09-06 の点検では 125 自治体に分かれて
// 429 が 491 件出ていた。取り直しで拾えてはいたが、毎回上限に当てていた。
function rate_limited_seen(bool $mark = false): bool
{
    static $seen = null;
    if ($mark && $seen !== true) {
        $seen = true;
        remember_host_rate_limit(current_source_host());
        return true;
    }
    if ($seen === null) {
        $seen = host_rate_limited_recently(current_source_host());
    }
    return (bool)$seen;
}


// いま取りに行っている取得元のホスト。ホスト単位で速度を覚えるために使う。
function current_source_host(string $sourceUrl = ''): string
{
    static $host = '';
    if ($sourceUrl !== '') {
        $parsed = parse_url($sourceUrl, PHP_URL_HOST);
        $host = is_string($parsed) ? strtolower($parsed) : '';
    }
    return $host;
}


function host_rate_limit_path(): string
{
    return build_work_path('reiki') . DIRECTORY_SEPARATOR . 'host_rate_limits.json';
}


// 覚えておく期間。取得元が制限を緩めることもあるので、いつかは忘れる。
const TAIKEI_RATE_LIMIT_MEMORY_SECONDS = 86400;


function host_rate_limited_recently(string $host): bool
{
    if ($host === '') {
        return false;
    }
    $raw = @file_get_contents(host_rate_limit_path());
    if (!is_string($raw) || trim($raw) === '') {
        return false;
    }
    $payload = json_decode($raw, true);
    if (!is_array($payload)) {
        return false;
    }
    $seenAt = (int)($payload[$host] ?? 0);
    return $seenAt > 0 && (time() - $seenAt) < TAIKEI_RATE_LIMIT_MEMORY_SECONDS;
}


function remember_host_rate_limit(string $host): void
{
    if ($host === '') {
        return;
    }
    $path = host_rate_limit_path();
    ensure_dir(dirname($path));
    $payload = [];
    $raw = @file_get_contents($path);
    if (is_string($raw) && trim($raw) !== '') {
        $decoded = json_decode($raw, true);
        if (is_array($decoded)) {
            $payload = $decoded;
        }
    }
    $payload[$host] = time();
    // 古い印は落とす。取得元が変わっても永久に遅いままにしない。
    foreach ($payload as $key => $seenAt) {
        if (!is_int($seenAt) || (time() - $seenAt) >= TAIKEI_RATE_LIMIT_MEMORY_SECONDS) {
            unset($payload[$key]);
        }
    }
    $encoded = json_encode($payload, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    if (!is_string($encoded)) {
        return;
    }
    // 3 並列で同時に書くので、一時ファイル経由で入れ替える。
    $temporaryPath = $path . '.' . getmypid() . '.tmp';
    if (@file_put_contents($temporaryPath, $encoded . "
") === false) {
        return;
    }
    if (!@rename($temporaryPath, $path)) {
        @unlink($temporaryPath);
    }
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
    for ($attempt = 1; $attempt <= 3; $attempt++) {
        if (@rename($tempPath, $path)) {
            return;
        }
        if (is_file($path) && @unlink($path) && @rename($tempPath, $path)) {
            return;
        }
        usleep(50000 * $attempt);
    }
    if (is_file($tempPath)) {
        @unlink($tempPath);
    }
}

function h(string $value): string
{
    return htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}
