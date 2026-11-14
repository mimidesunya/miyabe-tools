<?php
declare(strict_types=1);

// 公開ページ共通の軽量 asset helper。favicon のようなサイト共通資産を一元管理する。

function site_asset_disk_path(string $normalized): string
{
    $relative = str_replace('/', DIRECTORY_SEPARATOR, $normalized);
    $candidates = [];
    $documentRoot = (string)($_SERVER['DOCUMENT_ROOT'] ?? '');
    if ($documentRoot !== '') {
        $candidates[] = rtrim($documentRoot, '/\\') . DIRECTORY_SEPARATOR . $relative;
    }
    // ローカル開発はリポジトリ直下の app/、本番コンテナは /var/www/lib と並ぶ html/。
    $candidates[] = dirname(__DIR__) . DIRECTORY_SEPARATOR . 'app' . DIRECTORY_SEPARATOR . $relative;
    $candidates[] = dirname(__DIR__) . DIRECTORY_SEPARATOR . 'html' . DIRECTORY_SEPARATOR . $relative;
    foreach ($candidates as $candidate) {
        if (is_file($candidate)) {
            return $candidate;
        }
    }
    return '';
}

function site_asset_url(string $relativePath): string
{
    $normalized = trim(str_replace('\\', '/', $relativePath), '/');
    $publicPath = '/' . $normalized;
    // asset に filemtime を ?v= として付け、nginx の長期 immutable キャッシュと両立させる。
    // 解決に失敗して版なし URL を返すと更新がブラウザに届かなくなるので、候補は複数見る。
    $diskPath = site_asset_disk_path($normalized);
    $version = $diskPath !== '' ? (string)filemtime($diskPath) : '';
    return $version !== '' ? $publicPath . '?v=' . rawurlencode($version) : $publicPath;
}

function site_base_url(): string
{
    return 'https://tools.miya.be';
}

// description / canonical / OGP をまとめて出力する。$canonicalPath は「/search/」のような
// サイト内パス（必要ならクエリ付き）を渡す。
function site_render_page_meta(string $title, string $description, string $canonicalPath): string
{
    $h = static fn(string $value): string => htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
    $canonicalUrl = site_base_url() . $canonicalPath;
    $imageUrl = site_base_url() . '/assets/ken-dog.png';
    return '<meta name="description" content="' . $h($description) . '">' . "\n"
        . '    <link rel="canonical" href="' . $h($canonicalUrl) . '">' . "\n"
        . '    <meta property="og:site_name" content="自治体マップ">' . "\n"
        . '    <meta property="og:type" content="website">' . "\n"
        . '    <meta property="og:title" content="' . $h($title) . '">' . "\n"
        . '    <meta property="og:description" content="' . $h($description) . '">' . "\n"
        . '    <meta property="og:url" content="' . $h($canonicalUrl) . '">' . "\n"
        . '    <meta property="og:image" content="' . $h($imageUrl) . '">' . "\n"
        . '    <meta name="twitter:card" content="summary">';
}

