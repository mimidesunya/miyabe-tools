<?php
declare(strict_types=1);

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'municipalities.php';
require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'japanese_search.php';

// 会議録ページで使う文字列整形と本文解析の共通関数をまとめる。

function h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function normalize_space(string $value): string
{
    $normalized = preg_replace('/[ \t　]+/u', ' ', trim($value));
    return $normalized === null ? trim($value) : $normalized;
}

function render_excerpt(string $value): string
{
    return nl2br(str_replace(['[[[', ']]]'], ['<mark>', '</mark>'], h($value)));
}

function gijiroku_index_summary_cache_path(string $indexJsonPath): string
{
    return data_path('background_tasks/gijiroku_index_summary/' . sha1($indexJsonPath) . '.json');
}

function gijiroku_index_summary_candidates(string $path): array
{
    $normalized = trim($path);
    if ($normalized === '') {
        return [];
    }

    if (str_ends_with(strtolower($normalized), '.gz')) {
        return [$normalized, substr($normalized, 0, -3)];
    }

    return [$normalized, $normalized . '.gz'];
}

function gijiroku_index_summary_from_json(string $indexJsonPath): ?array
{
    static $cache = [];

    $candidates = gijiroku_index_summary_candidates($indexJsonPath);
    if ($candidates === []) {
        return null;
    }

    $existingPath = '';
    foreach ($candidates as $candidate) {
        if (is_file($candidate)) {
            $existingPath = $candidate;
            break;
        }
    }
    if ($existingPath === '') {
        return null;
    }

    $cacheKey = $existingPath . '|' . (string)@filemtime($existingPath);
    if (isset($cache[$cacheKey])) {
        return $cache[$cacheKey];
    }

    $cachePath = gijiroku_index_summary_cache_path($existingPath);
    if (json_cache_file_is_fresh($cachePath, 3600, [$existingPath])) {
        $cached = read_json_cache_file($cachePath);
        if (is_array($cached)) {
            return $cache[$cacheKey] = $cached;
        }
    }

    $raw = @file_get_contents($existingPath);
    if (!is_string($raw)) {
        return null;
    }
    if (str_ends_with(strtolower($existingPath), '.gz')) {
        $decoded = @gzdecode($raw);
        if (!is_string($decoded)) {
            return null;
        }
        $raw = $decoded;
    }

    $loaded = json_decode($raw, true);
    if (!is_array($loaded)) {
        return null;
    }

    $documents = 0;
    $yearCounts = [];
    $yearOrder = [];
    foreach ($loaded as $row) {
        if (!is_array($row)) {
            continue;
        }

        $documents++;
        $yearLabel = normalize_space((string)($row['year_label'] ?? ''));
        if ($yearLabel === '') {
            continue;
        }

        if (!isset($yearCounts[$yearLabel])) {
            $yearCounts[$yearLabel] = [
                'year_label' => $yearLabel,
                'count' => 0,
                'last_date' => null,
            ];
            $yearOrder[] = $yearLabel;
        }
        $yearCounts[$yearLabel]['count']++;
    }

    $yearOptions = [];
    foreach ($yearOrder as $yearLabel) {
        $yearOptions[] = $yearCounts[$yearLabel];
    }

    $summary = [
        'stats' => [
            'documents' => $documents,
            'years' => count($yearOptions),
            'first_date' => null,
            'last_date' => null,
        ],
        'year_options' => $yearOptions,
    ];
    write_json_cache_file($cachePath, $summary);
    return $cache[$cacheKey] = $summary;
}

function gijiroku_index_boundary_date(PDO $pdo, string $direction): ?string
{
    $direction = strtoupper(trim($direction));
    if (!in_array($direction, ['ASC', 'DESC'], true)) {
        $direction = 'DESC';
    }

    $stmt = $pdo->query(
        "SELECT held_on
           FROM minutes
          WHERE doc_type = 'minutes'
            AND held_on IS NOT NULL
       ORDER BY held_on {$direction}, id {$direction}
          LIMIT 1"
    );
    $row = $stmt->fetch();
    $value = is_array($row) ? trim((string)($row['held_on'] ?? '')) : '';
    return $value !== '' ? $value : null;
}

