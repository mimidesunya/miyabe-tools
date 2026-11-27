<?php
declare(strict_types=1);

// 行政文書（情報公開請求などで得た文書）の置き場。
//   /gyosei/                     案件の一覧
//   /gyosei/?case=<id>           案件（請求の中身・決定・文書の一覧）
//   /gyosei/?case=<id>&doc=<nn>  文書の文字起こし（頁ごと）と PDF
// 決まりは doc/gyosei.md。まだナビにもトップにも出さず、検索エンジンにも載せない（GYOSEI_NOINDEX）。

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'site_assets.php';
require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'gyosei.php';

const GYOSEI_NOINDEX = true;

function gyosei_h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function gyosei_case_url(array $case): string
{
    return '/gyosei/?case=' . rawurlencode((string)$case['id']);
}

function gyosei_doc_url(array $case, array $document): string
{
    return gyosei_case_url($case) . '&doc=' . rawurlencode(gyosei_doc_id($document));
}

function gyosei_download_name(array $case, array $document): string
{
    return (string)($case['agency'] ?? '') . '_' . (string)($case['title'] ?? '') . '_' . (string)($document['name'] ?? '') . '.pdf';
}

$caseId = trim((string)($_GET['case'] ?? ''));
$docId = trim((string)($_GET['doc'] ?? ''));
$case = $caseId !== '' ? gyosei_case($caseId) : null;
$document = $case !== null && $docId !== '' ? gyosei_document($case, $docId) : null;
$notFound = ($caseId !== '' && $case === null) || ($docId !== '' && $document === null);
if ($notFound) {
    http_response_code(404);
}

if ($notFound) {
    $title = '見つかりません｜行政文書｜自治体マップ';
    $description = 'お探しの行政文書は見つかりませんでした。';
    $canonical = '/gyosei/';
} elseif ($document !== null) {
    $title = gyosei_document_label($document) . '｜' . $case['agency'] . ' ' . $case['title'] . '｜行政文書｜自治体マップ';
    $description = $case['agency'] . 'の「' . $case['title'] . '」の情報公開請求で開示された' . $document['kind'] . '（' . $document['pages'] . '頁）の画像と文字。';
    $canonical = gyosei_doc_url($case, $document);
} elseif ($case !== null) {
    $title = $case['agency'] . ' ' . $case['title'] . '｜行政文書｜自治体マップ';
    $description = (string)($case['summary'] ?? '') !== '' ? (string)$case['summary'] : $case['agency'] . 'への情報公開請求で開示された文書。';
    $canonical = gyosei_case_url($case);
} else {
    $title = '行政文書｜自治体マップ';
    $description = '情報公開請求などで手に入れた自治体の行政文書を、PDF と文字で読めるようにしています。';
    $canonical = '/gyosei/';
}
?><!doctype html>
<html lang="ja">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title><?php echo gyosei_h($title); ?></title>
    <?php if (GYOSEI_NOINDEX): ?><meta name="robots" content="noindex, nofollow">
    <?php endif; ?>
<?php echo site_render_page_meta($title, $description, $canonical); ?>
    <?php echo site_render_favicon_links(); ?>
    <?php echo site_render_stylesheet(); ?>
    <link rel="stylesheet" href="<?php echo gyosei_h(site_asset_url('gyosei/gyosei.css')); ?>">
</head>
<body>
<?php echo site_render_header(''); ?>

<main id="main" class="gyosei-page">
<?php if ($notFound): ?>
    <nav class="gyosei-trail" aria-label="現在地"><a href="/gyosei/">行政文書</a></nav>
    <h1>見つかりません</h1>
    <p>この文書は置いていないか、場所が変わりました。<a href="/gyosei/">行政文書の一覧</a>から探してください。</p>

<?php elseif ($document !== null): ?>
<?php
    $pages = gyosei_document_pages($case, $document);
    $pdfUrl = gyosei_pdf_url($case, $document);
    $documents = array_values((array)$case['documents']);
    $position = array_search($document, $documents, true);
    $previous = $position !== false && $position > 0 ? $documents[$position - 1] : null;
    $next = $position !== false && $position + 1 < count($documents) ? $documents[$position + 1] : null;