function site_render_favicon_links(): string
{
    $faviconUrl = htmlspecialchars(site_asset_url('assets/ken-dog.png'), ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
    return '<link rel="icon" type="image/png" href="' . $faviconUrl . '">' . "\n"
        . '    <link rel="shortcut icon" href="' . $faviconUrl . '">' . "\n"
        . '    <link rel="apple-touch-icon" href="' . $faviconUrl . '">' . "\n"
        . '    <meta name="theme-color" content="#173845">';
}

// ロゴとサイト名はトップへ、運営者名は tatsuhiko.miya.be へ。入れ子アンカーを避けるため
// 外側はアンカーではなく span にする（.brand img / .brand > span のセレクタは維持）。
function site_render_brand(string $href = '/'): string
{
    $brandHref = htmlspecialchars($href, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
    $mascotUrl = htmlspecialchars(site_asset_url('assets/ken-dog.png'), ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
    return '<span class="brand">'
        . '<a href="' . $brandHref . '" aria-label="自治体マップ トップへ"><img src="' . $mascotUrl . '" alt=""></a>'
        . '<span>'
        . '<a href="' . $brandHref . '"><strong>自治体マップ</strong></a>'
        . '<small><a href="https://tatsuhiko.miya.be/" target="_blank" rel="noopener">宮部たつひこ</a></small>'
        . '</span>'
        . '</span>';
}

// ---- 全ページ共通の枠（ヘッダー・移動・フッター） ----
//
// どのページでも同じ 5 つの行き先を、同じ言葉・同じ順番で出す。スマホでは画面の下に固定の
// タブとして並び、PC ではヘッダーの右に並ぶ（見た目の切り替えは assets/css/site.css）。
// ページごとに案内の中身が違うと、初めての人は自分がどこにいるのか分からなくなる。

/** @return array<string, array{label: string, href: string, icon: string}> */
function site_nav_items(): array
{
    return [
        'home' => [
            'label' => 'ホーム',
            'href' => '/',
            'icon' => '<path d="M4 11.5 12 4l8 7.5"/><path d="M6.5 10v9.5h11V10"/><path d="M10 19.5v-5h4v5"/>',
        ],
        'search' => [
            'label' => '検索',
            'href' => '/search/',
            'icon' => '<circle cx="10.5" cy="10.5" r="6"/><path d="m15 15 5 5"/>',
        ],
        'municipalities' => [
            'label' => '自治体一覧',
            'href' => '/municipalities/',
            'icon' => '<path d="M5 6.5h14M5 12h14M5 17.5h14"/><circle cx="3" cy="6.5" r=".6"/><circle cx="3" cy="12" r=".6"/><circle cx="3" cy="17.5" r=".6"/>',
        ],
        'ai' => [
            'label' => 'AIで調べる',
            'href' => '/api-guide/',
            'icon' => '<path d="M5 5.5h14v10.5H12l-4.5 3.5V16H5z"/><path d="M9 9.5h6M9 12.5h4"/>',
        ],
        'status' => [
            'label' => '収集状況',
            'href' => '/status/',
            'icon' => '<path d="M5 19.5V13M10 19.5V8M15 19.5v-4.5M20 19.5V5"/>',
        ],
    ];
}

/** 共通のスタイル。各ページの専用 CSS より前に読む。 */
function site_render_stylesheet(): string
{
    $url = htmlspecialchars(site_asset_url('assets/css/site.css'), ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
    return '<link rel="stylesheet" href="' . $url . '">';
}

/** ヘッダーと移動。$current は site_nav_items() の鍵（どれでもないページは空）。 */
function site_render_header(string $current = ''): string
{
    $h = static fn(string $value): string => htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
    $links = '';
    foreach (site_nav_items() as $key => $item) {
        $links .= '<a href="' . $h($item['href']) . '"' . ($key === $current ? ' aria-current="page"' : '') . '>'
            . '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.8"'
            . ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' . $item['icon'] . '</svg>'
            . '<span>' . $h($item['label']) . '</span></a>';
    }
    return '<a class="skip-link" href="#main">本文へ進む</a>' . "\n"
        . '<header class="site-header">' . "\n"
        . '    <div class="site-header-inner">' . "\n"
        . '        ' . site_render_brand('/') . "\n"
        . '        <nav class="site-nav" aria-label="サイト内の移動">' . $links . '</nav>' . "\n"
        . '    </div>' . "\n"
        . '</header>';
}

function site_render_footer(): string
{
    return '<footer class="site-footer">' . "\n"
        . '    <div class="site-footer-inner">' . "\n"
        . '        <p class="site-footer-about"><strong>自治体マップ</strong>は、全国の自治体が公開している議会の会議録・条例や規則・事業の評価を集めて、'
        . 'まとめて検索できるようにしたサイトです。載せている文書には、元の自治体のページへのリンクを付けています。無料で、登録は要りません。</p>' . "\n"
        . '        <nav class="site-footer-links" aria-label="このサイトについて">'
        . '<a href="/search/">検索</a>'
        . '<a href="/municipalities/">自治体一覧</a>'
        . '<a href="/api-guide/">AIで調べる</a>'
        . '<a href="/status/">収集状況</a>'
        . '<a href="/support/">お問い合わせ</a>'
        . '<a href="/terms/">利用規約</a>'
        . '<a href="/privacy/">プライバシー</a>'
        . '</nav>' . "\n"
        . '        <p class="site-footer-owner">運営: <a href="https://tatsuhiko.miya.be/" target="_blank" rel="noopener">宮部たつひこ</a></p>' . "\n"
        . '    </div>' . "\n"
        . '</footer>';
}

// ChatGPTのプラグイン名がサイト名と異なるため、審査や利用者が同一サービスだと
// 確認できるよう、規約・プライバシー・サポートの各ページで名称の対応を明示する。
//
// 運営者名は、プラグインを提出した OpenAI アカウントの本人確認済みの氏名（宮部龍彦）を先に出す。
// 2026-09-17 の審査で「確認済みの個人がこのアプリ・ブランドを所有していると
// 確認できない」と不承認になり、09-25 の返答で「開発者名は本人確認済みの氏名と
// 完全に一致させること」と指示された。プラグインの開発者名もこの氏名に揃えている。
// 三川ミミ・宮部たつひこの名前でも活動しているので、同一人物であることと本人の X も示す。
function site_render_service_identity(): string
{
    return '<section class="docs-section">' . "
"
        . '            <h2>サービスの名称</h2>' . "
"
        . '            <p>' . "
"
        . '                本サイト「自治体マップ」（<a href="https://tools.miya.be/">https://tools.miya.be/</a>）は、' . "
"
        . '                宮部龍彦（Tatsuhiko Miyabe）が運営しています。' . "
"
        . '                三川ミミ（<a href="https://x.com/mimidesunya" target="_blank" rel="noopener">@mimidesunya</a>）・宮部たつひこの名前でも活動しており、いずれも同一人物です。' . "
"
        . '                本サイトの検索機能をChatGPTから利用するためのプラグイン' . "
"
        . '                「日本自治体会議録例規集横断調査」も、同じ運営者による同一のサービスです。' . "
"
        . '                プラグインは本サイトのMCPサーバー（<code>https://tools.miya.be/mcp</code>）に接続しており、' . "
"
        . '                本ページの内容はプラグインの利用にもそのまま適用されます。' . "
"
        . '            </p>' . "
"
        . '        </section>';
}
