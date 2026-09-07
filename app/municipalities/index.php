<?php
declare(strict_types=1);

// 自治体一覧。会議録・例規集・事務事業評価の**取得元（自治体サイト）**へ
// 直接つなぐ表を、地方・都道府県ごとに並べる。
//
// トップページの地図は「こちらで何を持っているか」を見せる画面で、こちらは
// 「自治体自身がどこに出しているか」を見せる画面。原典へ 1 手で行けるように
// しておくと、こちらの収録を疑ったときに読者が自分で確かめられる。

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'municipalities.php';
require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'site_assets.php';

function municipalities_h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function municipalities_asset_url(string $relativePath): string
{
    $normalized = trim(str_replace('\\', '/', $relativePath), '/');
    $publicPath = '/assets/' . $normalized;
    $diskPath = dirname(__DIR__) . DIRECTORY_SEPARATOR . 'assets' . DIRECTORY_SEPARATOR
        . str_replace('/', DIRECTORY_SEPARATOR, $normalized);
    $version = is_file($diskPath) ? (string)filemtime($diskPath) : '';
    return $version !== '' ? $publicPath . '?v=' . rawurlencode($version) : $publicPath;
}

/** 取得元のセル 1 つ分。URL が無い理由も短く見せる。 */
function municipalities_source_cell(array $entry): string
{
    $url = trim((string)($entry['url'] ?? ''));
    $status = trim((string)($entry['crawl_status'] ?? ''));
    $reason = trim((string)($entry['exclusion_reason'] ?? ''));
    if ($url === '') {
        $label = match ($reason) {
            'not_published' => '公開なし',
            'video_only' => '映像のみ',
            'login_required' => '要ログイン',
            'source_url_unresolved', '' => '未特定',
            default => '未特定',
        };
        return '<span class="source-none">' . municipalities_h($label) . '</span>';
    }

    $host = (string)(parse_url($url, PHP_URL_HOST) ?: '');
    $note = '';
    if ($status === 'review_required') {
        $note = '<span class="source-note" title="入口は見つかったが、中身の確認が済んでいない">要確認</span>';
    } elseif ($status === 'excluded') {
        $note = '<span class="source-note">取得対象外</span>';
    }
    return '<a class="source-link" href="' . municipalities_h($url) . '" rel="noopener nofollow"'
        . ' target="_blank" title="' . municipalities_h($url) . '">'
        . municipalities_h($host !== '' ? $host : $url) . '</a>' . $note;
}

$master = load_municipality_master_index();
$prefectureNames = municipality_prefecture_names();
$regionNames = municipality_region_names();
$sources = [
    'gijiroku' => load_system_url_index('municipalities/assembly_minutes_system_urls.tsv'),
    'reiki' => load_system_url_index('municipalities/reiki_system_urls.tsv'),
    'hyoka' => load_system_url_index('municipalities/hyoka_system_urls.tsv'),
];

// 地方 → 都道府県 → 自治体（コード順）に積む。master は既にコード順だが、
// 読み込み順に頼らず並べ直す。
$tree = [];
$counts = ['total' => 0, 'gijiroku' => 0, 'reiki' => 0, 'hyoka' => 0];
// PHP は「13101」のような数字だけのキーを int にする。5 桁へ戻してから扱う。
// 「01000」は先頭の 0 で文字列のまま残るので、混ざったまま比べると並びが崩れる。
$codes = array_map(
    static fn ($code): string => str_pad((string)$code, 5, '0', STR_PAD_LEFT),
    array_keys($master)
);
sort($codes, SORT_STRING);
foreach ($codes as $code) {
    $prefCode = municipality_prefecture_code_from_code($code);
    $region = municipality_region_for_prefecture($prefCode);
    if ($region === '' || !isset($prefectureNames[$prefCode])) {
        continue;
    }
    $tree[$region][$prefCode][] = $code;
    $counts['total'] += 1;
    foreach (['gijiroku', 'reiki', 'hyoka'] as $key) {
        if (trim((string)($sources[$key][$code]['url'] ?? '')) !== '') {
            $counts[$key] += 1;
        }
    }
}

$generatedAt = app_now_tokyo('Y-m-d');
?><!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>自治体一覧｜自治体マップ</title>
    <?php echo site_render_page_meta(
        '自治体一覧｜自治体マップ',
        '全国1,700超の自治体について、会議録・例規集・事務事業評価を自治体自身がどこで公開しているかを、地方別・都道府県別の表にまとめています。自治体コード順。原典サイトへ直接たどれます。',
        '/municipalities/'
    ); ?>
    <?php echo site_render_favicon_links(); ?>
    <link rel="stylesheet" href="<?php echo municipalities_h(municipalities_asset_url('css/home.css')); ?>">
    <link rel="stylesheet" href="<?php echo municipalities_h(municipalities_asset_url('css/municipalities.css')); ?>">
