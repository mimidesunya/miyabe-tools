<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'site_assets.php';

function terms_h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function terms_asset_url(string $relativePath): string
{
    $normalized = trim(str_replace('\\', '/', $relativePath), '/');
    $publicPath = '/search/assets/' . $normalized;
    $diskPath = dirname(__DIR__) . DIRECTORY_SEPARATOR . 'search' . DIRECTORY_SEPARATOR . 'assets' . DIRECTORY_SEPARATOR
        . str_replace('/', DIRECTORY_SEPARATOR, $normalized);
    $version = is_file($diskPath) ? (string)filemtime($diskPath) : '';
    return $version !== '' ? $publicPath . '?v=' . rawurlencode($version) : $publicPath;
}
?><!doctype html>
<html lang="ja">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>利用規約｜自治体マップ</title>
    <?php echo site_render_page_meta(
        '利用規約｜自治体マップ',
        '自治体マップと、その検索機能をAI（MCP）やAPIから利用する場合の条件について説明します。',
        '/terms/'
    ); ?>
    <?php echo site_render_favicon_links(); ?>
    <link rel="stylesheet" href="<?php echo terms_h(terms_asset_url('css/search.css')); ?>">
</head>
<body>
<div class="app-shell docs-shell">
    <header class="topbar">
        <?php echo site_render_brand('/'); ?>
        <nav class="page-links" aria-label="関連ページ">
            <a href="/">地図から探す</a>
            <a href="/search/">記録を検索</a>
            <a href="/api-guide/">AIから使う（MCP）</a>
            <a href="/privacy/">プライバシー</a>
            <a href="/support/">サポート</a>
        </nav>
    </header>

    <main class="docs-page">
        <section class="docs-hero">
            <p class="kicker">Terms of Use</p>
            <h1>利用規約</h1>
            <p>
                このページは、「自治体マップ」と、その検索機能をブラウザ・API・AI（MCP）から利用する場合の条件を
                説明するものです。本サイトを利用した時点で、この内容に同意したものとして扱います。
            </p>
            <p>制定日: 2026年8月21日</p>
        </section>

        <?php echo site_render_service_identity(); ?>

        <section class="docs-section">
            <h2>提供する内容</h2>
            <p>
                本サイトは、全国の自治体が公開している議会の会議録と、条例・規則などの例規集を収集し、
                横断して検索できるようにしたものです。あわせて、同じ検索機能をAIから使うためのMCPサーバーと、
                互換用のHTTP APIを提供しています。
            </p>
            <p>
                本サイトは個人が運営しており、国、地方公共団体、その他の公的機関とは関係がありません。
                自治体の公式な発表ではない点にご注意ください。
            </p>
        </section>

        <section class="docs-section">
            <h2>情報の正確性について</h2>
            <p>
                本サイトが表示する内容は、収集元の公開情報を機械的に取り込んだものです。
                取り込みの失敗、収集元の変更、更新の遅れ、文字認識の誤りなどにより、
                収集元の内容と一致しない場合や、一部が欠けている場合があります。
            </p>
            <p>
                <strong>本サイトは、掲載内容の正確性、完全性、最新性を保証しません。</strong>
                引用、報道、議会活動、業務上の判断など、正確さが必要な用途では、
                検索結果に表示される原典URLから収集元の文書を必ず確認してください。
            </p>
            <p>
                収集元が公開を取りやめた文書や、収集に対応できていない自治体は、本サイトには表示されません。
                本サイトで見つからないことは、その文書が存在しないことを意味しません。
            </p>
        </section>

        <section class="docs-section">
            <h2>収集元の情報と著作権</h2>
            <p>
                本サイトが扱う文書の著作権は、それぞれの収集元に帰属します。
                本サイトは検索と閲覧の便宜のために本文を保持していますが、権利関係は収集元の定めに従います。
                二次利用にあたっては、収集元の利用条件を確認してください。
            </p>
            <p>
                収集元の権利者から、掲載の停止や修正について申し出があった場合は、
                <a href="/support/">サポート</a>の窓口から連絡をいただければ、内容を確認のうえ対応します。
            </p>
        </section>

        <section class="docs-section">
            <h2>APIとAI（MCP）からの利用</h2>
            <p>
                MCPサーバーとHTTP APIは、認証なしで利用できます。ただし、次の点を守ってください。
            </p>
            <ul>
                <li>本サイトや収集元のサーバーに、過大な負荷をかけないこと</li>
                <li>短時間に大量のリクエストを繰り返し送らないこと</li>
                <li>全文の一括取得を目的とした網羅的な巡回を行わないこと</li>
            </ul>
            <p>
                過大な負荷を検知した場合、事前の通知なくリクエストを制限し、または遮断することがあります。
                大量のデータが必要な場合は、あらかじめご相談ください。
            </p>
        </section>

        <section class="docs-section">
            <h2>禁止事項</h2>
            <p>次の行為はご遠慮ください。</p>
            <ul>
                <li>法令または公序良俗に反する行為</li>
                <li>本サイトの運営を妨害する行為</li>
                <li>本サイトの内容を、収集元の公式発表であるかのように装って提示する行為</li>
                <li>本サイトの内容を改変したうえで、本サイトの内容として提示する行為</li>
                <li>他の利用者、収集元、第三者の権利を侵害する行為</li>
            </ul>
        </section>

        <section class="docs-section">
            <h2>サービスの変更と中断</h2>
            <p>
                本サイトは、検索対象、機能、APIやMCPの仕様を予告なく変更することがあります。
                また、保守、障害、収集元の事情などにより、予告なく提供を中断または終了することがあります。
            </p>
        </section>

        <section class="docs-section">
            <h2>免責</h2>
            <p>
                本サイトの利用、または利用できなかったことによって生じた損害について、
                運営者は責任を負いません。掲載内容に基づく判断は、利用者ご自身の責任で行ってください。
            </p>
        </section>

        <section class="docs-section">
            <h2>準拠法と裁判管轄</h2>
            <p>
                本規約は日本法に準拠します。本サイトに関して紛争が生じた場合は、
                横浜地方裁判所川崎支部を第一審の専属的合意管轄裁判所とします。
            </p>
        </section>

        <section class="docs-section">
            <h2>変更</h2>
            <p>
                本規約は、サービス内容の変更や法令上の必要に応じて改定することがあります。
                重要な変更がある場合は、このページで分かるようにします。
            </p>
        </section>

        <section class="docs-section">
            <h2>連絡先</h2>
            <p>
                本規約や本サイトに関するお問い合わせは、<a href="/support/">サポート</a>のページをご覧ください。
            </p>
        </section>
    </main>
</div>
</body>
</html>