?>
    <nav class="gyosei-trail" aria-label="現在地">
        <a href="/gyosei/">行政文書</a>
        <span aria-hidden="true">›</span>
        <a href="<?php echo gyosei_h(gyosei_case_url($case)); ?>"><?php echo gyosei_h($case['agency'] . ' ' . $case['title']); ?></a>
    </nav>
    <header class="gyosei-head">
        <p class="gyosei-kind"><?php echo gyosei_h($document['kind']); ?></p>
        <h1><?php echo gyosei_h(gyosei_document_label($document)); ?></h1>
        <p class="gyosei-meta"><?php echo gyosei_h(gyosei_date_label((string)$document['date'])); ?><?php echo $document['kind'] === '開示文書' ? 'に開示' : ''; ?>
            ／ <?php echo (int)$document['pages']; ?>頁 ／ PDF <?php echo gyosei_h(gyosei_bytes_label((int)$document['bytes'])); ?></p>
        <p class="gyosei-actions">
            <a class="button" href="<?php echo gyosei_h($pdfUrl); ?>" download="<?php echo gyosei_h(gyosei_download_name($case, $document)); ?>">PDF を保存する</a>
            <a class="button-quiet" href="<?php echo gyosei_h($pdfUrl); ?>" target="_blank" rel="noopener">PDF を開く</a>
        </p>
        <p class="note">下の文字は画像から機械で読み取ったもので、誤りがあります。引くときは PDF の画像で確かめてください。黒塗りの部分は読んでいません。</p>
    </header>

    <ol class="gyosei-pages">
<?php foreach ($pages as $index => $markdown): $number = $index + 1; ?>
        <li class="gyosei-page-text" id="p<?php echo $number; ?>">
            <div class="gyosei-page-head">
                <h2><?php echo $number; ?>頁</h2>
                <a href="<?php echo gyosei_h($pdfUrl . '#page=' . $number); ?>" target="_blank" rel="noopener">画像で見る</a>
            </div>
<?php if (trim($markdown) === ''): ?>
            <p class="gyosei-empty">読み取れる文字がありません（頁の全体が黒塗りのものなど）。</p>
<?php else: ?>
            <div class="gyosei-ocr"><?php echo gyosei_render_ocr($markdown); ?></div>
<?php endif; ?>
        </li>
<?php endforeach; ?>
    </ol>
<?php if ($pages === []): ?>
    <p class="gyosei-empty">この文書の文字はまだ読み取っていません。PDF で読んでください。</p>
<?php endif; ?>

    <nav class="gyosei-pager" aria-label="同じ案件のほかの文書">
<?php if ($previous !== null): ?>
        <a href="<?php echo gyosei_h(gyosei_doc_url($case, $previous)); ?>"><span>前の文書</span><?php echo gyosei_h(gyosei_document_label($previous)); ?></a>
<?php endif; ?>
<?php if ($next !== null): ?>
        <a class="gyosei-pager-next" href="<?php echo gyosei_h(gyosei_doc_url($case, $next)); ?>"><span>次の文書</span><?php echo gyosei_h(gyosei_document_label($next)); ?></a>
<?php endif; ?>
    </nav>

<?php elseif ($case !== null): ?>
<?php
    $groups = [];
    foreach ((array)$case['documents'] as $item) {
        $groups[(string)$item['kind']][] = $item;
    }
    $totalPages = array_sum(array_map(static fn(array $item): int => (int)$item['pages'], (array)$case['documents']));
?>
    <nav class="gyosei-trail" aria-label="現在地"><a href="/gyosei/">行政文書</a></nav>
    <header class="gyosei-head">
        <p class="gyosei-kind"><?php echo gyosei_h($case['agency']); ?></p>
        <h1><?php echo gyosei_h($case['title']); ?></h1>
<?php if ((string)($case['summary'] ?? '') !== ''): ?>
        <p class="gyosei-summary"><?php echo gyosei_h((string)$case['summary']); ?></p>
<?php endif; ?>
    </header>

    <dl class="gyosei-facts">
        <div><dt>請求した日</dt><dd><?php echo gyosei_h(gyosei_date_label((string)($case['requested_on'] ?? ''))); ?></dd></div>
        <div><dt>請求の種類</dt><dd><?php echo gyosei_h((string)($case['request_kind'] ?? $case['route'] ?? '')); ?></dd></div>
        <div><dt>請求先</dt><dd><?php echo gyosei_h((string)($case['body'] ?? $case['agency'])); ?></dd></div>
<?php if ((string)($case['legal_basis'] ?? '') !== ''): ?>
        <div><dt>根拠</dt><dd><?php echo gyosei_h((string)$case['legal_basis']); ?></dd></div>
<?php endif; ?>
<?php if ((string)($case['request_text'] ?? '') !== ''): ?>
        <div class="gyosei-facts-wide"><dt>請求した文書</dt><dd><blockquote><?php echo gyosei_h((string)$case['request_text']); ?></blockquote></dd></div>
