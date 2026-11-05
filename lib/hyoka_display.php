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

// 詳細画面の ID「hyoka:<slug>:<doc_id>」、「…:p<頁>」、事業のシートが何頁にもわたれば「…:p<頁>-<頁>」から、
// 控えのファイルを決める部分だけを取る。page_end は頁の範囲の最後（1 頁なら page と同じ、文書全体なら 0）。
function miyabe_hyoka_display_parse_id(string $id): ?array
{
    if (!preg_match('/^hyoka:([0-9]{5}-[a-z0-9-]+):([0-9a-f]{16})(?::p([0-9]{1,5})(?:-([0-9]{1,5}))?)?$/', $id, $match)) {
        return null;
    }
    $page = isset($match[3]) ? (int)$match[3] : 0;
    $end = isset($match[4]) && $match[4] !== '' ? (int)$match[4] : $page;
    if ($end < $page) {
        return null;
    }
    return ['slug' => $match[1], 'doc_id' => $match[2], 'page' => $page, 'page_end' => $end];
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
        // その頁（範囲）から始まる事業。何頁にもわたる事業は最初の頁で数える。
        $records = array_values(array_filter($records, static fn(array $record): bool
            => (int)($record['page'] ?? 0) >= $parsed['page'] && (int)($record['page'] ?? 0) <= $parsed['page_end']));
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

function miyabe_hyoka_display_sheet_path(string $id): ?string
{
    $parsed = miyabe_hyoka_display_parse_id($id);
    if ($parsed === null) {
        return null;
    }
    $path = miyabe_hyoka_display_dir() . DIRECTORY_SEPARATOR . 'sheets' . DIRECTORY_SEPARATOR . $parsed['slug']
        . DIRECTORY_SEPARATOR . $parsed['doc_id'] . '.json.gz';
    return is_file($path) ? $path : null;
}

// シートの全部（頁の表の構造）から 1 頁を読む。1 行目が目次で、頁ごとに 1 行なので、大きな文書でも開いた頁までしか解かない。
// 返すのは ['page' => 頁の構造, 'pages' => 頁番号の一覧]。読めなければ null。
function miyabe_hyoka_display_sheet_page(string $id, int $pageNumber = 0): ?array
{
    $path = miyabe_hyoka_display_sheet_path($id);
    $parsed = miyabe_hyoka_display_parse_id($id);
    if ($path === null || $parsed === null) {
        return null;
    }
    $stream = @gzopen($path, 'rb');
    if ($stream === false) {
        return null;
    }
    $head = json_decode((string)gzgets($stream), true);
    $pages = is_array($head['pages'] ?? null) ? array_values(array_map('intval', $head['pages'])) : [];
    if (($head['doc_id'] ?? '') !== $parsed['doc_id'] || $pages === []) {
        gzclose($stream);
        return null;
    }
    $wanted = $pageNumber > 0 ? $pageNumber : ($parsed['page'] > 0 ? $parsed['page'] : $pages[0]);
    $found = null;
    while (!gzeof($stream)) {
        $line = gzgets($stream);
        if ($line === false) {
            break;
        }
        // 頁の番号だけ先に見て、要らない行は解かない。
        if (!preg_match('/^\{"page":(\d+)/', $line, $match) || (int)$match[1] !== $wanted) {
            continue;
        }
        $decoded = json_decode($line, true);
        $found = is_array($decoded) ? $decoded : null;
        break;
    }
    gzclose($stream);
    return $found === null ? null : ['page' => $found, 'pages' => $pages];
}

// 升目を表の番地に置く。升目どうしが重なることがある（川崎市の「事業の概要」の帯は、表全体を囲む枠の大きさで
// 読み取られている）ので、小さい升目から置き、大きい升目は空いている所まで縮める（左上から右へ、次に下へ）。
// 空いた番地は空の升目で埋める。返すのは行ごとの [列 => 升目]（升目は r, c, rs, cs, text）。
function miyabe_hyoka_display_sheet_grid(array $cells): array
{
    $items = [];
    foreach ($cells as $cell) {
        if (is_array($cell)) {
            $r = max(0, (int)($cell['r'] ?? 0));
            $c = max(0, (int)($cell['c'] ?? 0));
            $items[] = ['r' => $r, 'c' => $c, 'rs' => max(1, (int)($cell['rs'] ?? 1)), 'cs' => max(1, (int)($cell['cs'] ?? 1)),
                'text' => (string)($cell['text'] ?? '')];
        }
    }
    usort($items, static fn(array $a, array $b): int => [$a['rs'] * $a['cs'], $a['r'], $a['c']] <=> [$b['rs'] * $b['cs'], $b['r'], $b['c']]);
    $used = [];
    $rows = [];
    $lost = [];
    $height = 0;
    $width = 0;
    foreach ($items as $cell) {
        $anchor = null;
        for ($y = $cell['r']; $y < $cell['r'] + $cell['rs'] && $anchor === null; $y++) {
            for ($x = $cell['c']; $x < $cell['c'] + $cell['cs']; $x++) {
                if (!isset($used[$y][$x])) {
                    $anchor = [$y, $x];
                    break;
                }
            }
        }
        if ($anchor === null) {
            // 小さい升目にすべて覆われた升目は、文字があれば最後の行に足す（落とさない）。
            if (trim($cell['text']) !== '') {
                $lost[] = $cell['text'];
            }
            continue;
        }
        [$y, $x] = $anchor;
        $w = 1;
        while ($x + $w < $cell['c'] + $cell['cs'] && !isset($used[$y][$x + $w])) {
            $w++;
        }
        $h = 1;
        while ($y + $h < $cell['r'] + $cell['rs']) {
            for ($i = $x; $i < $x + $w; $i++) {
                if (isset($used[$y + $h][$i])) {
                    break 2;
                }
            }
            $h++;
        }
        for ($i = $y; $i < $y + $h; $i++) {
            for ($j = $x; $j < $x + $w; $j++) {
                $used[$i][$j] = true;
            }
        }
        $rows[$y][$x] = ['r' => $y, 'c' => $x, 'rs' => $h, 'cs' => $w, 'text' => $cell['text']];
        $height = max($height, $y + $h);
        $width = max($width, $x + $w);
    }
    for ($y = 0; $y < $height; $y++) {
        for ($x = 0; $x < $width; $x++) {
            if (!isset($used[$y][$x])) {
                $rows[$y][$x] = ['r' => $y, 'c' => $x, 'rs' => 1, 'cs' => 1, 'text' => ''];
            }
        }
        // 上の升目に覆われて自分の升目の無い行も、行の数を合わせるため空の行にする。
        $rows[$y] ??= [];
        ksort($rows[$y]);
    }
    ksort($rows);
    if ($lost !== []) {
        $rows[$height] = [0 => ['r' => $height, 'c' => 0, 'rs' => 1, 'cs' => max(1, $width), 'text' => implode(' ', $lost)]];
    }
    return $rows;
}

function miyabe_hyoka_display_sheet_table_html(array $table): string
{
    $rows = miyabe_hyoka_display_sheet_grid(is_array($table['cells'] ?? null) ? $table['cells'] : []);
    $html = '<div class="hyoka-sheet-scroll"><table class="hyoka-sheet-table">';
    $widths = is_array($table['widths'] ?? null) ? $table['widths'] : [];
    if ($widths !== []) {
        $html .= '<colgroup>';
        foreach ($widths as $width) {
            $html .= '<col style="width:' . number_format((float)$width, 1, '.', '') . '%">';
        }
        $html .= '</colgroup>';
    }
    $html .= '<tbody>';
    foreach ($rows as $cells) {
        $html .= '<tr>';
        foreach ($cells as $cell) {
            $rowSpan = max(1, (int)($cell['rs'] ?? 1));
            $colSpan = max(1, (int)($cell['cs'] ?? 1));
            $html .= '<td' . ($rowSpan > 1 ? ' rowspan="' . $rowSpan . '"' : '') . ($colSpan > 1 ? ' colspan="' . $colSpan . '"' : '') . '>'
                . htmlspecialchars((string)($cell['text'] ?? ''), ENT_QUOTES, 'UTF-8') . '</td>';
        }
        $html .= '</tr>';
    }
    return $html . '</tbody></table></div>';
}

// 表の罫線を読み取れない頁は、文字を原典の位置に並べる。位置と大きさは頁に対する %。
function miyabe_hyoka_display_sheet_canvas_html(array $page): string
{
    $width = max(1.0, (float)($page['width'] ?? 595));
    $height = max(1.0, (float)($page['height'] ?? 842));
    $html = '<div class="hyoka-sheet-scroll"><div class="hyoka-sheet-canvas" style="aspect-ratio:'
        . number_format($width, 1, '.', '') . '/' . number_format($height, 1, '.', '') . '">';
    foreach ($page['canvas'] as $item) {
        if (!is_array($item)) {
            continue;
        }
        $style = 'left:' . number_format((float)($item['l'] ?? 0), 2, '.', '') . '%;top:' . number_format((float)($item['t'] ?? 0), 2, '.', '')
            . '%;font-size:' . number_format((float)($item['s'] ?? 1), 2, '.', '') . 'cqw';
        $html .= '<span' . (!empty($item['v']) ? ' class="is-vertical"' : '') . ' style="' . $style . '">'
            . htmlspecialchars((string)($item['x'] ?? ''), ENT_QUOTES, 'UTF-8') . '</span>';
    }
    return $html . '</div></div>';
}

// 1 頁を、原典の表の形のまま HTML にする。文字はすべて逃がす。
function miyabe_hyoka_display_sheet_page_html(array $page): string
{
    if (!empty($page['image_only'])) {
        return '<p class="detail-note">この頁には文字の層がありません。原典の PDF をご覧ください。</p>';
    }
    if (!empty($page['garbled'])) {
        return '<p class="detail-note">この頁の文字の層は文字化けしていて読めません。原典の PDF をご覧ください。</p>';
    }
    if (isset($page['plain'])) {
        return '<pre class="hyoka-sheet-plain">' . htmlspecialchars((string)$page['plain'], ENT_QUOTES, 'UTF-8') . '</pre>';
    }
    if (is_array($page['canvas'] ?? null) && $page['canvas'] !== []) {
        return miyabe_hyoka_display_sheet_canvas_html($page);
    }
    // 表と表の外の文字を、頁の上からの位置で並べる。
    $items = [];
    foreach (($page['tables'] ?? []) as $table) {
        if (is_array($table)) {
            $items[] = [(float)($table['top'] ?? 0), 0, miyabe_hyoka_display_sheet_table_html($table)];
        }
    }
    foreach (($page['lines'] ?? []) as $line) {
        if (is_array($line)) {
            $items[] = [(float)($line['y'] ?? 0), (int)($line['x'] ?? 0) + 1,
                '<p class="hyoka-sheet-line">' . htmlspecialchars((string)($line['text'] ?? ''), ENT_QUOTES, 'UTF-8') . '</p>'];
        }
    }
    usort($items, static fn(array $a, array $b): int => [$a[0], $a[1]] <=> [$b[0], $b[1]]);
    $html = implode('', array_column($items, 2));
    foreach (($page['vertical'] ?? []) as $text) {
        $html .= '<p class="hyoka-sheet-line">' . htmlspecialchars((string)$text, ENT_QUOTES, 'UTF-8') . '</p>';
    }
    return $html;
}

// 1 頁を文字にする（MCP で AI に渡す形）。表は行ごとに升目の文字を「 | 」で区切り、表の外の文字は行のまま、
// 上からの位置の順に並べる。重なる升目の扱いは画面と同じ（miyabe_hyoka_display_sheet_grid）。
function miyabe_hyoka_display_sheet_page_text(array $page): string
{
    if (!empty($page['image_only'])) {
        return '（この頁には文字の層がありません）';
    }
    if (!empty($page['garbled'])) {
        return '（この頁の文字の層は文字化けしていて読めません）';
    }
    if (isset($page['plain'])) {
        return trim((string)$page['plain']);
    }
    $items = [];
    if (is_array($page['canvas'] ?? null) && $page['canvas'] !== []) {
        foreach ($page['canvas'] as $item) {
            if (is_array($item) && trim((string)($item['x'] ?? '')) !== '') {
                $items[] = [(float)($item['t'] ?? 0), (float)($item['l'] ?? 0), trim((string)$item['x'])];
            }
        }
    }
    foreach (($page['tables'] ?? []) as $tableIndex => $table) {
        if (!is_array($table)) {
            continue;
        }
        $lines = [];
        foreach (miyabe_hyoka_display_sheet_grid(is_array($table['cells'] ?? null) ? $table['cells'] : []) as $cells) {
            $texts = [];
            foreach ($cells as $cell) {
                $text = preg_replace('/\s+/u', ' ', trim((string)($cell['text'] ?? ''))) ?? '';
                if ($text !== '') {
                    $texts[] = $text;
                }
            }
            if ($texts !== []) {
                $lines[] = implode(' | ', $texts);
            }
        }
        if ($lines !== []) {
            $items[] = [(float)($table['top'] ?? 0), 0.0, '[表 ' . ($tableIndex + 1) . "]\n" . implode("\n", $lines)];
        }
    }
    foreach (($page['lines'] ?? []) as $line) {
        if (is_array($line) && trim((string)($line['text'] ?? '')) !== '') {
            $items[] = [(float)($line['y'] ?? 0), (float)($line['x'] ?? 0) + 1, trim((string)$line['text'])];
        }
    }
    usort($items, static fn(array $a, array $b): int => [$a[0], $a[1]] <=> [$b[0], $b[1]]);
    $text = implode("\n", array_column($items, 2));
    foreach (($page['vertical'] ?? []) as $vertical) {
        $text .= "\n" . trim((string)$vertical);
    }
    return trim($text);
}

// シートの 1 頁と、前後の頁へ動く釦。
function miyabe_hyoka_display_sheet_html(array $sheet, string $sourceUrl = ''): string
{
    $page = $sheet['page'];
    $pages = $sheet['pages'];
    $number = (int)($page['page'] ?? 0);
    $index = array_search($number, $pages, true);
    $html = '<div class="hyoka-sheet-nav">';
    if ($index !== false && $index > 0) {
        $html .= '<button type="button" data-sheet-page="' . $pages[$index - 1] . '">前の頁</button>';
    }
    $html .= '<span>原典 ' . $number . '頁（全 ' . count($pages) . ' 頁）</span>';
    if ($index !== false && $index < count($pages) - 1) {
        $html .= '<button type="button" data-sheet-page="' . $pages[$index + 1] . '">次の頁</button>';
    }
    if ($sourceUrl !== '' && preg_match('#^https?://#i', $sourceUrl)) {
        $base = preg_replace('/#.*$/', '', $sourceUrl);
        // HTML の原典には頁が無いので、頁の印を付けない。
        $link = preg_match('/\.html?$/i', (string)parse_url($base, PHP_URL_PATH)) ? $base : $base . '#page=' . $number;
        $html .= '<a href="' . htmlspecialchars($link, ENT_QUOTES, 'UTF-8') . '" target="_blank" rel="noopener noreferrer">原典を開く</a>';
    }
    return $html . '</div>' . miyabe_hyoka_display_sheet_page_html($page);
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
        'method' => miyabe_hyoka_display_is_ai($record) ? 'ai' : (miyabe_hyoka_display_is_form($record) ? 'program' : 'machine'),
    ];
}

