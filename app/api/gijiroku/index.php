<?php
declare(strict_types=1);

require_once dirname(__DIR__, 3) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'gijiroku_api.php';
require_once dirname(__DIR__, 3) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'site_assets.php';

$catalog = gijiroku_api_catalog_payload();
$items = is_array($catalog['items'] ?? null) ? $catalog['items'] : [];
$total = (int)($catalog['total'] ?? 0);
$sampleSlug = '';
foreach ($items as $item) {
    if (!is_array($item)) {
        continue;
    }
    if (trim((string)($item['slug'] ?? '')) === '14130-kawasaki-shi') {
        $sampleSlug = '14130-kawasaki-shi';
        break;
    }
}
if ($sampleSlug === '' && isset($items[0]) && is_array($items[0])) {
    $sampleSlug = trim((string)($items[0]['slug'] ?? ''));
}
if ($sampleSlug === '') {
    $sampleSlug = '14130-kawasaki-shi';
}
$sampleHeldOn = '2025-06-18';
$sampleDocumentId = 123;
$sampleSearchUrl = gijiroku_api_search_url($sampleSlug, ['q' => '補正予算', 'page' => 1, 'per_page' => 5]);
$sampleDocumentsUrl = gijiroku_api_documents_url($sampleSlug, ['held_on' => $sampleHeldOn, 'page' => 1, 'per_page' => 5]);
$sampleDocumentUrl = gijiroku_api_document_url($sampleSlug, $sampleDocumentId, ['q' => '補正予算']);
?><!doctype html>
<html lang="ja">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>会議録検索 API</title>
    <?php echo site_render_favicon_links(); ?>
    <style>
        :root {
            color-scheme: light;
            --bg: #f3f0e8;
            --card: #fffdfa;
            --line: #d7c7aa;
            --ink: #213028;
            --muted: #5f6f66;
            --accent: #0f5c4d;
            --accent-soft: #dff0e7;
            --code: #f7f2e8;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            font-family: "BIZ UDPGothic", "Hiragino Sans", sans-serif;
            background:
                radial-gradient(circle at top right, rgba(15, 92, 77, 0.12), transparent 28%),
                linear-gradient(180deg, #fcfaf5 0%, var(--bg) 100%);
            color: var(--ink);
        }
        main {
            max-width: 980px;
            margin: 0 auto;
            padding: 32px 20px 56px;
        }
        .hero, .card {
            background: var(--card);
            border: 1px solid var(--line);
            border-radius: 24px;
            box-shadow: 0 16px 40px rgba(50, 67, 59, 0.08);
        }
        .hero {
            padding: 28px;
            display: grid;
            gap: 18px;
            margin-bottom: 22px;
        }
        .eyebrow {
            display: inline-flex;
            width: fit-content;
            padding: 6px 10px;
            border-radius: 999px;
            background: var(--accent-soft);
            color: var(--accent);
            font-size: 12px;
            font-weight: 700;
            letter-spacing: 0.06em;
            text-transform: uppercase;
        }
        h1, h2 {
            margin: 0;
            line-height: 1.2;
        }
        h1 { font-size: clamp(32px, 5vw, 52px); }
        h2 { font-size: 24px; }
        p {
            margin: 0;
            line-height: 1.7;
            color: var(--muted);
        }
        .metrics {
            display: grid;
            gap: 14px;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        }
        .metric {
            padding: 16px 18px;
            border-radius: 18px;
            background: #f7f4ec;
            border: 1px solid #eadfc9;
        }
        .metric strong {
            display: block;
            font-size: 28px;
            color: var(--accent);
        }
        .grid {
            display: grid;
            gap: 16px;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
        }
        .card {
            padding: 22px;
        }
        .card p + p {
            margin-top: 10px;
        }
        a {
            color: var(--accent);
            text-decoration: none;
        }
        a:hover { text-decoration: underline; }
        code, pre {
            font-family: Consolas, "Cascadia Code", monospace;
            background: var(--code);
        }
        code {
            padding: 0.15em 0.4em;
            border-radius: 8px;
        }
        pre {
            margin: 12px 0 0;
            padding: 14px 16px;
            border-radius: 16px;
            border: 1px solid #eadfc9;
            overflow-x: auto;
            white-space: pre-wrap;
            line-height: 1.6;
        }
        ul {
            margin: 12px 0 0;
            padding-left: 18px;
            color: var(--muted);
        }
        li + li {
            margin-top: 8px;
        }
        .links {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }
        .link-pill {
            display: inline-flex;
            padding: 10px 14px;
            border: 1px solid var(--line);
            border-radius: 999px;
            background: #fff;
            font-weight: 700;
        }
    </style>
</head>
<body>
<main>
    <section class="hero">
        <div class="eyebrow">OpenAPI Gateway</div>
        <h1>会議録検索 API</h1>
        <p>既存の <a href="/gijiroku/cross.php">会議録横断検索</a> と同じ自治体カタログ・SQLite FTS5 検索ロジックを使う読み取り API です。まず自治体一覧を取得し、返ってきた <code>slug</code> を使って自治体ごとの会議録を検索できます。</p>
        <div class="metrics">
            <div class="metric">
                <span>検索対象自治体</span>
                <strong><?php echo h((string)$total); ?></strong>
            </div>
            <div class="metric">
                <span>OpenAPI 仕様</span>
                <strong>3.0.3</strong>
            </div>
            <div class="metric">
                <span>既存 UI 接続</span>
                <strong>そのまま流用</strong>
            </div>
        </div>
        <div class="links">
            <a class="link-pill" href="<?php echo h(gijiroku_api_municipalities_url()); ?>">自治体一覧 JSON</a>
            <a class="link-pill" href="<?php echo h(gijiroku_api_openapi_url()); ?>">OpenAPI JSON</a>
            <a class="link-pill" href="<?php echo h($sampleSearchUrl); ?>">検索サンプル JSON</a>
            <a class="link-pill" href="<?php echo h($sampleDocumentsUrl); ?>">日付一覧サンプル JSON</a>
        </div>
    </section>

    <section class="grid">
        <article class="card">
            <h2>1. 自治体一覧</h2>
            <p><code>GET <?php echo h(gijiroku_api_municipalities_url()); ?></code></p>
            <p>会議録検索 DB が使える自治体だけを返します。各要素の <code>search_api_url</code> は、その自治体を検索する API の入口です。</p>
            <pre>curl "<?php echo h(gijiroku_api_municipalities_url()); ?>"</pre>
        </article>

        <article class="card">
            <h2>2. 自治体別検索</h2>
            <p><code>GET <?php echo h(gijiroku_api_search_url()); ?>?slug=...&amp;q=...</code></p>
            <p>既存 UI と同じ検索式を受け付けます。<code>AND</code> / <code>OR</code> / <code>NOT</code> / <code>NEAR/5</code> と、<code>"同和地区"</code> のようなフレーズ一致が使えます。抜粋は <code>excerpt</code> にハイライト記号つき、<code>excerpt_plain</code> に平文で入ります。</p>
            <pre>curl "<?php echo h($sampleSearchUrl); ?>"</pre>
        </article>

        <article class="card">
            <h2>3. OpenAPI 仕様</h2>
            <p><code>GET <?php echo h(gijiroku_api_openapi_url()); ?></code></p>
            <p>クライアント生成や連携設定にはこちらを使えます。仕様は一覧 API の返却値に合わせて自動で組み立てています。</p>
            <pre>curl "<?php echo h(gijiroku_api_openapi_url()); ?>"</pre>
        </article>

        <article class="card">
            <h2>4. 日付で会議一覧</h2>
            <p><code>GET <?php echo h(gijiroku_api_documents_url()); ?>?slug=...&amp;held_on=YYYY-MM-DD</code></p>
            <p>特定開催日の会議一覧を返します。ここで取れた <code>id</code> を、全文取得 API に渡します。</p>
            <pre>curl "<?php echo h($sampleDocumentsUrl); ?>"</pre>
        </article>

        <article class="card">
            <h2>5. 会議録全文</h2>
            <p><code>GET <?php echo h(gijiroku_api_document_url()); ?>?slug=...&amp;id=...</code></p>
            <p>本文全文と、既存ビューアと同じ構造化ブロックを返します。<code>q</code> を渡すと一致ブロックも分かります。</p>
            <pre>curl "<?php echo h($sampleDocumentUrl); ?>"</pre>
        </article>
    </section>

    <section class="card" style="margin-top: 18px;">
        <h2>返却のポイント</h2>
        <ul>
            <li><code>slug</code> は自治体一覧 API の値をそのまま検索 API に渡します。</li>
            <li><code>status</code> は <code>ok</code> / <code>missing_db</code> / <code>db_error</code> / <code>search_error</code> など既存 UI と同じ値です。</li>
            <li><code>documents.php</code> は開催日ごとの会議一覧、<code>document.php</code> は全文取得です。</li>
            <li><code>detail_url</code> と <code>browse_url</code> は、既存の自治体別会議録画面へ飛ぶ相対 URL です。</li>
        </ul>
    </section>
</main>
</body>
</html>
