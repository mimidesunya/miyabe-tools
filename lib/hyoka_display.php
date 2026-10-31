<?php
declare(strict_types=1);

// 事務事業評価の画面用の控え（tools/hyoka/build_display_data.py が data/hyoka に書く）を読む。
// 取得の保存先と抽出の出力先は画面の容器から見えないので、控えだけを読む。
// 控えが無ければ null や空を返し、画面はその欄を出さない。

function miyabe_hyoka_display_dir(): string
{
    $configured = trim((string)(getenv('MIYABE_HYOKA_DISPLAY_DIR') ?: ''));
    return $configured !== '' ? rtrim($configured, '/\\') : dirname(__DIR__) . DIRECTORY_SEPARATOR . 'data' . DIRECTORY_SEPARATOR . 'hyoka';
}

function miyabe_hyoka_display_read_json(string $path): ?array
{
    if (!is_file($path)) {
        return null;
    }
    $raw = @file_get_contents($path);
    if ($raw === false) {
        return null;
    }
    $data = json_decode($raw, true);
    return is_array($data) ? $data : null;
}

function miyabe_hyoka_display_status(): ?array
{
    static $status = false;
    if ($status === false) {
        $status = miyabe_hyoka_display_read_json(miyabe_hyoka_display_dir() . DIRECTORY_SEPARATOR . 'status.json');
        if ($status !== null && !is_array($status['municipalities'] ?? null)) {
            $status = null;
        }
    }
    return $status;
}

function miyabe_hyoka_display_status_by_slug(): array
{
    $rows = [];
    foreach ((miyabe_hyoka_display_status()['municipalities'] ?? []) as $row) {
        if (is_array($row) && is_string($row['slug'] ?? null)) {
            $rows[$row['slug']] = $row;
        }
    }
    return $rows;
}

// 詳細画面の ID「hyoka:<slug>:<doc_id>」又は「…:p<頁>」から、控えのファイルを決める部分だけを取る。
function miyabe_hyoka_display_parse_id(string $id): ?array
{
    if (!preg_match('/^hyoka:([0-9]{5}-[a-z0-9-]+):([0-9a-f]{16})(?::p([0-9]{1,5}))?$/', $id, $match)) {
        return null;
    }
    return ['slug' => $match[1], 'doc_id' => $match[2], 'page' => isset($match[3]) ? (int)$match[3] : 0];
}

// これより事業が多い文書は、一覧の見出しだけを先に出し、中身は開いたときに読む（島根県は 1 文書 743 件）。
const MIYABE_HYOKA_INLINE_RECORDS = 60;

// 文書から取り出した事業の記録と原典の URL。頁で分けた詳細なら、その頁の記録だけ。
function miyabe_hyoka_display_document(string $id): array
{
    $empty = ['records' => [], 'source_url' => ''];
    $parsed = miyabe_hyoka_display_parse_id($id);
    if ($parsed === null) {
        return $empty;
    }
    $data = miyabe_hyoka_display_read_json(miyabe_hyoka_display_dir() . DIRECTORY_SEPARATOR . 'records'
        . DIRECTORY_SEPARATOR . $parsed['slug'] . DIRECTORY_SEPARATOR . $parsed['doc_id'] . '.json');
    if ($data === null || ($data['doc_id'] ?? '') !== $parsed['doc_id'] || !is_array($data['records'] ?? null)) {
        return $empty;
    }
    $records = array_values(array_filter($data['records'], 'is_array'));
    if ($parsed['page'] > 0) {
        $records = array_values(array_filter($records, static fn(array $record): bool => (int)($record['page'] ?? 0) === $parsed['page']));
    }
    return ['records' => $records, 'source_url' => trim((string)($data['source_url'] ?? ''))];
}

function miyabe_hyoka_display_records(string $id): array
{
    return miyabe_hyoka_display_document($id)['records'];
}

function miyabe_hyoka_display_find_record(string $id, string $recordId): ?array
{
    $document = miyabe_hyoka_display_document($id);
    foreach ($document['records'] as $record) {
        if (($record['id'] ?? '') === $recordId) {
            return ['record' => $record, 'source_url' => $document['source_url']];
        }
    }
    return null;
}

// 検索結果の一覧に添える事業の数。全文の頁 1 つに数百件ある文書もあるので、検索語を含むものから絞る。
const MIYABE_HYOKA_SEARCH_RECORDS = 5;

// 一覧や MCP に出す、事業 1 件の短い形。値は控えの専用欄の原文のまま（切らない）。
function miyabe_hyoka_display_record_summary(array $record): array
{
    return [
        'id' => (string)($record['id'] ?? ''),
        'project_name' => miyabe_hyoka_display_field($record, 'project_name'),
        'department' => miyabe_hyoka_display_field($record, 'department'),
        'year' => miyabe_hyoka_display_field($record, 'target_year') ?: miyabe_hyoka_display_field($record, 'evaluation_year'),
        'evaluation_rating' => miyabe_hyoka_display_field($record, 'evaluation_rating'),
        'future_direction' => miyabe_hyoka_display_field($record, 'future_direction'),
        'location' => miyabe_hyoka_display_location_label($record),
    ];
}

/**
 * 検索結果 1 件（文書、または頁）に載っている事業を、検索語を含むものから最大 $limit 件。
 * 語は欄の文字（標準の欄とそのほかの欄）に含まれるかで数え、事業名に含むものを先にする。
 * 返すのは ['records' => [...], 'total' => 事業の数]。控えの無い文書は空。
 */