// 自治体の様式に合わせた読み取りプログラム（tools/hyoka/forms）で書き写した記録か。金額などを表で持つ。
function miyabe_hyoka_display_is_form(array $record): bool
{
    return ($record['layout'] ?? '') === 'form_reader';
}

// AI が書き写した記録か（doc/hyoka-ai-extraction.md）。様式の定義で機械が取り出した記録と断り書きを分ける。
function miyabe_hyoka_display_is_ai(array $record): bool
{
    return ($record['layout'] ?? '') === 'ai_transcription';
}

// AI の記録を書き写した型の呼び名。型を持たない控えは、最初に使った gpt-6-luna で書き写したもの。
function miyabe_hyoka_display_ai_model_label(array $records): string
{
    $names = ['gpt-6-luna' => 'OpenAI の gpt-6-luna', 'claude-haiku-5-5' => 'Anthropic の Claude Haiku 5.5'];
    $labels = [];
    foreach (array_filter($records, 'miyabe_hyoka_display_is_ai') as $record) {
        foreach (explode(' + ', (string)($record['model'] ?? 'gpt-6-luna')) as $model) {
            $labels[$names[$model] ?? $model] = true;
        }
    }
    return implode('・', array_keys($labels));
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

// 年度と段階の列、欄名の行の表（金額、財源、人員）。値は原典の升目の原文のまま。
function miyabe_hyoka_display_pivot_html(mixed $table, string $title): string
{
    if (!is_array($table) || !is_array($table['columns'] ?? null) || !is_array($table['rows'] ?? null) || $table['rows'] === []) {
        return '';
    }
    $unit = trim((string)($table['unit'] ?? ''));
    $html = '<h3 class="hyoka-record-subhead">' . miyabe_hyoka_display_h($title)
        . ($unit !== '' ? '<small>（単位：' . miyabe_hyoka_display_h($unit) . '）</small>' : '') . '</h3>'
        . '<div class="hyoka-sheet-scroll"><table class="hyoka-record-table"><thead><tr><th scope="col">欄</th>';
    foreach ($table['columns'] as $column) {
        $year = (int)($column['year'] ?? 0);
        $html .= '<th scope="col">' . miyabe_hyoka_display_h(($year > 0 ? $year . '年度' : '') . ' ' . (string)($column['stage'] ?? '')) . '</th>';
    }
    $html .= '</tr></thead><tbody>';
    foreach ($table['rows'] as $row) {
        if (!is_array($row)) {
            continue;
        }
        $label = (string)($row['label'] ?? '');
        $kind = (string)($row['kind'] ?? '');
        $rowUnit = (string)($row['unit'] ?? '');
        $html .= '<tr><th scope="row">' . miyabe_hyoka_display_h($label)
            . ($kind !== '' && $kind !== $label ? '<small>' . miyabe_hyoka_display_h($kind) . '</small>' : '')
            . ($rowUnit !== '' ? '<small>（' . miyabe_hyoka_display_h($rowUnit) . '）</small>' : '') . '</th>';
        foreach (($row['values'] ?? []) as $value) {
            $html .= '<td>' . miyabe_hyoka_display_h((string)$value) . '</td>';
        }
        $html .= '</tr>';
    }
    return $html . '</tbody></table></div>';
}

// 指標ごとの年度の目標と実績。
function miyabe_hyoka_display_indicators_html(mixed $indicators): string
{
    if (!is_array($indicators) || $indicators === []) {
        return '';
    }
    $html = '<h3 class="hyoka-record-subhead">指標</h3>';
    foreach ($indicators as $indicator) {
        $values = array_values(array_filter($indicator['values'] ?? [], 'is_array'));
        if (!is_array($indicator) || $values === []) {
            continue;
        }
        $head = trim((string)($indicator['name'] ?? ''));
        $notes = array_filter([(string)($indicator['kind'] ?? ''), (string)($indicator['unit'] ?? '') !== '' ? '単位 ' . $indicator['unit'] : '']);
        $html .= '<div class="hyoka-sheet-scroll"><table class="hyoka-record-table"><caption>' . miyabe_hyoka_display_h($head !== '' ? $head : '（指標名なし）')
            . ($notes !== [] ? '<small>' . miyabe_hyoka_display_h(implode('・', $notes)) . '</small>' : '') . '</caption><thead><tr><th scope="col"></th>';
        foreach ($values as $value) {
            $year = (int)($value['year'] ?? 0);
            $html .= '<th scope="col">' . miyabe_hyoka_display_h($year > 0 ? $year . '年度' : '年度不明') . '</th>';
        }
        $html .= '</tr></thead><tbody>';
        foreach (['target' => '目標', 'actual' => '実績', 'rate' => '達成率'] as $key => $name) {
            $cells = array_map(static fn(array $value): string => (string)($value[$key] ?? ''), $values);
            if (implode('', $cells) === '') {
                continue;
            }
            $html .= '<tr><th scope="row">' . $name . '</th>';
            foreach ($cells as $cell) {
                $html .= '<td>' . miyabe_hyoka_display_h($cell) . '</td>';
            }
            $html .= '</tr>';
        }
        $html .= '</tbody></table></div>';
    }
    return $html;
}

// 評価の段階ごとの区分、方向性、観点。
function miyabe_hyoka_display_evaluations_html(mixed $evaluations): string
{
    if (!is_array($evaluations) || $evaluations === []) {
        return '';
    }
    $html = '<h3 class="hyoka-record-subhead">評価の段階</h3><dl class="hyoka-record-fields">';
    foreach ($evaluations as $evaluation) {
        if (!is_array($evaluation)) {
            continue;
        }
        $parts = array_filter([
            (string)($evaluation['rating'] ?? '') !== '' ? '評価: ' . $evaluation['rating'] : '',
            (string)($evaluation['direction'] ?? '') !== '' ? '方向性: ' . $evaluation['direction'] : '',
            implode(' / ', array_filter($evaluation['aspects'] ?? [], 'is_string')),
        ]);
        $html .= '<div><dt>' . miyabe_hyoka_display_h((string)($evaluation['stage'] ?? '') ?: '評価') . '</dt><dd>'
            . miyabe_hyoka_display_h(implode("\n", $parts)) . '</dd></div>';
    }
    return $html . '</dl>';
}

// 検算の結果。合計と内訳（事業費＋人件費＝総費用、財源の合計＝事業費）を確かめた組の数と、合わなかったもの。
function miyabe_hyoka_display_checks_html(mixed $checks): string
{
    if (!is_array($checks)) {
        return '';
    }
    $pairs = (int)($checks['pairs'] ?? 0);
    $problems = array_values(array_filter($checks['problems'] ?? [], 'is_string'));
    if ($pairs === 0 && $problems === []) {
        return '';
    }
    $html = '<p class="hyoka-record-checks">検算: 合計と内訳を ' . number_format($pairs) . ' 組確かめ、'
        . ($problems === [] ? 'すべて合っています。' : '合わないものがあります（原典のまま載せています）。') . '</p>';
    if ($problems !== []) {
        $html .= '<ul class="hyoka-record-checks">';
        foreach ($problems as $problem) {
            $html .= '<li>' . miyabe_hyoka_display_h($problem) . '</li>';
        }
        $html .= '</ul>';
    }
    return $html;
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
    $html .= miyabe_hyoka_display_pivot_html($record['amounts'] ?? null, '金額')
        . miyabe_hyoka_display_pivot_html($record['funding'] ?? null, '財源')
        . miyabe_hyoka_display_pivot_html($record['staff'] ?? null, '人員・人件費の単価')
        . miyabe_hyoka_display_indicators_html($record['indicators'] ?? null)
        . miyabe_hyoka_display_evaluations_html($record['evaluations'] ?? null);
    $raw = array_values(array_filter($record['raw_fields'] ?? [], 'is_array'));
    if ($raw !== []) {
        $html .= '<h3 class="hyoka-record-subhead">そのほかの欄（原典の欄名のまま）</h3><dl class="hyoka-record-fields is-raw">';
        foreach ($raw as $field) {
            $html .= '<div><dt>' . miyabe_hyoka_display_h((string)($field['label'] ?? '')) . '</dt><dd>'
                . miyabe_hyoka_display_h((string)($field['text'] ?? '')) . '</dd></div>';
        }
        $html .= '</dl>';
    }
    $html .= miyabe_hyoka_display_checks_html($record['checks'] ?? null);
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