<?php endif; ?>
    </dl>

<?php if ((array)($case['decisions'] ?? []) !== []): ?>
    <section class="gyosei-section">
        <h2>決定</h2>
        <div class="table-scroll">
            <table class="gyosei-table">
                <thead><tr><th scope="col">日付</th><th scope="col">番号</th><th scope="col">結果</th><th scope="col">出さなかった部分</th></tr></thead>
                <tbody>
<?php foreach ((array)$case['decisions'] as $decision): ?>
                    <tr>
                        <td class="nowrap"><?php echo gyosei_h(gyosei_date_label((string)($decision['date'] ?? ''))); ?></td>
                        <td class="nowrap"><?php echo gyosei_h((string)($decision['number'] ?? '')); ?></td>
                        <td class="nowrap"><?php echo gyosei_h((string)($decision['result'] ?? '')); ?></td>
                        <td><?php echo gyosei_h((string)($decision['withheld_reason'] ?? '')); ?></td>
                    </tr>
<?php endforeach; ?>
                </tbody>
            </table>
        </div>
    </section>
<?php endif; ?>

    <section class="gyosei-section">
        <h2>文書</h2>
        <p class="note"><?php echo count((array)$case['documents']); ?>件・<?php echo $totalPages; ?>頁。「読む」で頁ごとの文字と画像、「PDF」で画像の PDF を開きます。</p>
<?php foreach ($groups as $kind => $items): ?>
        <h3><?php echo gyosei_h($kind); ?></h3>
        <ul class="gyosei-docs">
<?php foreach ($items as $item): ?>
            <li>
                <a class="gyosei-doc-title" href="<?php echo gyosei_h(gyosei_doc_url($case, $item)); ?>"><?php echo gyosei_h(gyosei_document_label($item)); ?></a>
                <span class="gyosei-doc-meta"><?php echo (int)$item['pages']; ?>頁 ／ <?php echo gyosei_h(gyosei_bytes_label((int)$item['bytes'])); ?></span>
                <span class="gyosei-doc-links">
                    <a href="<?php echo gyosei_h(gyosei_doc_url($case, $item)); ?>">読む</a>
                    <a href="<?php echo gyosei_h(gyosei_pdf_url($case, $item)); ?>" target="_blank" rel="noopener">PDF</a>
                </span>
            </li>
<?php endforeach; ?>
        </ul>
<?php endforeach; ?>
    </section>

    <section class="gyosei-section">
        <h2>載せ方</h2>
        <ul class="gyosei-notes">
            <li>PDF は、交付された PDF の各頁を画像にして作り直したものです。黒塗りは交付されたときのままです。</li>
            <li>文字は画像から機械で読み取ったもので、誤りがあります。</li>
<?php foreach ((array)($case['omitted'] ?? []) as $omitted): ?>
            <li>「<?php echo gyosei_h((string)$omitted['name']); ?>」は載せていません。<?php echo gyosei_h((string)$omitted['reason']); ?>。</li>
<?php endforeach; ?>
        </ul>
    </section>

<?php else: ?>
<?php $cases = gyosei_cases(); ?>
    <header class="gyosei-head">
        <h1>行政文書</h1>
        <p class="gyosei-summary">情報公開請求などで手に入れた自治体の行政文書を置いています。PDF の画像と、頁ごとに読み取った文字で読めます。</p>
    </header>
<?php if ($cases === []): ?>
    <p class="gyosei-empty">まだ文書を置いていません。</p>
<?php else: ?>
    <ul class="gyosei-cases">
<?php foreach ($cases as $item):
        $pageCount = array_sum(array_map(static fn(array $doc): int => (int)$doc['pages'], (array)$item['documents'])); ?>
        <li>
            <p class="gyosei-kind"><?php echo gyosei_h((string)$item['agency']); ?></p>
            <h2><a href="<?php echo gyosei_h(gyosei_case_url($item)); ?>"><?php echo gyosei_h((string)$item['title']); ?></a></h2>
            <p class="gyosei-meta"><?php echo gyosei_h(gyosei_date_label((string)($item['requested_on'] ?? ''))); ?>の請求 ／ <?php echo count((array)$item['documents']); ?>件・<?php echo $pageCount; ?>頁</p>
<?php if ((string)($item['summary'] ?? '') !== ''): ?>
            <p><?php echo gyosei_h((string)$item['summary']); ?></p>
<?php endif; ?>
        </li>
<?php endforeach; ?>
    </ul>
<?php endif; ?>
<?php endif; ?>
</main>

<?php echo site_render_footer(); ?>
</body>
</html>