function miyabe_hyoka_display_search_records(string $id, array $terms, int $limit = MIYABE_HYOKA_SEARCH_RECORDS): array
{
    $records = miyabe_hyoka_display_records($id);
    if ($records === []) {
        return ['records' => [], 'total' => 0];
    }
    $terms = array_values(array_filter(array_map(
        static fn($term): string => mb_strtolower(trim((string)$term), 'UTF-8'),
        $terms
    ), static fn(string $term): bool => $term !== ''));
    $scored = [];
    foreach ($records as $index => $record) {
        $name = mb_strtolower(miyabe_hyoka_display_field($record, 'project_name'), 'UTF-8');
        $texts = [];
        foreach (array_merge($record['fields'] ?? [], $record['raw_fields'] ?? []) as $field) {
            if (is_array($field)) {
                $texts[] = (string)($field['text'] ?? '');
            }
        }
        $haystack = mb_strtolower(implode("\n", $texts), 'UTF-8');
        $score = 0;
        foreach ($terms as $term) {
            if ($name !== '' && str_contains($name, $term)) {
                $score += 2;
            } elseif (str_contains($haystack, $term)) {
                $score += 1;
            }
        }
        $scored[] = ['score' => $score, 'index' => $index, 'record' => $record];
    }
    // 語を含むものを先に、同じなら原典の順。
    usort($scored, static fn(array $a, array $b): int => [$b['score'], $a['index']] <=> [$a['score'], $b['index']]);
    $picked = [];
    foreach (array_slice($scored, 0, max(0, $limit)) as $row) {
        $picked[] = miyabe_hyoka_display_record_summary($row['record']) + ['matched' => $row['score'] > 0];
    }
    return ['records' => $picked, 'total' => count($records)];
}

function miyabe_hyoka_display_h(string $value): string
{
    return htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

// 事業 1 件の中身（標準の欄・そのほかの欄・原典への飛び先）。詳細画面と、開いたときに読む API の両方で使う。
function miyabe_hyoka_display_record_body_html(array $record, string $sourceUrl): string
{
    $html = '<dl class="hyoka-record-fields">';
    foreach (($record['fields'] ?? []) as $field) {
        if (!is_array($field)) {
            continue;
        }
        $name = (string)($field['name'] ?? '');
        $label = trim((string)($field['label'] ?? ''));
        $html .= '<div><dt>' . miyabe_hyoka_display_h($name);
        if ($label !== '' && $label !== $name) {
            $html .= '<small>原典の欄名: ' . miyabe_hyoka_display_h($label) . '</small>';
        }
        $html .= '</dt><dd>' . miyabe_hyoka_display_h((string)($field['text'] ?? '')) . '</dd></div>';
    }
    $html .= '</dl>';
    $raw = array_values(array_filter($record['raw_fields'] ?? [], 'is_array'));
    if ($raw !== []) {
        $html .= '<h3 class="hyoka-record-subhead">そのほかの欄（原典の欄名のまま）</h3><dl class="hyoka-record-fields is-raw">';
        foreach ($raw as $field) {
            $html .= '<div><dt>' . miyabe_hyoka_display_h((string)($field['label'] ?? '')) . '</dt><dd>'
                . miyabe_hyoka_display_h((string)($field['text'] ?? '')) . '</dd></div>';
        }
        $html .= '</dl>';
    }
    $link = miyabe_hyoka_display_page_link($sourceUrl, $record);
    if ($link !== '') {
        $location = miyabe_hyoka_display_location_label($record);
        $html .= '<p class="hyoka-record-source"><a href="' . miyabe_hyoka_display_h($link) . '" target="_blank" rel="noopener noreferrer">'
            . miyabe_hyoka_display_h(($location !== '' ? $location : '原典') . 'を開く') . '</a></p>';
    }
    return $html;
}

function miyabe_hyoka_display_field(array $record, string $key): string
{
    foreach (($record['fields'] ?? []) as $field) {
        if (is_array($field) && ($field['key'] ?? '') === $key) {
            return trim((string)($field['text'] ?? ''));
        }
    }
    return '';
}

// 一覧の札に出す短い形。長い方向性の文などを切る。
function miyabe_hyoka_display_short(string $text, int $chars = 30): string
{
    $text = preg_replace('/\s+/u', ' ', trim($text)) ?? $text;
    $parts = preg_split('//u', $text, -1, PREG_SPLIT_NO_EMPTY) ?: [];
    return count($parts) > $chars ? implode('', array_slice($parts, 0, $chars)) . '…' : $text;
}

// 原典の頁への飛び先。PDF の頁は 1 始まりの物理頁。
function miyabe_hyoka_display_page_link(string $sourceUrl, array $record): string
{
    $page = (int)($record['page'] ?? 0);
    if ($sourceUrl === '' || $page <= 0) {
        return $sourceUrl;
    }
    return preg_replace('/#.*$/', '', $sourceUrl) . '#page=' . $page;
}

function miyabe_hyoka_display_location_label(array $record): string
{
    $page = (int)($record['page'] ?? 0);
    if ($page > 0) {
        return '原典 ' . $page . '頁';
    }
    $sheet = trim((string)($record['sheet'] ?? ''));
    $cells = trim((string)($record['cells'] ?? ''));
    return $sheet !== '' ? '原典 ' . $sheet . ($cells !== '' ? ' ' . $cells : '') : '';
}

function miyabe_hyoka_display_fetch_label(string $status): string
{
    return match ($status) {
        'ok' => '取得済み',
        'partial' => '一部取得',
        'entry_unreachable' => '入口に届かない',
        'error' => '取得で失敗',
        default => '未取得',
    };
}