// FTS5 の演算子は残しつつ、ハイライト用の語だけを抜き出す。
function extract_query_terms(string $query): array
{
    $prepared = japanese_search_prepare_query($query);
    $terms = $prepared['highlight_terms'] ?? [];
    return is_array($terms) ? array_values(array_unique(array_map('strval', $terms))) : [];
}

function ordered_terms(array $terms): array
{
    $terms = array_values(array_filter(array_map('strval', $terms), static fn (string $term): bool => $term !== ''));
    usort($terms, static fn (string $a, string $b): int => mb_strlen($b, 'UTF-8') <=> mb_strlen($a, 'UTF-8'));
    return $terms;
}

// 長い語を先に並べ、短い語で先に分割されて強調が崩れるのを防ぐ。
function render_inline_highlighted(string $text, array $terms): string
{
    if ($text === '') {
        return '';
    }

    $ordered = ordered_terms($terms);
    if ($ordered === []) {
        return h($text);
    }

    $pattern = '/(' . implode('|', array_map(static fn (string $term): string => preg_quote($term, '/'), $ordered)) . ')/iu';
    $parts = preg_split($pattern, $text, -1, PREG_SPLIT_DELIM_CAPTURE);
    if ($parts === false) {
        return h($text);
    }

    $termMap = [];
    foreach ($ordered as $term) {
        $termMap[mb_strtolower($term, 'UTF-8')] = true;
    }

    $html = '';
    foreach ($parts as $part) {
        if ($part === '') {
            continue;
        }

        $matched = isset($termMap[mb_strtolower($part, 'UTF-8')]);
        $html .= $matched ? '<mark>' . h($part) . '</mark>' : h($part);
    }

    return $html;
}

function render_paragraphs(string $text, array $terms): string
{
    $parts = preg_split('/\n{2,}/u', trim($text));
    if ($parts === false) {
        return '<p>' . render_inline_highlighted($text, $terms) . '</p>';
    }

    $html = '';
    foreach ($parts as $part) {
        $part = trim($part);
        if ($part === '') {
            continue;
        }

        $lines = preg_split('/\n/u', $part);
        if ($lines === false) {
            $lines = [$part];
        }

        $rendered = [];
        foreach ($lines as $line) {
            $line = trim($line);
            if ($line === '') {
                continue;
            }
            $rendered[] = render_inline_highlighted($line, $terms);
        }

        if ($rendered !== []) {
            $html .= '<p>' . implode('<br>', $rendered) . '</p>';
        }
    }

    return $html;
}

function is_separator_line(string $line): bool
{
    return preg_match('/^[\s　]*[─-]{8,}[\s　]*$/u', $line) === 1;
}

function is_stage_note_line(string $line): bool
{
    return preg_match('/^[\s　]*（.+）[\s　]*$/u', $line) === 1;
}

function is_speaker_line(string $line): bool
{
    return preg_match('/^[○◎◆◇△▲▽▼□■●〇◯]/u', $line) === 1;
}

// PDF 由来の折り返しを段落単位へ戻し、検索結果の本文を読みやすく整える。
function merge_wrapped_lines(array $lines): string
{
    $paragraphs = [];
    $current = '';
    foreach ($lines as $line) {
        $clean = normalize_space((string)$line);
        if ($clean === '') {
            if ($current !== '') {
                $paragraphs[] = $current;
                $current = '';
            }
            continue;
        }

        if ($current === '') {
            $current = $clean;
            continue;
        }

        $space = preg_match('/[A-Za-z0-9]$/u', $current) === 1 && preg_match('/^[A-Za-z0-9]/u', $clean) === 1 ? ' ' : '';
        $current .= $space . $clean;
    }

    if ($current !== '') {
        $paragraphs[] = $current;
    }

    return implode("\n\n", $paragraphs);
}

