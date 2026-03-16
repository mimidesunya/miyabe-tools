<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'municipalities.php';

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

function extract_query_terms(string $query): array
{
    $normalized = preg_replace('/["()]/u', ' ', $query);
    $parts = preg_split('/[\s　]+/u', (string)$normalized, -1, PREG_SPLIT_NO_EMPTY);
    if ($parts === false) {
        return [];
    }

    $terms = [];
    $skipNext = false;
    foreach ($parts as $part) {
        $token = trim($part);
        if ($token === '') {
            continue;
        }

        $upper = strtoupper($token);
        if ($skipNext) {
            $skipNext = false;
            continue;
        }
        if ($upper === 'NOT') {
            $skipNext = true;
            continue;
        }
        if (in_array($upper, ['AND', 'OR'], true) || str_starts_with($upper, 'NEAR')) {
            continue;
        }
        if (!preg_match('/[\p{Han}\p{Hiragana}\p{Katakana}]/u', $token) && strlen($token) < 2) {
            continue;
        }

        $terms[] = $token;
    }

    return array_values(array_unique($terms));
}

function ordered_terms(array $terms): array
{
    $terms = array_values(array_filter(array_map('strval', $terms), static fn (string $term): bool => $term !== ''));
    usort($terms, static fn (string $a, string $b): int => mb_strlen($b, 'UTF-8') <=> mb_strlen($a, 'UTF-8'));
    return $terms;
}

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

$slug = get_slug();
if ($slug === '') {
    $slug = get_default_slug();
}
$municipality = municipality_entry($slug);
if ($municipality === null) {
    http_response_code(404);
    echo '自治体が見つかりません。';
    exit;
}
$gijirokuFeature = municipality_feature($slug, 'gijiroku') ?? [];
$featureAvailable = (bool)($gijirokuFeature['enabled'] ?? false);
$switcherItems = municipality_switcher_items('gijiroku');
$assemblyName = (string)($gijirokuFeature['assembly_name'] ?? ($municipality['name'] . '議会'));
$pageTitle = (string)($gijirokuFeature['title'] ?? ($assemblyName . ' 会議録 全文検索'));
$clearUrl = '/gijiroku/?slug=' . rawurlencode($slug);
$featureNotice = $featureAvailable ? '' : ($assemblyName . 'の会議録は準備中です。');
$dbPath = (string)($gijirokuFeature['db_path'] ?? '');
$q = trim((string)($_GET['q'] ?? ''));
$year = trim((string)($_GET['year'] ?? ''));
$hasRequestedDoc = isset($_GET['doc']) && (int)$_GET['doc'] > 0;
$selectedId = max(0, (int)($_GET['doc'] ?? 0));
$workspaceTabParam = trim((string)($_GET['tab'] ?? ''));
$viewerTabParam = trim((string)($_GET['viewer_tab'] ?? ''));
$page = max(1, (int)($_GET['page'] ?? 1));
$perPage = 20;
$offset = ($page - 1) * $perPage;
$queryTerms = extract_query_terms($q);

$pdo = null;
$error = '';
if ($featureAvailable && $dbPath !== '' && is_file($dbPath)) {
    try {
        $pdo = new PDO('sqlite:' . $dbPath);
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $pdo->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);
    } catch (Exception $e) {
        $error = 'SQLiteの読み込みに失敗しました。';
    }
} elseif ($featureAvailable) {
    $error = '検索DBが見つかりません。tools/gijiroku/build_minutes_index.py --slug ' . $slug . ' を実行してください。';
}

$rows = [];
$detail = null;
$detailDocument = null;
$detailMatches = [];
$focusAnchor = '';
$stats = ['documents' => 0, 'years' => 0, 'first_date' => null, 'last_date' => null];
$yearOptions = [];
$total = 0;

if ($pdo) {
    $statsStmt = $pdo->query("SELECT COUNT(*) AS documents, COUNT(DISTINCT year_label) AS years, MIN(held_on) AS first_date, MAX(held_on) AS last_date FROM minutes WHERE doc_type = 'minutes'");
    $stats = $statsStmt->fetch() ?: $stats;

    $yearOptionsStmt = $pdo->query("SELECT year_label, COUNT(*) AS count, MAX(held_on) AS last_date FROM minutes WHERE doc_type = 'minutes' GROUP BY year_label ORDER BY last_date DESC, year_label DESC");
    $yearOptions = $yearOptionsStmt->fetchAll();

    if ($q !== '') {
        try {
            $countSql = 'SELECT COUNT(*) FROM minutes_fts JOIN minutes m ON m.id = minutes_fts.rowid WHERE minutes_fts MATCH :q';
            if ($year !== '') {
                $countSql .= ' AND m.year_label = :year';
            }

            $countStmt = $pdo->prepare($countSql);
            $countStmt->bindValue(':q', $q, PDO::PARAM_STR);
            if ($year !== '') {
                $countStmt->bindValue(':year', $year, PDO::PARAM_STR);
            }
            $countStmt->execute();
            $total = (int)$countStmt->fetchColumn();

            $sql = 'SELECT m.id, m.title, m.meeting_name, m.year_label, m.held_on, m.rel_path, m.source_url, snippet(minutes_fts, 2, "[[[", "]]]", "…", 18) AS excerpt, bm25(minutes_fts) AS score FROM minutes_fts JOIN minutes m ON m.id = minutes_fts.rowid WHERE minutes_fts MATCH :q';
            if ($year !== '') {
                $sql .= ' AND m.year_label = :year';
            }
            $sql .= ' ORDER BY score ASC, m.held_on DESC, m.id DESC LIMIT :limit OFFSET :offset';

            $stmt = $pdo->prepare($sql);
            $stmt->bindValue(':q', $q, PDO::PARAM_STR);
            if ($year !== '') {
                $stmt->bindValue(':year', $year, PDO::PARAM_STR);
            }
            $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
            $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
            $stmt->execute();
            $rows = $stmt->fetchAll();
        } catch (Exception $e) {
            $error = '検索式の解釈に失敗しました。キーワードを調整してください。';
        }
    } else {
        $countSql = "SELECT COUNT(*) FROM minutes WHERE doc_type = 'minutes'";
        if ($year !== '') {
            $countStmt = $pdo->prepare($countSql . ' AND year_label = :year');
            $countStmt->bindValue(':year', $year, PDO::PARAM_STR);
            $countStmt->execute();
            $total = (int)$countStmt->fetchColumn();
        } else {
            $total = (int)$pdo->query($countSql)->fetchColumn();
        }

        $sql = 'SELECT id, title, meeting_name, year_label, held_on, rel_path, source_url, substr(content, 1, 240) AS excerpt FROM minutes WHERE doc_type = \'minutes\'';
        if ($year !== '') {
            $sql .= ' AND year_label = :year';
        }
        $sql .= ' ORDER BY held_on DESC, id DESC LIMIT :limit OFFSET :offset';

        $stmt = $pdo->prepare($sql);
        if ($year !== '') {
            $stmt->bindValue(':year', $year, PDO::PARAM_STR);
        }
        $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
        $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
        $stmt->execute();
        $rows = $stmt->fetchAll();
    }

    if ($selectedId === 0 && !empty($rows)) {
        $selectedId = (int)$rows[0]['id'];
    }

    if ($selectedId > 0) {
        $detailStmt = $pdo->prepare("SELECT id, title, meeting_name, year_label, held_on, rel_path, source_url, source_fino, content FROM minutes WHERE id = :id AND doc_type = 'minutes'");
        $detailStmt->bindValue(':id', $selectedId, PDO::PARAM_INT);
        $detailStmt->execute();
        $detail = $detailStmt->fetch() ?: null;
        if ($detail) {
            $detailDocument = annotate_document_matches(parse_minutes_document((string)$detail['content']), $queryTerms);
            $detailMatches = $detailDocument['matches'] ?? [];
            $focusAnchor = $detailMatches[0]['anchor'] ?? '';
        }
    }
}