</head>
<body>
    <div class="shell">
        <header class="status-masthead">
            <?php echo site_render_brand('/'); ?>
            <nav aria-label="関連ページ">
                <a href="/">地図から探す</a>
                <a href="/search/">記録を検索</a>
                <a href="/status/">収集・公開状況</a>
                <a href="/api-guide/">AIから使う（MCP）</a>
            </nav>
        </header>

        <section class="hero">
            <div class="eyebrow">原典への入口</div>
            <h1>自治体一覧</h1>
            <div class="hero-copy">
                会議録・例規集・事務事業評価を、<strong>自治体自身がどこで公開しているか</strong>の一覧です。
                リンク先はこちらの検索ではなく、自治体のサイトです。
                こちらの収録を確かめたいときは、原典を直接ご覧ください。
                検索は<a href="/search/">記録を検索</a>、収録の進み具合は<a href="/status/">収集・公開状況</a>にあります。
            </div>
            <div class="hero-meta">
                <span>自治体 <?php echo number_format($counts['total']); ?></span>
                <span>会議録 <?php echo number_format($counts['gijiroku']); ?></span>
                <span>例規集 <?php echo number_format($counts['reiki']); ?></span>
                <span>事務事業評価 <?php echo number_format($counts['hyoka']); ?></span>
                <span>更新 <?php echo municipalities_h($generatedAt); ?></span>
            </div>
        </section>

        <nav class="region-index" aria-label="地方から探す">
            <span class="region-index-label">地方から探す</span>
            <?php foreach ($regionNames as $regionKey => $regionLabel): ?>
                <?php if (!isset($tree[$regionKey])) { continue; } ?>
                <a href="#region-<?php echo municipalities_h($regionKey); ?>"><?php echo municipalities_h($regionLabel); ?></a>
            <?php endforeach; ?>
        </nav>

        <p class="list-note">
            「未特定」は、この調査手順で公開先を確定できなかったという意味です。
            公開していないと決まったわけではありません。事務事業評価の「要確認」は、
            評価の入口は見つかったが中身の確認が済んでいないものです。
            調べ方は<a href="/status/">収集・公開状況</a>と各調査文書に書いています。
        </p>

        <main>
        <?php foreach ($regionNames as $regionKey => $regionLabel): ?>
            <?php if (!isset($tree[$regionKey])) { continue; } ?>
            <section class="region" id="region-<?php echo municipalities_h($regionKey); ?>">
                <h2 class="region-heading"><?php echo municipalities_h($regionLabel); ?></h2>
                <?php foreach ($tree[$regionKey] as $prefKey => $codesInPref): ?>
                    <?php // 「13」のような数字だけのキーは int になる。2 桁へ戻す。
                        $prefCode = str_pad((string)$prefKey, 2, '0', STR_PAD_LEFT); ?>
                    <section class="prefecture" id="pref-<?php echo municipalities_h($prefCode); ?>">
                        <h3 class="prefecture-heading">
                            <?php echo municipalities_h($prefectureNames[$prefCode]); ?>
                            <span class="prefecture-count"><?php echo count($codesInPref); ?>件</span>
                        </h3>
                        <div class="table-scroll">
                        <table class="municipality-table">
                            <caption class="visually-hidden">
                                <?php echo municipalities_h($prefectureNames[$prefCode]); ?>の自治体と、会議録・例規集・事務事業評価の公開先
                            </caption>
                            <thead>
                                <tr>
                                    <th scope="col" class="col-code">コード</th>
                                    <th scope="col" class="col-name">自治体</th>
                                    <th scope="col">会議録</th>
                                    <th scope="col">例規集</th>
                                    <th scope="col">事務事業評価</th>
                                </tr>
                            </thead>
                            <tbody>
                            <?php foreach ($codesInPref as $code): ?>
                                <tr>
                                    <td class="col-code"><?php echo municipalities_h($code); ?></td>
                                    <th scope="row" class="col-name"><?php echo municipalities_h((string)($master[$code]['name'] ?? '')); ?></th>
                                    <td><?php echo municipalities_source_cell($sources['gijiroku'][$code] ?? []); ?></td>
                                    <td><?php echo municipalities_source_cell($sources['reiki'][$code] ?? []); ?></td>
                                    <td><?php echo municipalities_source_cell($sources['hyoka'][$code] ?? []); ?></td>
                                </tr>
                            <?php endforeach; ?>
                            </tbody>
                        </table>
                        </div>
                    </section>
                <?php endforeach; ?>
            </section>
        <?php endforeach; ?>
        </main>

        <footer class="status-footer">
            <a href="/">地図から探す</a>
            <a href="/search/">記録を検索</a>
            <a href="/status/">収集・公開状況</a>
            <a href="/privacy/">プライバシー</a>
            <a href="/terms/">利用規約</a>
        </footer>
    </div>
</body>
</html>