// 発言者行は自治体ごとに揺れるため、記号・話者名・役職・本文へ緩く分解する。
function parse_speaker_line(string $line): array
{
    $mark = mb_substr($line, 0, 1, 'UTF-8');
    $rest = trim(mb_substr($line, 1, null, 'UTF-8'));
    $parts = preg_split('/[　 ]+/u', $rest, 3, PREG_SPLIT_NO_EMPTY);
    if ($parts === false) {
        $parts = [];
    }

    return [
        'mark' => $mark,
        'speaker' => $parts[0] ?? '',
        'role' => count($parts) >= 3 ? ($parts[1] ?? '') : '',
        'body' => count($parts) >= 3 ? ($parts[2] ?? '') : ($parts[1] ?? ''),
    ];
}

// 冒頭の開催情報・出席者・日程を本文ブロックとは別に抜き出す。
function parse_document_preamble(array $lines): array
{
    $header = [];
    $meta = [];
    $agenda = [];
    $currentMeta = null;
    $inAgenda = false;

    foreach ($lines as $line) {
        $raw = rtrim((string)$line);
        $trimmed = trim($raw);
        if ($trimmed === '') {
            $currentMeta = null;
            continue;
        }
        if (is_separator_line($trimmed)) {
            continue;
        }

        $normalized = normalize_space($raw);
        if ($normalized === '') {
            continue;
        }

        if ($inAgenda || preg_match('/^日程/u', str_replace(' ', '', $normalized)) === 1) {
            $inAgenda = true;
            $agenda[] = $normalized;
            $currentMeta = null;
            continue;
        }

        if (preg_match('/^([^：]{1,18})：\s*(.+)$/u', $normalized, $matches) === 1) {
            $meta[] = ['label' => trim($matches[1]), 'value' => trim($matches[2])];
            $currentMeta = array_key_last($meta);
            continue;
        }

        if ($currentMeta !== null && preg_match('/^[\s　]/u', $raw) === 1) {
            $meta[$currentMeta]['value'] .= ' ' . $normalized;
            continue;
        }

        if (preg_match('/(開会|閉会|再開|休憩)/u', $normalized) === 1 && preg_match('/(令和|平成|昭和|\d{4}年)/u', $normalized) === 1) {
            $meta[] = ['label' => '日時', 'value' => $normalized];
            $currentMeta = array_key_last($meta);
            continue;
        }

        if (count($header) < 3) {
            $header[] = $normalized;
            continue;
        }

        $meta[] = ['label' => '記録', 'value' => $normalized];
        $currentMeta = array_key_last($meta);
    }

    return ['header' => $header, 'meta' => $meta, 'agenda' => $agenda];
}

