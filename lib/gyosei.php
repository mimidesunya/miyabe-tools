<?php
declare(strict_types=1);

// 行政文書（情報公開請求などで得た文書）の公開用の束を読む。
// 束は tools/gyosei/build_public.py が作り、deploy/sync_gyosei.py が本番の data/gyosei/ へ送る。
//
//     data/gyosei/<案件のid>/case.json   案件の情報と文書の一覧
//     data/gyosei/<案件のid>/<nn>.pdf    画像にして作り直した PDF（nginx が /data/gyosei/... で出す）
//     data/gyosei/<案件のid>/<nn>.json   頁ごとの文字起こし（nginx は出さない。PHP が読む）

const GYOSEI_ID_PATTERN = '/^[a-z0-9][a-z0-9-]*$/';
const GYOSEI_DOC_PATTERN = '/^[0-9]{2,3}$/';

function gyosei_data_dir(): string
{
    $configured = trim((string)getenv('MIYABE_GYOSEI_DIR'));
    return $configured !== '' ? rtrim($configured, '/\\') : dirname(__DIR__) . DIRECTORY_SEPARATOR . 'data' . DIRECTORY_SEPARATOR . 'gyosei';
}

/** @return array<string,mixed>|null */
function gyosei_read_json(string $path): ?array
{
    if (!is_file($path)) {
        return null;
    }
    $data = json_decode((string)file_get_contents($path), true);
    return is_array($data) ? $data : null;
}

/** @return list<array<string,mixed>> 請求日の新しい順 */
function gyosei_cases(): array
{
    $cases = [];
    foreach (glob(gyosei_data_dir() . DIRECTORY_SEPARATOR . '*' . DIRECTORY_SEPARATOR . 'case.json') ?: [] as $path) {
        $case = gyosei_read_json($path);
        if ($case !== null && preg_match(GYOSEI_ID_PATTERN, (string)($case['id'] ?? '')) === 1) {
            $cases[] = $case;
        }
    }
    usort($cases, static fn(array $a, array $b): int => strcmp((string)($b['requested_on'] ?? ''), (string)($a['requested_on'] ?? '')));
    return $cases;
}

/** @return array<string,mixed>|null */
function gyosei_case(string $id): ?array
{
    if (preg_match(GYOSEI_ID_PATTERN, $id) !== 1) {
        return null;
    }
    $case = gyosei_read_json(gyosei_data_dir() . DIRECTORY_SEPARATOR . $id . DIRECTORY_SEPARATOR . 'case.json');
    return $case !== null && ($case['id'] ?? '') === $id ? $case : null;
}

/** @return array<string,mixed>|null 案件の documents のうち file が <doc>.pdf のもの */
function gyosei_document(array $case, string $doc): ?array
{
    if (preg_match(GYOSEI_DOC_PATTERN, $doc) !== 1) {
        return null;
    }
    foreach ((array)($case['documents'] ?? []) as $document) {
        if (is_array($document) && ($document['file'] ?? '') === $doc . '.pdf') {
            return $document;
        }
    }
    return null;
}

/** @return list<string> 頁ごとの文字起こし（Markdown） */
function gyosei_document_pages(array $case, array $document): array
{
    $text = (string)($document['text'] ?? '');
    if (preg_match('/^[0-9]{2,3}\.json$/', $text) !== 1) {
        return [];
    }
    $data = gyosei_read_json(gyosei_data_dir() . DIRECTORY_SEPARATOR . (string)$case['id'] . DIRECTORY_SEPARATOR . $text);
    return array_values(array_map('strval', (array)($data['pages'] ?? [])));
}

function gyosei_pdf_url(array $case, array $document): string
{
    return '/data/gyosei/' . rawurlencode((string)$case['id']) . '/' . rawurlencode((string)$document['file']);
}

function gyosei_doc_id(array $document): string
{
    return basename((string)($document['file'] ?? ''), '.pdf');
}

/** 文書の名前。「部分公開_田島支所」→「部分公開（田島支所）」のように、補足の 2 つ目からを括弧に入れる。 */
function gyosei_document_label(array $document): string
{
    $detail = (string)($document['detail'] ?? '');
    if ($detail === '') {
        return (string)($document['kind'] ?? '');
    }
    $parts = explode('_', $detail);
    $head = array_shift($parts);
    return $parts === [] ? $head : $head . '（' . implode('・', $parts) . '）';
}

function gyosei_date_label(string $date): string
{
    if (preg_match('/^(\d{4})-(\d{2})-(\d{2})$/', $date, $m) !== 1) {
        return $date;
    }
    if ($m[2] === '00') {
        return $m[1] . '年';
    }
    if ($m[3] === '00') {
        return $m[1] . '年' . (int)$m[2] . '月';
    }
    return $m[1] . '年' . (int)$m[2] . '月' . (int)$m[3] . '日';
}

function gyosei_bytes_label(int $bytes): string
{
    if ($bytes >= 1000000) {
        return number_format($bytes / 1000000, 1) . 'MB';
    }
    return max(1, (int)round($bytes / 1000)) . 'KB';
}

/**
 * 文字起こしの Markdown を HTML にする。mimi-ocr の書き方（見出し・表・太字・`### --右` の寄せの印）だけを扱う。
 * 中身はすべてエスケープし、表の中の <br> だけを改行に戻す。
 */
function gyosei_render_ocr(string $markdown): string
{
    $h = static fn(string $value): string => htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
    $inline = static function (string $value) use ($h): string {
        $html = $h(trim($value));
        $html = preg_replace('/&lt;br\s*\/?&gt;/i', '<br>', $html) ?? $html;
        return preg_replace('/\*\*(.+?)\*\*/u', '<strong>$1</strong>', $html) ?? $html;
    };
    $out = [];
    $paragraph = [];
    $table = [];
    $flushParagraph = static function () use (&$paragraph, &$out): void {
        if ($paragraph !== []) {
            $out[] = '<p>' . implode('<br>', $paragraph) . '</p>';
            $paragraph = [];
        }
    };
    $flushTable = static function () use (&$table, &$out, $inline): void {
        if ($table === []) {
            return;
        }
        $rows = '';
        foreach ($table as $line) {
            $cells = explode('|', trim(trim($line), '|'));
            if (preg_match('/^\s*:?-{2,}:?\s*$/', $cells[0] ?? '') === 1) {
                continue; // 見出しの下の区切り
            }
            $rows .= '<tr>' . implode('', array_map(static fn(string $cell): string => '<td>' . $inline($cell) . '</td>', $cells)) . '</tr>';
        }
        $out[] = '<div class="table-scroll"><table class="ocr-table">' . $rows . '</table></div>';
        $table = [];
    };
    foreach (preg_split('/\R/u', $markdown) ?: [] as $line) {
        if (preg_match('/^###\s*--/u', $line) === 1) {
            continue; // 寄せ（右・左）の印
        }
        if (str_starts_with(ltrim($line), '|')) {
            $flushParagraph();
            $table[] = $line;
            continue;
        }
        $flushTable();
        if (trim($line) === '') {
            $flushParagraph();
            continue;
        }
        if (preg_match('/^#{1,6}\s+(.*)$/u', $line, $m) === 1) {
            $flushParagraph();
            $out[] = '<p class="ocr-heading">' . $inline($m[1]) . '</p>';
            continue;
        }
        $paragraph[] = $inline($line);
    }
    $flushTable();
    $flushParagraph();
    return implode("\n", $out);
}