$totalPages = max(1, (int)ceil($total / $perPage));
$start = $total > 0 ? ($offset + 1) : 0;
$end = min($total, $offset + $perPage);
$headerLines = $detailDocument ? array_values(array_filter($detailDocument['preamble']['header'], static fn (string $line): bool => $line !== '' && $line !== (string)($detail['title'] ?? ''))) : [];
$matchPreview = array_slice($detailMatches, 0, 8);
$queryTermPreview = array_slice($queryTerms, 0, 6);
$activeFilters = [];
if ($q !== '') {
    $activeFilters[] = ['label' => '検索語', 'value' => $q];
}
if ($year !== '') {
    $activeFilters[] = ['label' => '年度', 'value' => $year];
}
$resultModeLabel = $q !== '' ? 'キーワード検索' : ($year !== '' ? '年度絞り込み' : '最新順ブラウズ');
$pageSummary = $total > 0 ? ($page . ' / ' . $totalPages) : '0 / 1';
$detailFacts = [];
if ($detail) {
    if (!empty($detail['held_on'])) {
        $detailFacts[] = ['label' => '開催日', 'value' => (string)$detail['held_on']];
    }
    $detailFacts[] = ['label' => '年度', 'value' => (string)$detail['year_label']];
    if (!empty($detail['meeting_name'])) {
        $detailFacts[] = ['label' => '会議名', 'value' => (string)$detail['meeting_name']];
    }
    $detailFacts[] = ['label' => 'ファイル', 'value' => (string)$detail['rel_path']];
    if (!empty($detail['source_fino'])) {
        $detailFacts[] = ['label' => 'FINO', 'value' => (string)$detail['source_fino']];
    }
}
$workspaceTab = in_array($workspaceTabParam, ['results', 'viewer'], true) ? $workspaceTabParam : ($hasRequestedDoc ? 'viewer' : 'results');
if (!$detail) {
    $workspaceTab = 'results';
}
$viewerTab = in_array($viewerTabParam, ['transcript', 'summary', 'matches'], true) ? $viewerTabParam : ($detailMatches !== [] ? 'matches' : 'transcript');
if ($viewerTab === 'matches' && $detailMatches === []) {
    $viewerTab = 'summary';
}
?><!doctype html>
<html lang="ja">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title><?php echo h($pageTitle); ?></title>
    <style>
        :root { --bg:#f5f1e8; --bg2:#ece2cf; --panel:rgba(255,252,246,.92); --panel-strong:#fffdfa; --line:#d8ccb5; --line-strong:#c5b28c; --text:#18251f; --muted:#61706a; --accent:#0f5c4d; --accent-soft:rgba(15,92,77,.12); --accent2:#b56d2f; --accent2-soft:rgba(181,109,47,.12); --match:#ffef96; --shadow:0 22px 50px rgba(75,57,32,.12); --shadow-soft:0 12px 30px rgba(75,57,32,.08); --radius:24px; --radius-sm:16px; }
        * { box-sizing:border-box; }
        body { margin:0; min-height:100vh; color:var(--text); font-family:'BIZ UDPGothic','Hiragino Sans','Yu Gothic',sans-serif; background:radial-gradient(circle at 0% 0%, rgba(255,255,255,.74), transparent 30%), radial-gradient(circle at 100% 0%, rgba(181,109,47,.18), transparent 28%), linear-gradient(180deg, #e6dac4 0%, var(--bg2) 16%, var(--bg) 48%, #fbf8f2 100%); }
        a { color:inherit; }
        .shell { max-width:1520px; margin:0 auto; padding:28px 18px 40px; }
        .hero, .layout, .main-column, .workspace, .viewer-tabs { display:grid; gap:18px; }
        .hero { grid-template-columns:minmax(0,1.18fr) minmax(280px,.82fr); margin-bottom:18px; align-items:stretch; }
        .layout { grid-template-columns:320px minmax(0,1fr); align-items:start; }
        .main-column { grid-template-columns:1fr; }
        .hero-card, .stats, .filters, .results, .viewer, .error { background:var(--panel); border:1px solid rgba(216,204,181,.88); border-radius:var(--radius); box-shadow:var(--shadow); backdrop-filter:blur(16px); }
        .hero-card, .filters, .results, .viewer { padding:24px; }
        .workspace-head { display:flex; justify-content:space-between; align-items:flex-end; gap:16px; padding:18px 22px; background:var(--panel); border:1px solid rgba(216,204,181,.88); border-radius:var(--radius); box-shadow:var(--shadow); backdrop-filter:blur(16px); }
        .hero-card { min-height:250px; position:relative; overflow:hidden; display:flex; flex-direction:column; justify-content:space-between; gap:20px; background:linear-gradient(135deg, rgba(255,253,249,.98), rgba(250,244,232,.94)); }
        .hero-card::before { content:''; position:absolute; inset:auto auto 18px -26px; width:180px; height:180px; border-radius:40px; background:radial-gradient(circle, rgba(15,92,77,.12), transparent 68%); filter:blur(2px); }
        .hero-card::after { content:''; position:absolute; right:-44px; top:-30px; width:220px; height:220px; border-radius:50%; background:radial-gradient(circle, rgba(181,109,47,.18), transparent 68%); }
        .eyebrow { margin:0 0 10px; font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--accent); font-weight:800; }
        h1 { margin:0; font-size:clamp(30px,4vw,46px); line-height:1.06; font-family:'Yu Mincho','Hiragino Mincho ProN',serif; letter-spacing:.01em; }
        .hero-copy { margin-top:14px; max-width:48rem; font-size:15px; line-height:1.85; color:#35433c; }
        .hero-tags, .top-links, .actions, .chips, .badges, .viewer-links, .pager, .active-filters, .viewer-kicker { display:flex; flex-wrap:wrap; gap:10px; }
        .hero-tags { margin-top:18px; }
        .hero-tag, .top-links a, .button, .button-secondary, .pager a, .pager span, .chip, .active-filter, .viewer-links a { text-decoration:none; border-radius:999px; }
        .hero-tag { padding:8px 12px; font-size:12px; font-weight:800; color:var(--accent); border:1px solid rgba(15,92,77,.14); background:rgba(255,255,255,.74); }
        .hero-context { position:relative; z-index:1; padding:14px 16px; border-radius:20px; border:1px solid rgba(216,204,181,.88); background:rgba(255,255,255,.76); max-width:56rem; }
        .hero-context-title { font-size:12px; font-weight:800; letter-spacing:.05em; text-transform:uppercase; color:var(--muted); }
        .hero-context-meta { margin-top:10px; font-size:13px; line-height:1.8; color:#3b4a42; }
        .top-links { margin-top:18px; }
        .top-links a, .chip, .pager a, .pager span, .active-filter { padding:8px 12px; font-size:13px; border:1px solid var(--line); background:rgba(255,255,255,.74); }
        .municipality-switcher { display:inline-flex; align-items:center; gap:8px; }
        .municipality-switcher select { border:0; background:transparent; color:inherit; font:inherit; min-width:150px; }
        .municipality-switcher select:focus { outline:none; }
        .active-filter { align-items:center; gap:8px; }
        .active-filter strong, .chip strong { font-size:11px; color:var(--muted); }
        .button, .button-secondary { padding:12px 16px; font-size:13px; cursor:pointer; transition:transform .14s ease, box-shadow .14s ease, border-color .14s ease; }
        .button:hover, .button-secondary:hover { transform:translateY(-1px); box-shadow:var(--shadow-soft); }
        .button { border:1px solid var(--accent); background:linear-gradient(180deg, #18705d, var(--accent)); color:#fff; }
        .button-secondary { border:1px solid var(--line); background:#fffdf8; color:var(--text); text-align:center; }
        .stats { padding:20px; display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:12px; align-content:start; background:linear-gradient(180deg, rgba(255,252,247,.98), rgba(248,243,233,.92)); }
        .stats-head { grid-column:1 / -1; display:grid; gap:6px; }
        .stats-copy { font-size:14px; line-height:1.75; color:#324038; }
        .stat { min-height:112px; padding:16px; border-radius:18px; border:1px solid rgba(216,204,181,.84); background:var(--panel-strong); box-shadow:inset 0 1px 0 rgba(255,255,255,.64); }
        .stat-label, .results-meta, .viewer-meta, .field label, .examples p, .section-copy, .field-hint, .active-filter-card p, .summary-metric span, .results-toolbar-label { font-size:12px; color:var(--muted); }
        .stat-value { margin-top:8px; font-size:28px; line-height:1.15; font-weight:700; font-family:'Yu Mincho','Hiragino Mincho ProN',serif; }
        .tab-list { display:flex; flex-wrap:wrap; gap:10px; }
        .tab-button { appearance:none; display:inline-flex; align-items:center; justify-content:center; border:1px solid var(--line); background:rgba(255,255,255,.72); color:var(--muted); padding:10px 14px; border-radius:999px; font:inherit; font-size:13px; font-weight:800; cursor:pointer; transition:border-color .14s ease, background .14s ease, color .14s ease, transform .14s ease, box-shadow .14s ease; }
        .tab-button:hover { transform:translateY(-1px); box-shadow:var(--shadow-soft); }
        .tab-button.is-active { border-color:var(--accent); background:var(--accent); color:#fff; }
        .tab-button.is-disabled { opacity:.55; }
        .tab-button .tab-count { margin-left:6px; font-size:11px; opacity:.84; }
        .tab-panel[hidden] { display:none !important; }
        .workspace-panel { display:grid; }
        .filters { position:sticky; top:18px; display:grid; gap:18px; }
        .filters-head, .examples, .active-filter-card { display:grid; gap:10px; }
        .section-title { margin:0; font-size:17px; line-height:1.5; font-weight:800; letter-spacing:.01em; }
        .section-copy { margin:0; line-height:1.8; }
        .results-head, .viewer-head { display:flex; justify-content:space-between; align-items:flex-start; gap:18px; margin-bottom:18px; }
        .results-meta, .viewer-meta { line-height:1.8; color:#3b4a42; }
        .results-summary { display:grid; grid-template-columns:repeat(3, minmax(120px,1fr)); gap:10px; min-width:min(100%, 390px); }
        .summary-metric { padding:12px 14px; border-radius:18px; border:1px solid rgba(216,204,181,.84); background:rgba(255,255,255,.76); }
        .summary-metric span { display:block; }
        .summary-metric strong { display:block; margin-top:8px; font-size:15px; line-height:1.45; }
        .search-form, .field, .result-list, .viewer-panels, .match-list, .transcript, .header-lines, .transcript-column { display:grid; gap:12px; }
        .field { gap:6px; }
        .field label { font-weight:800; }
        .field input, .field select { width:100%; padding:12px 14px; font-size:14px; color:var(--text); border:1px solid var(--line); border-radius:14px; background:#fffdf8; box-shadow:inset 0 1px 2px rgba(75,57,32,.03); transition:border-color .14s ease, box-shadow .14s ease; }
        .field input:focus, .field select:focus { outline:none; border-color:rgba(15,92,77,.52); box-shadow:0 0 0 4px rgba(15,92,77,.12); }
        .field-hint, .active-filter-card p, .examples p { margin:0; line-height:1.7; }
        .actions .button, .actions .button-secondary { flex:1 1 140px; }
        .examples { padding-top:16px; border-top:1px solid rgba(216,204,181,.82); }
        .jump-hint { margin-top:12px; padding:12px 14px; border-radius:16px; border:1px solid rgba(15,92,77,.14); background:linear-gradient(180deg, rgba(15,92,77,.08), rgba(255,255,255,.68)); color:var(--accent); font-size:12px; font-weight:800; line-height:1.7; }
        .results-filters { margin:-4px 0 16px; }
        .result-list { list-style:none; padding:0; margin:0; }
        .result-card, .summary-card, .speech-card, .note-card { background:var(--panel-strong); border-radius:20px; }
        .result-card { display:block; text-decoration:none; padding:18px; border:1px solid rgba(216,204,181,.84); border-left:4px solid transparent; transition:transform .14s ease, border-color .14s ease, box-shadow .14s ease; }
        .result-card:hover { transform:translateY(-2px); border-color:rgba(15,92,77,.3); box-shadow:var(--shadow-soft); }
        .result-card.active { border-color:rgba(15,92,77,.24); border-left-color:var(--accent); background:linear-gradient(180deg, rgba(255,255,255,.98), rgba(251,247,239,.95)); box-shadow:0 16px 32px rgba(15,92,77,.08); }
        .result-top { display:flex; align-items:flex-start; justify-content:space-between; gap:10px; }
        .result-rank { display:inline-flex; align-items:center; gap:8px; font-size:12px; font-weight:800; color:var(--accent); }
        .result-rank::before { content:''; width:8px; height:8px; border-radius:999px; background:var(--accent2); box-shadow:0 0 0 5px rgba(181,109,47,.08); }
        .result-title { margin:12px 0 10px; font-size:19px; line-height:1.42; }
        .badge, .match-badge { border-radius:999px; font-size:11px; font-weight:800; }
        .badge { padding:5px 10px; background:var(--accent-soft); color:var(--accent); }
        .badge-current { background:var(--accent2-soft); color:var(--accent2); }
        .result-sub { display:grid; gap:8px; font-size:13px; color:var(--muted); }
        .result-meta-row { display:flex; gap:8px; align-items:flex-start; }
        .result-meta-label { flex:0 0 auto; font-weight:800; color:#455149; }
        .result-meta-value { min-width:0; word-break:break-word; }
        .excerpt-wrap { margin-top:14px; padding:14px 16px; border-radius:18px; border:1px solid rgba(216,204,181,.74); background:rgba(255,255,255,.72); }
        .excerpt-label { display:block; margin-bottom:8px; font-size:11px; font-weight:800; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); }
        .excerpt { margin:0; font-size:14px; line-height:1.85; color:#304037; display:-webkit-box; -webkit-line-clamp:5; -webkit-box-orient:vertical; overflow:hidden; }
        .result-foot { margin-top:14px; display:flex; align-items:center; justify-content:space-between; gap:10px; font-size:12px; color:var(--accent); font-weight:800; }
        .result-foot::after { content:'→'; font-size:15px; }
        .viewer { min-height:720px; display:flex; flex-direction:column; }
        .anchor-alias { display:block; position:relative; top:-18px; visibility:hidden; }
        .viewer-head { padding-bottom:18px; border-bottom:1px solid rgba(216,204,181,.82); }
        .viewer-title { font-size:24px; }
        .viewer-links { justify-content:flex-end; }
        .viewer-links a { padding:9px 12px; font-size:13px; color:var(--accent); border:1px solid rgba(15,92,77,.16); background:rgba(255,255,255,.76); }
        .viewer-body { margin-top:18px; font-size:14px; line-height:1.9; scroll-behavior:smooth; }
        .viewer-section, .viewer-stack { display:grid; gap:12px; }
        .summary-card { padding:16px; border:1px solid rgba(216,204,181,.82); background:linear-gradient(180deg, rgba(255,255,255,.9), rgba(254,251,245,.94)); box-shadow:0 8px 24px rgba(75,57,32,.06); }
        .summary-card-accent { border-color:rgba(15,92,77,.18); background:linear-gradient(160deg, rgba(15,92,77,.08), rgba(255,255,255,.94)); }
        .summary-title { margin:0 0 10px; font-size:12px; font-weight:800; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); }
        .summary-copy { margin:0; font-size:13px; line-height:1.8; color:#334037; }
        .header-lines { gap:8px; }
        .header-line { font-size:15px; line-height:1.65; font-weight:700; }
        .meta-grid { display:grid; grid-template-columns:1fr; gap:10px; }
        .meta-item { padding:12px 13px; border-radius:16px; border:1px solid rgba(216,204,181,.72); background:rgba(255,255,255,.68); }
        .meta-label { font-size:11px; font-weight:800; color:var(--muted); }
        .meta-value, .agenda-list li, .match-link span { font-size:13px; line-height:1.75; word-break:break-word; }
        .agenda-list { list-style:none; padding:0; margin:0; display:grid; gap:8px; }
        .agenda-list li { position:relative; padding-left:14px; }
        .agenda-list li::before { content:''; position:absolute; left:0; top:.8em; width:6px; height:6px; border-radius:999px; background:var(--accent2); }
        .match-link { display:grid; gap:5px; padding:11px 12px; text-decoration:none; border-radius:16px; border:1px solid rgba(15,92,77,.16); background:rgba(255,255,255,.76); transition:transform .14s ease, border-color .14s ease, box-shadow .14s ease; }
        .match-link:hover { transform:translateY(-1px); border-color:rgba(15,92,77,.34); box-shadow:var(--shadow-soft); }
        .match-link strong { font-size:12px; color:var(--accent); }
        .match-more { margin-top:8px; font-size:12px; color:var(--muted); }
        .transcript-intro { padding:14px 16px; border-radius:18px; border:1px solid rgba(216,204,181,.78); background:rgba(255,255,255,.7); color:var(--muted); }
        .transcript-intro strong { display:block; margin-bottom:6px; font-size:13px; color:var(--text); }
        .speech-card, .note-card { scroll-margin-top:18px; }
        .speech-card { display:grid; grid-template-columns:148px minmax(0,1fr); gap:14px; padding:16px; border:1px solid rgba(216,204,181,.84); background:linear-gradient(180deg, rgba(255,255,255,.98), rgba(252,249,243,.96)); }
        .speech-card.is-match, .note-card.is-match { border-color:rgba(181,109,47,.52); background:linear-gradient(180deg, rgba(255,248,232,.98), rgba(255,252,246,.96)); }
        .speech-card.is-target, .note-card.is-target { border-color:var(--accent); box-shadow:0 0 0 3px rgba(15,92,77,.12), 0 14px 28px rgba(15,92,77,.1); }
        .speech-aside { display:grid; align-content:start; gap:8px; padding-right:2px; }
        .speaker-mark { display:inline-flex; align-items:center; justify-content:center; width:36px; height:36px; border-radius:999px; background:rgba(15,92,77,.12); color:var(--accent); font-size:15px; font-weight:800; }
        .speaker-name { font-size:15px; line-height:1.5; font-weight:800; }
        .speaker-role { font-size:12px; color:var(--muted); }
        .match-badge { width:fit-content; padding:5px 9px; background:rgba(181,109,47,.12); color:var(--accent2); }
        .speech-content p, .note-card p { margin:0; }
        .speech-content p + p, .note-card p + p { margin-top:12px; }
        .note-card { padding:13px 14px; border:1px dashed rgba(216,204,181,.92); background:rgba(255,255,255,.58); color:#495146; }
        .note-card.stage { text-align:center; font-weight:700; color:var(--muted); background:rgba(248,243,231,.82); }
        .transcript-divider { margin:4px 0; border:0; border-top:1px solid rgba(195,179,146,.7); }
        .viewer-empty { display:grid; place-items:center; min-height:320px; padding:24px; text-align:center; color:var(--muted); border:1px dashed rgba(216,204,181,.9); border-radius:20px; background:rgba(255,255,255,.58); }
        .viewer-empty strong { display:block; font-size:16px; color:var(--text); }
        .viewer-empty span { display:block; margin-top:8px; line-height:1.8; }
        .error { padding:14px 16px; margin-bottom:18px; color:#8b1e27; background:#fff5f5; border-color:#efc8c8; }
        mark { padding:0 2px; border-radius:4px; background:var(--match); }
        @media (max-width:1320px) { .results-summary { grid-template-columns:repeat(2, minmax(120px,1fr)); } .workspace-head { align-items:flex-start; } }
        @media (max-width:1120px) { .hero, .layout, .main-column { grid-template-columns:1fr; } .filters { position:static; } .viewer { min-height:0; } .results-head, .viewer-head, .workspace-head { flex-direction:column; } .results-summary { width:100%; min-width:0; } .meta-grid { grid-template-columns:repeat(2, minmax(0,1fr)); } }
        @media (max-width:720px) { .shell { padding:18px 12px 28px; } .hero-card, .stats, .filters, .results, .viewer, .workspace-head { padding:16px; border-radius:18px; } .hero-card { min-height:0; } h1 { font-size:clamp(24px,8vw,34px); } .stats, .results-summary, .meta-grid { grid-template-columns:1fr; } .stat { min-height:0; } .top-links, .actions, .viewer-links, .tab-list { gap:8px; } .tab-button { width:100%; justify-content:center; } .speech-card { grid-template-columns:1fr; } .speech-aside { grid-template-columns:auto 1fr; align-items:center; column-gap:10px; } .speaker-role, .match-badge { grid-column:2; } .match-badge { margin-top:4px; } }
    </style>
</head>
<body>
<div class="shell">
    <?php if ($error !== ''): ?>
        <div class="error"><?php echo h($error); ?></div>
    <?php endif; ?>
    <?php if ($featureNotice !== ''): ?>
        <div class="error" style="color:#7a4c16; background:#fff7e8; border-color:#ead4a3;"><?php echo h($featureNotice); ?></div>
    <?php endif; ?>

    <section class="hero">
        <div class="hero-card">
            <div>
                <div class="eyebrow">Minutes Search Console</div>
                <h1><?php echo h($assemblyName); ?>の会議録を<br>日付・年度・本文から横断検索</h1>
                <div class="hero-copy">冒頭情報・会議メタデータ・発言本文を分けて表示し、検索結果を開くと一致した発言位置まで直接寄せます。全文をただ流すより、必要な論点へ早く届く見え方に整えています。</div>
                <div class="hero-tags">
                    <span class="hero-tag">SQLite FTS5</span>
                    <span class="hero-tag">発言単位で構造化</span>
                    <span class="hero-tag">一致箇所へジャンプ</span>
                </div>
            </div>

            <?php if ($activeFilters !== [] || $detail): ?>
                <div class="hero-context">
                    <div class="hero-context-title">現在の表示</div>
                    <?php if ($activeFilters !== []): ?>
                        <div class="active-filters">
                            <?php foreach ($activeFilters as $filter): ?>
                                <span class="active-filter"><strong><?php echo h((string)$filter['label']); ?></strong><?php echo h((string)$filter['value']); ?></span>
                            <?php endforeach; ?>
                        </div>
                    <?php endif; ?>
                    <div class="hero-context-meta">
                        <?php echo h((string)$total); ?>件ヒット / <?php echo h($pageSummary); ?>ページ
                        <?php if ($detail): ?> / 選択中: <?php echo h((string)$detail['title']); ?><?php endif; ?>
                    </div>
                </div>
            <?php endif; ?>

            <div class="top-links">
                <a href="/">トップへ戻る</a>
                <a href="<?php echo h($clearUrl); ?>">検索条件をクリア</a>
                <span class="chip municipality-switcher">
                    <strong>自治体</strong>
                    <select aria-label="自治体切り替え" onchange="if (this.value) { window.location.href = this.value; }">
                        <?php foreach ($switcherItems as $item): ?>
                            <?php $switchMunicipality = municipality_entry((string)$item['slug']); ?>
                            <?php $switchUrl = (string)($switchMunicipality['gijiroku']['url'] ?? ''); ?>
                            <option value="<?php echo h($switchUrl); ?>" <?php echo $item['slug'] === $slug ? 'selected' : ''; ?>>
                                <?php echo h($item['name'] . (!empty($item['enabled']) ? '' : ' (準備中)')); ?>
                            </option>
                        <?php endforeach; ?>
                    </select>
                </span>
            </div>
        </div>

        <div class="stats">
            <div class="stats-head">
                <div class="eyebrow">Coverage</div>
                <div class="stats-copy">会議録の収録範囲と更新状況です。最新開催日から過去分まで、同じUIのまま連続してたどれます。</div>
            </div>
            <div class="stat"><div class="stat-label">本文件数</div><div class="stat-value"><?php echo h((string)$stats['documents']); ?></div></div>
            <div class="stat"><div class="stat-label">収録年度</div><div class="stat-value"><?php echo h((string)$stats['years']); ?></div></div>
            <div class="stat"><div class="stat-label">最古開催日</div><div class="stat-value"><?php echo h((string)($stats['first_date'] ?? '不明')); ?></div></div>
            <div class="stat"><div class="stat-label">最新開催日</div><div class="stat-value"><?php echo h((string)($stats['last_date'] ?? '不明')); ?></div></div>
        </div>
    </section>

    <section class="layout">
        <aside class="filters">
            <div class="filters-head">
                <h2 class="section-title">検索条件</h2>
                <p class="section-copy">本文検索は SQLite FTS5 です。`AND` / `OR` / `NOT` / `NEAR/5` をそのまま使えます。</p>
            </div>
            <form class="search-form" method="get">
                <input type="hidden" name="slug" value="<?php echo h($slug); ?>">
                <div class="field">
                    <label for="q">キーワード</label>
                    <input id="q" type="text" name="q" value="<?php echo h($q); ?>" placeholder="例: 補正予算 / 子育て支援 AND 学校">
                    <div class="field-hint">近接条件も使えます。例: `学校 NEAR/5 空調`</div>
                </div>
                <div class="field">
                    <label for="year">年度</label>
                    <select id="year" name="year">
                        <option value="">すべての年度</option>
                        <?php foreach ($yearOptions as $option): ?>
                            <option value="<?php echo h((string)$option['year_label']); ?>" <?php echo $year === (string)$option['year_label'] ? 'selected' : ''; ?>><?php echo h((string)$option['year_label']); ?> (<?php echo h((string)$option['count']); ?>)</option>
                        <?php endforeach; ?>
                    </select>
                </div>
                <div class="actions">
                    <button class="button" type="submit">検索する</button>
                    <a class="button-secondary" href="<?php echo h($clearUrl); ?>">リセット</a>
                </div>
            </form>

            <?php if ($activeFilters !== []): ?>
                <div class="active-filter-card">
                    <p>適用中の条件</p>
                    <div class="active-filters">
                        <?php foreach ($activeFilters as $filter): ?>
                            <span class="active-filter"><strong><?php echo h((string)$filter['label']); ?></strong><?php echo h((string)$filter['value']); ?></span>
                        <?php endforeach; ?>
                    </div>
                </div>
            <?php endif; ?>

            <div class="examples">
                <p>検索例</p>
                <div class="chips">
                    <a class="chip" href="<?php echo h(query_with(['q' => '補正予算', 'doc' => null, 'page' => null, 'tab' => 'results', 'viewer_tab' => null])); ?>">補正予算</a>
                    <a class="chip" href="<?php echo h(query_with(['q' => '学校 AND 空調', 'doc' => null, 'page' => null, 'tab' => 'results', 'viewer_tab' => null])); ?>">学校 AND 空調</a>
                    <a class="chip" href="<?php echo h(query_with(['q' => '多摩川 NEAR/5 トイレ', 'doc' => null, 'page' => null, 'tab' => 'results', 'viewer_tab' => null])); ?>">多摩川 NEAR/5 トイレ</a>
                    <a class="chip" href="<?php echo h(query_with(['q' => '子育て OR 保育', 'doc' => null, 'page' => null, 'tab' => 'results', 'viewer_tab' => null])); ?>">子育て OR 保育</a>
                </div>
            </div>

            <div class="examples">
                <p>年度ショートカット</p>
                <div class="chips">
                    <?php foreach (array_slice($yearOptions, 0, 8) as $option): ?>
                        <a class="chip" href="<?php echo h(query_with(['year' => (string)$option['year_label'], 'doc' => null, 'page' => null, 'tab' => 'results', 'viewer_tab' => null])); ?>"><?php echo h((string)$option['year_label']); ?> <strong><?php echo h((string)$option['count']); ?></strong></a>
                    <?php endforeach; ?>
                </div>
            </div>
        </aside>

        <div class="main-column">
            <section class="workspace" data-tab-group="workspace" data-default-tab="<?php echo h($workspaceTab); ?>">
                <div class="workspace-head">
                    <div>
                        <h2 class="section-title">表示エリア</h2>
                        <p class="section-copy">検索結果と会議録詳細を同時に詰め込まず、必要な方だけ切り替えて表示します。</p>
                    </div>
                    <div class="tab-list" role="tablist" aria-label="表示エリア切り替え">
                        <button type="button" id="workspace-results-tab" class="tab-button<?php echo $workspaceTab === 'results' ? ' is-active' : ''; ?>" data-tab-button="results" aria-controls="workspace-results-panel" aria-selected="<?php echo $workspaceTab === 'results' ? 'true' : 'false'; ?>">検索結果 <span class="tab-count"><?php echo h((string)$total); ?></span></button>
                        <button type="button" id="workspace-viewer-tab" class="tab-button<?php echo $workspaceTab === 'viewer' ? ' is-active' : ''; ?><?php echo $detail ? '' : ' is-disabled'; ?>" data-tab-button="viewer" aria-controls="workspace-viewer-panel" aria-selected="<?php echo $workspaceTab === 'viewer' ? 'true' : 'false'; ?>">会議録詳細 <span class="tab-count"><?php echo $detail ? '選択中' : '未選択'; ?></span></button>
                    </div>
                </div>

                <div id="workspace-results-panel" class="tab-panel workspace-panel" data-tab-panel="results" <?php if ($workspaceTab !== 'results'): ?>hidden<?php endif; ?>>
                    <section class="results">
                        <div class="results-head">
                            <div>
                                <h2 class="section-title">検索結果</h2>
                                <div class="results-meta">
                                    <?php if ($q !== ''): ?>検索語: <?php echo h($q); ?><br><?php else: ?>新しい会議録順で表示中<br><?php endif; ?>
                                    <?php if ($year !== ''): ?>年度: <?php echo h($year); ?><br><?php endif; ?>
                                    <?php echo h((string)$total); ?>件中 <?php echo h((string)$start); ?>-<?php echo h((string)$end); ?>件を表示
                                </div>
                                <?php if ($q !== ''): ?><div class="jump-hint">結果を開くと、会議録詳細タブで一致発言一覧を開きます。</div><?php endif; ?>
                            </div>
                            <div class="results-summary">
                                <div class="summary-metric">
                                    <span>表示モード</span>
                                    <strong><?php echo h($resultModeLabel); ?></strong>
                                </div>
                                <div class="summary-metric">
                                    <span>現在ページ</span>
                                    <strong><?php echo h($pageSummary); ?></strong>
                                </div>
                                <div class="summary-metric">
                                    <span><?php echo $q !== '' ? '抽出語' : '並び順'; ?></span>
                                    <strong><?php echo $q !== '' ? h((string)count($queryTerms)) . '語' : '開催日の新しい順'; ?></strong>
                                </div>
                            </div>
                        </div>

                        <?php if ($queryTermPreview !== []): ?>
                            <div class="results-filters">
                                <div class="active-filters">
                                    <?php foreach ($queryTermPreview as $term): ?>
                                        <span class="active-filter"><strong>抽出語</strong><?php echo h((string)$term); ?></span>
                                    <?php endforeach; ?>
                                </div>
                            </div>
                        <?php endif; ?>

                        <ul class="result-list">
                            <?php foreach ($rows as $index => $row): ?>
                                <?php $active = $detail && (int)$detail['id'] === (int)$row['id']; ?>
                                <li>
                                    <a class="result-card<?php echo $active ? ' active' : ''; ?>" href="<?php echo h(query_with(['doc' => (string)$row['id'], 'tab' => 'viewer', 'viewer_tab' => $q !== '' ? 'matches' : 'transcript'])); ?>#viewer">
                                        <div class="result-top">
                                            <div class="result-rank"><?php echo h((string)($offset + $index + 1)); ?></div>
                                            <div class="badges">
                                                <?php if ($active): ?><span class="badge badge-current">表示中</span><?php endif; ?>
                                                <?php if (!empty($row['held_on'])): ?><span class="badge"><?php echo h((string)$row['held_on']); ?></span><?php endif; ?>
                                                <span class="badge"><?php echo h((string)$row['year_label']); ?></span>
                                            </div>
                                        </div>
                                        <h3 class="result-title"><?php echo h((string)$row['title']); ?></h3>
                                        <div class="result-sub">
                                            <?php if (!empty($row['meeting_name'])): ?>
                                                <div class="result-meta-row">
                                                    <span class="result-meta-label">会議</span>
                                                    <span class="result-meta-value"><?php echo h((string)$row['meeting_name']); ?></span>
                                                </div>
                                            <?php endif; ?>
                                            <div class="result-meta-row">
                                                <span class="result-meta-label">ファイル</span>
                                                <span class="result-meta-value"><?php echo h((string)$row['rel_path']); ?></span>
                                            </div>
                                        </div>
                                        <div class="excerpt-wrap">
                                            <span class="excerpt-label"><?php echo $q !== '' ? '一致プレビュー' : '本文冒頭'; ?></span>
                                            <div class="excerpt"><?php echo render_excerpt((string)$row['excerpt']); ?></div>
                                        </div>
                                        <div class="result-foot"><?php echo $q !== '' ? '会議録詳細タブで開く' : '会議録詳細を開く'; ?></div>
                                    </a>
                                </li>
                            <?php endforeach; ?>

                            <?php if (empty($rows) && $error === ''): ?>
                                <li><div class="viewer-empty"><div><strong>条件に一致する会議録がありません。</strong><span>キーワードか年度条件を少し広げて再検索してください。</span></div></div></li>
                            <?php endif; ?>
                        </ul>

                        <div class="pager">
                            <?php if ($page > 1): ?><a href="<?php echo h(query_with(['page' => $page - 1, 'tab' => 'results'])); ?>">前へ</a><?php endif; ?>
                            <span><?php echo h((string)$page); ?> / <?php echo h((string)$totalPages); ?></span>
                            <?php if ($page < $totalPages): ?><a href="<?php echo h(query_with(['page' => $page + 1, 'tab' => 'results'])); ?>">次へ</a><?php endif; ?>
                        </div>
                    </section>
                </div>

                <div id="workspace-viewer-panel" class="tab-panel workspace-panel" data-tab-panel="viewer" <?php if ($workspaceTab !== 'viewer'): ?>hidden<?php endif; ?>>
                    <section class="viewer" id="viewer">
                        <span class="anchor-alias" id="viewe" aria-hidden="true"></span>
                        <?php if ($detail && $detailDocument): ?>
                            <div class="viewer-head">
                                <div>
                                    <div class="viewer-kicker">
                                        <span class="hero-tag">選択中の会議録</span>
                                        <?php if (!empty($detail['held_on'])): ?><span class="hero-tag"><?php echo h((string)$detail['held_on']); ?></span><?php endif; ?>
                                        <?php if ($detailMatches !== []): ?><span class="hero-tag">一致 <?php echo h((string)count($detailMatches)); ?>件</span><?php endif; ?>
                                    </div>
                                    <h2 class="section-title viewer-title"><?php echo h((string)$detail['title']); ?></h2>
                                    <div class="viewer-meta">
                                        <?php if (!empty($detail['meeting_name'])): ?><?php echo h((string)$detail['meeting_name']); ?> / <?php endif; ?>
                                        年度 <?php echo h((string)$detail['year_label']); ?>
                                        <?php if ($detailMatches !== []): ?> / 一致発言 <?php echo h((string)count($detailMatches)); ?>件<?php endif; ?>
                                    </div>
                                </div>
                                <div class="viewer-links">
                                    <?php if (!empty($detail['source_url'])): ?><a href="<?php echo h((string)$detail['source_url']); ?>" target="_blank" rel="noopener noreferrer">元ページを開く</a><?php endif; ?>
                                    <a href="<?php echo h(query_with(['doc' => null, 'tab' => 'results', 'viewer_tab' => null])); ?>">選択を解除</a>
                                </div>
                            </div>

                            <div class="viewer-body" data-viewer-body="1" <?php if ($focusAnchor !== ''): ?>data-focus-target="<?php echo h((string)$focusAnchor); ?>"<?php endif; ?>>
                                <div class="viewer-tabs" data-tab-group="viewer" data-default-tab="<?php echo h($viewerTab); ?>">
                                    <div class="tab-list" role="tablist" aria-label="会議録表示切り替え">
                                        <button type="button" id="viewer-transcript-tab" class="tab-button<?php echo $viewerTab === 'transcript' ? ' is-active' : ''; ?>" data-tab-button="transcript" aria-controls="viewer-transcript-panel" aria-selected="<?php echo $viewerTab === 'transcript' ? 'true' : 'false'; ?>">本文</button>
                                        <button type="button" id="viewer-summary-tab" class="tab-button<?php echo $viewerTab === 'summary' ? ' is-active' : ''; ?>" data-tab-button="summary" aria-controls="viewer-summary-panel" aria-selected="<?php echo $viewerTab === 'summary' ? 'true' : 'false'; ?>">概要</button>
                                        <?php if ($detailMatches !== []): ?><button type="button" id="viewer-matches-tab" class="tab-button<?php echo $viewerTab === 'matches' ? ' is-active' : ''; ?>" data-tab-button="matches" aria-controls="viewer-matches-panel" aria-selected="<?php echo $viewerTab === 'matches' ? 'true' : 'false'; ?>">一致発言 <span class="tab-count"><?php echo h((string)count($detailMatches)); ?></span></button><?php endif; ?>
                                    </div>

                                    <section id="viewer-transcript-panel" class="tab-panel viewer-section" data-tab-panel="transcript" <?php if ($viewerTab !== 'transcript'): ?>hidden<?php endif; ?>>
                                        <div class="transcript-intro">
                                            <strong>本文</strong>
                                            <?php echo $q !== '' ? '一致した発言は黄色で強調し、対象ブロックを枠でも目立たせています。' : '発言者ごとに本文を分割しているため、誰が何を話したかを追いやすくしています。'; ?>
                                        </div>

                                        <div class="transcript">
                                            <?php foreach ($detailDocument['blocks'] as $block): ?>
                                                <?php if (($block['type'] ?? '') === 'divider'): ?>
                                                    <hr class="transcript-divider">
                                                <?php elseif (($block['type'] ?? '') === 'speech'): ?>
                                                    <article id="<?php echo h((string)$block['anchor']); ?>" class="speech-card<?php echo ((int)($block['match_count'] ?? 0) > 0) ? ' is-match' : ''; ?>">
                                                        <div class="speech-aside">
                                                            <span class="speaker-mark"><?php echo h((string)$block['mark']); ?></span>
                                                            <div class="speaker-name"><?php echo h((string)$block['speaker']); ?></div>
                                                            <?php if (!empty($block['role'])): ?><div class="speaker-role"><?php echo h((string)$block['role']); ?></div><?php endif; ?>
                                                            <?php if ((int)($block['match_count'] ?? 0) > 0): ?><div class="match-badge">一致 <?php echo h((string)$block['match_count']); ?>件</div><?php endif; ?>
                                                        </div>
                                                        <div class="speech-content"><?php echo render_paragraphs((string)$block['body'], $queryTerms); ?></div>
                                                    </article>
                                                <?php else: ?>
                                                    <div id="<?php echo h((string)$block['anchor']); ?>" class="note-card <?php echo h((string)($block['kind'] ?? 'note')); ?><?php echo ((int)($block['match_count'] ?? 0) > 0) ? ' is-match' : ''; ?>">
                                                        <?php echo render_paragraphs((string)$block['body'], $queryTerms); ?>
                                                    </div>
                                                <?php endif; ?>
                                            <?php endforeach; ?>
                                        </div>
                                    </section>

                                    <section id="viewer-summary-panel" class="tab-panel viewer-section" data-tab-panel="summary" <?php if ($viewerTab !== 'summary'): ?>hidden<?php endif; ?>>
                                        <div class="viewer-stack">
                                            <div class="summary-card summary-card-accent">
                                                <h3 class="summary-title">閲覧ガイド</h3>
                                                <p class="summary-copy">本文・概要・一致発言を分けているので、必要な情報だけ切り替えて確認できます。</p>
                                            </div>

                                            <?php if ($detailFacts !== []): ?>
                                                <div class="summary-card">
                                                    <h3 class="summary-title">会議概要</h3>
                                                    <div class="meta-grid">
                                                        <?php foreach ($detailFacts as $item): ?>
                                                            <div class="meta-item">
                                                                <div class="meta-label"><?php echo h((string)$item['label']); ?></div>
                                                                <div class="meta-value"><?php echo h((string)$item['value']); ?></div>
                                                            </div>
                                                        <?php endforeach; ?>
                                                    </div>
                                                </div>
                                            <?php endif; ?>

                                            <?php if ($headerLines !== []): ?>
                                                <div class="summary-card">
                                                    <h3 class="summary-title">冒頭情報</h3>
                                                    <div class="header-lines">
                                                        <?php foreach ($headerLines as $line): ?><div class="header-line"><?php echo h($line); ?></div><?php endforeach; ?>
                                                    </div>
                                                </div>
                                            <?php endif; ?>

                                            <?php if (!empty($detailDocument['preamble']['meta'])): ?>
                                                <div class="summary-card">
                                                    <h3 class="summary-title">出席者・会場など</h3>
                                                    <div class="meta-grid">
                                                        <?php foreach ($detailDocument['preamble']['meta'] as $item): ?>
                                                            <div class="meta-item">
                                                                <div class="meta-label"><?php echo h((string)$item['label']); ?></div>
                                                                <div class="meta-value"><?php echo h((string)$item['value']); ?></div>
                                                            </div>
                                                        <?php endforeach; ?>
                                                    </div>
                                                </div>
                                            <?php endif; ?>

                                            <?php if (!empty($detailDocument['preamble']['agenda'])): ?>
                                                <div class="summary-card">
                                                    <h3 class="summary-title">日程</h3>
                                                    <ul class="agenda-list">
                                                        <?php foreach ($detailDocument['preamble']['agenda'] as $line): ?><li><?php echo h((string)$line); ?></li><?php endforeach; ?>
                                                    </ul>
                                                </div>
                                            <?php endif; ?>
                                        </div>
                                    </section>

                                    <?php if ($detailMatches !== []): ?>
                                        <section id="viewer-matches-panel" class="tab-panel viewer-section" data-tab-panel="matches" <?php if ($viewerTab !== 'matches'): ?>hidden<?php endif; ?>>
                                            <div class="summary-card summary-card-accent">
                                                <h3 class="summary-title">一致した発言</h3>
                                                <p class="summary-copy">一致した発言を一覧しています。クリックすると本文タブへ切り替えて該当位置へ移動します。</p>
                                            </div>
                                            <div class="match-list">
                                                <?php foreach ($detailMatches as $match): ?>
                                                    <a class="match-link" href="#<?php echo h((string)$match['anchor']); ?>" data-jump-to="<?php echo h((string)$match['anchor']); ?>" data-open-tab="transcript">
                                                        <strong><?php echo h((string)$match['label']); ?><?php if ((int)$match['count'] > 1): ?> / <?php echo h((string)$match['count']); ?>件<?php endif; ?></strong>
                                                        <span><?php echo render_inline_highlighted((string)$match['preview'], $queryTerms); ?></span>
                                                    </a>
                                                <?php endforeach; ?>
                                            </div>
                                        </section>
                                    <?php endif; ?>
                                </div>
                            </div>
                        <?php else: ?>
                            <div class="viewer-empty"><div><strong>会議録を選ぶと詳細を表示します。</strong><span>検索結果タブから会議録を選ぶと、ここに本文と概要が表示されます。</span></div></div>
                        <?php endif; ?>
                    </section>
                </div>
            </section>
        </div>
    </section>
</div>
<script>
    document.addEventListener('DOMContentLoaded', function () {
        var groupNodes = function (group, attribute) {
            return Array.prototype.filter.call(group.querySelectorAll('[' + attribute + ']'), function (node) {
                return node.closest('[data-tab-group]') === group;
            });
        };

        var activateTabGroup = function (group, tabName) {
            var found = false;
            groupNodes(group, 'data-tab-button').forEach(function (button) {
                var active = button.getAttribute('data-tab-button') === tabName;
                button.classList.toggle('is-active', active);
                button.setAttribute('aria-selected', active ? 'true' : 'false');
                found = found || active;
            });
            groupNodes(group, 'data-tab-panel').forEach(function (panel) {
                var active = panel.getAttribute('data-tab-panel') === tabName;
                panel.hidden = !active;
            });
            if (found) {
                group.setAttribute('data-default-tab', tabName);
            }
            return found;
        };

        document.querySelectorAll('[data-tab-group]').forEach(function (group) {
            group.__activateTab = function (tabName) {
                return activateTabGroup(group, tabName);
            };

            groupNodes(group, 'data-tab-button').forEach(function (button) {
                button.addEventListener('click', function () {
                    activateTabGroup(group, button.getAttribute('data-tab-button'));
                });
            });

            var initialTab = group.getAttribute('data-default-tab');
            if (!initialTab) {
                var firstButton = groupNodes(group, 'data-tab-button')[0] || null;
                initialTab = firstButton ? firstButton.getAttribute('data-tab-button') : '';
            }
            if (initialTab) {
                activateTabGroup(group, initialTab);
            }
        });

        var viewerBody = document.querySelector('[data-viewer-body]');
        if (!viewerBody) {
            return;
        }

        var viewerTabs = document.querySelector('[data-tab-group="viewer"]');
        var jumpToBlock = function (anchor, behavior) {
            var target = document.getElementById(anchor);
            if (!target) {
                return;
            }

            if (viewerTabs && typeof viewerTabs.__activateTab === 'function') {
                viewerTabs.__activateTab('transcript');
            }

            viewerBody.querySelectorAll('.is-target').forEach(function (node) {
                node.classList.remove('is-target');
            });
            target.classList.add('is-target');
            target.scrollIntoView({ behavior: behavior, block: 'start' });
        };

        document.querySelectorAll('[data-jump-to]').forEach(function (link) {
            link.addEventListener('click', function (event) {
                event.preventDefault();
                var openTab = link.getAttribute('data-open-tab');
                if (openTab && viewerTabs && typeof viewerTabs.__activateTab === 'function') {
                    viewerTabs.__activateTab(openTab);
                }
                window.setTimeout(function () {
                    jumpToBlock(link.getAttribute('data-jump-to'), 'smooth');
                }, 20);
            });
        });

        var initial = viewerBody.getAttribute('data-focus-target');
        if (initial) {
            window.setTimeout(function () {
                jumpToBlock(initial, 'auto');
            }, 40);
        }
    });
</script>
</body>
</html>