// 生テキストの会議録を、見出し・発言・注記のブロックへ分解する。
function parse_minutes_document(string $content): array
{
    $lines = preg_split('/\r\n|\r|\n/u', $content);
    if ($lines === false) {
        $lines = [$content];
    }

    $firstSpeech = null;
    foreach ($lines as $i => $line) {
        $trimmed = trim((string)$line);
        if ($trimmed !== '' && is_speaker_line($trimmed)) {
            $firstSpeech = $i;
            break;
        }
    }

    $preamble = parse_document_preamble($firstSpeech === null ? $lines : array_slice($lines, 0, $firstSpeech));
    $bodyLines = $firstSpeech === null ? [] : array_slice($lines, $firstSpeech);
    $blocks = [];
    $counter = 0;
    $current = null;

    $anchor = static function () use (&$counter): string {
        $counter++;
        return 'block-' . $counter;
    };

    $flush = static function () use (&$current, &$blocks, $anchor): void {
        if ($current === null) {
            return;
        }

        $body = merge_wrapped_lines($current['lines']);
        if ($body !== '') {
            $blocks[] = [
                'type' => 'speech',
                'anchor' => $anchor(),
                'mark' => $current['mark'],
                'speaker' => $current['speaker'],
                'role' => $current['role'],
                'body' => $body,
            ];
        }
        $current = null;
    };

    foreach ($bodyLines as $line) {
        $trimmed = trim((string)$line);
        if ($trimmed === '') {
            if ($current !== null) {
                $current['lines'][] = '';
            }
            continue;
        }
        if (is_separator_line($trimmed)) {
            $flush();
            $blocks[] = ['type' => 'divider'];
            continue;
        }
        if (is_stage_note_line($trimmed)) {
            $flush();
            $blocks[] = ['type' => 'note', 'kind' => 'stage', 'anchor' => $anchor(), 'body' => normalize_space($trimmed)];
            continue;
        }
        if (is_speaker_line($trimmed)) {
            $flush();
            $parsed = parse_speaker_line($trimmed);
            $current = ['mark' => $parsed['mark'], 'speaker' => $parsed['speaker'], 'role' => $parsed['role'], 'lines' => []];
            if ($parsed['body'] !== '') {
                $current['lines'][] = $parsed['body'];
            }
            continue;
        }
        if ($current !== null) {
            $current['lines'][] = (string)$line;
            continue;
        }

        $blocks[] = ['type' => 'note', 'kind' => 'note', 'anchor' => $anchor(), 'body' => merge_wrapped_lines([(string)$line])];
    }
    $flush();

    return ['preamble' => $preamble, 'blocks' => $blocks];
}

function block_plain_text(array $block): string
{
    if (($block['type'] ?? '') === 'speech') {
        return trim(implode(' ', array_filter([(string)($block['speaker'] ?? ''), (string)($block['role'] ?? ''), (string)($block['body'] ?? '')])));
    }
    return (string)($block['body'] ?? '');
}

function block_match_count(array $block, array $terms): int
{
    $text = block_plain_text($block);
    if ($text === '' || $terms === []) {
        return 0;
    }

    $count = 0;
    foreach (ordered_terms($terms) as $term) {
        $matches = [];
        $found = preg_match_all('/' . preg_quote($term, '/') . '/iu', $text, $matches);
        if ($found !== false) {
            $count += $found;
        }
    }

    return $count;
}

function truncate_text(string $text, int $width = 88): string
{
    $text = normalize_space($text);
    return function_exists('mb_strimwidth') ? mb_strimwidth($text, 0, $width, '…', 'UTF-8') : $text;
}

function block_label(array $block): string
{
    if (($block['type'] ?? '') === 'speech') {
        return trim(implode(' ', array_filter([(string)($block['speaker'] ?? ''), (string)($block['role'] ?? '')]))) ?: '発言';
    }
    return ($block['kind'] ?? '') === 'stage' ? '進行メモ' : '記録メモ';
}

// 検索語に一致した発言ブロックへ件数とプレビューを付ける。
function annotate_document_matches(array $document, array $terms): array
{
    $blocks = [];
    $matches = [];
    foreach ($document['blocks'] as $block) {
        $block['match_count'] = block_match_count($block, $terms);
        if (($block['match_count'] ?? 0) > 0 && !empty($block['anchor'])) {
            $matches[] = [
                'anchor' => (string)$block['anchor'],
                'label' => block_label($block),
                'preview' => truncate_text(block_plain_text($block)),
                'count' => (int)$block['match_count'],
            ];
        }
        $blocks[] = $block;
    }
    $document['blocks'] = $blocks;
    $document['matches'] = $matches;
    return $document;
}

function query_with(array $patch): string
{
    $params = $_GET;
    foreach ($patch as $key => $val) {
        if ($val === null || $val === '') {
            unset($params[$key]);
        } else {
            $params[$key] = (string)$val;
        }
    }
    return '?' . http_build_query($params);
}
