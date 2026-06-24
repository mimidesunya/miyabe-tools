<?php
declare(strict_types=1);

require_once dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'site_assets.php';

function api_guide_h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function api_guide_asset_url(string $relativePath): string
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
    <title>AI向けAPI解説 - 宮部たつひこの自治体調査</title>
    <?php echo site_render_favicon_links(); ?>
    <link rel="stylesheet" href="<?php echo api_guide_h(api_guide_asset_url('css/search.css')); ?>">
</head>
<body>
<div class="app-shell docs-shell">
    <header class="topbar">
        <a class="brand" href="/">宮部たつひこの自治体調査</a>
        <nav class="page-links" aria-label="関連ページ">
            <a href="/">横断検索</a>
            <a href="/status/">処理状況</a>
            <a href="/privacy/">プライバシー</a>
            <a href="/openapi.json">OpenAPI JSON</a>
            <a href="/openapi.yaml">OpenAPI YAML</a>
        </nav>
    </header>

    <main class="docs-page">
        <section class="docs-hero">
            <p class="kicker">API Guide</p>
            <h1>AIから検索APIを使う</h1>
            <p>
                このAPIは、全国自治体の会議録と例規集を検索するための公開APIです。
                AIエージェントやカスタムGPTに渡しやすいよう、OpenAPI定義を用意しています。
            </p>
            <pre><code>OpenAPI JSON: https://tools.miya.be/openapi.json
OpenAPI YAML: https://tools.miya.be/openapi.yaml</code></pre>
        </section>

        <section class="docs-section">
            <h2>まず使うもの</h2>
            <p>
                AIに直接読ませる場合は、上のOpenAPI定義を指定してください。
                人間が試すだけなら、次のURLで同じ検索を確認できます。
            </p>
            <pre><code>GET https://tools.miya.be/api/search?q=盛土%20メガソーラー</code></pre>
            <p>
                省略時は会議録検索です。例規集を検索する場合は <code>doc_type=reiki</code> を付けます。
            </p>
            <pre><code>GET https://tools.miya.be/api/search?doc_type=reiki&amp;q=個人情報保護</code></pre>
            <p>
                検索結果の <code>api_document_url</code> を呼ぶと、その文書の全文テキストをJSONで取得できます。
            </p>
            <pre><code>GET https://tools.miya.be/api/document?id=検索結果のid&amp;doc_type=minutes</code></pre>
        </section>

        <section class="docs-section">
            <h2>AI別の考え方</h2>
            <dl class="docs-params docs-params-wide">
                <dt>ChatGPT / GPTs</dt>
                <dd>
                    GPT Actions はOpenAPIスキーマを使って外部APIを呼び出します。
                    Actionsに <code>https://tools.miya.be/openapi.json</code> を読み込ませるのが最短です。
                    このAPIは認証なしで検索できます。
                    <a href="https://help.openai.com/en/articles/9442513-configuring-actions-in-gpts" target="_blank" rel="noopener">OpenAIの説明</a>
                </dd>
                <dt>Claude</dt>
                <dd>
                    ClaudeのコネクタはMCPが中心です。Claudeに常設ツールとして持たせるなら、
                    このOpenAPI定義をそのまま登録するより、<code>/api/search</code> を呼ぶ小さなMCPサーバーを用意するのが自然です。
                    <a href="https://claude.com/docs/connectors/overview" target="_blank" rel="noopener">Claude Connectors</a>
                </dd>
                <dt>Gemini / Vertex AI</dt>
                <dd>
                    Gemini APIのfunction callingでは関数宣言を渡します。Vertex AI Extensions ではOpenAPI 3.0互換のYAMLをAPI仕様として使えます。
                    Vertex側に取り込む場合は <code>openapi.yaml</code> を基にしてください。
                    <a href="https://cloud.google.com/vertex-ai/generative-ai/docs/extensions/create-extension" target="_blank" rel="noopener">Vertex AI Extensions</a>
                </dd>
                <dt>Microsoft Copilot Studio</dt>
                <dd>
                    Copilot StudioのREST APIツールやカスタムコネクタでは、OpenAPI仕様ファイルをアップロードしてツール化できます。
                    取り込み時は <code>openapi.yaml</code> または <code>openapi.json</code> を使います。
                    <a href="https://learn.microsoft.com/en-sg/microsoft-copilot-studio/agent-extend-action-rest-api" target="_blank" rel="noopener">REST API tool</a>
                </dd>
            </dl>
        </section>

        <section class="docs-section">
            <h2>AIに渡す短い指示例</h2>
            <p>カスタムGPTやエージェントの説明には、次のような指示を入れると安定します。</p>
            <pre><code>自治体の会議録・例規集を調べるときは searchMunicipalDocuments を使う。
まず doc_type=minutes で会議録を検索する。
条例や規則そのものを探すときだけ doc_type=reiki を使う。
地域が指定されたら pref_code または slug で絞る。
回答では title, municipality_name, held_on または sort_date, excerpt, source_url を根拠として示す。
excerpt は抜粋なので、必要なら api_document_url で全文を取得して確認する。
最終確認が必要な場合は source_url の原サイトも示す。</code></pre>
        </section>

        <section class="docs-section">
            <h2>主なパラメータ</h2>
            <dl class="docs-params">
                <dt><code>q</code></dt>
                <dd>必須。検索語です。例: <code>盛土 メガソーラー</code></dd>
                <dt><code>doc_type</code></dt>
                <dd><code>minutes</code> は会議録、<code>reiki</code> は例規集です。省略すると <code>minutes</code> になります。</dd>
                <dt><code>pref_code</code></dt>
                <dd>都道府県コードです。例: 神奈川県は <code>14</code>。</dd>
                <dt><code>slug</code></dt>
                <dd>自治体を1つに絞るIDです。検索結果や検索画面のURLに含まれます。</dd>
                <dt><code>start_date</code> / <code>end_date</code></dt>
                <dd>対象日を絞ります。例: <code>start_date=2020-01-01&amp;end_date=2024-12-31</code>。</dd>
                <dt><code>start_year</code> / <code>end_year</code></dt>
                <dd>対象年で絞る互換パラメータです。例: <code>start_year=2020&amp;end_year=2024</code>。</dd>
                <dt><code>sort</code></dt>
                <dd><code>date</code> は新しい順、<code>relevance</code> は関連度順です。</dd>
                <dt><code>page</code> / <code>per_page</code></dt>
                <dd>ページ番号と1ページあたりの件数です。<code>per_page</code> は最大100件です。</dd>
            </dl>
        </section>

        <section class="docs-section">
            <h2>検索語の書き方</h2>
            <dl class="docs-params">
                <dt><code>盛土 メガソーラー</code></dt>
                <dd>複数語はAND検索です。</dd>
                <dt><code>"同和団体" 温泉</code></dt>
                <dd>引用符で囲んだ語句を完全一致にし、ほかの語と組み合わせます。</dd>
                <dt><code>盛土 OR 土砂</code></dt>
                <dd>どちらかを含む文書を探します。</dd>
                <dt><code>メガソーラー NOT 促進</code></dt>
                <dd>後ろの語を含む文書を除外します。</dd>
            </dl>
        </section>

        <section class="docs-section">
            <h2>返ってくる項目</h2>
            <dl class="docs-params">
                <dt><code>total</code></dt>
                <dd>ヒット件数です。</dd>
                <dt><code>items</code></dt>
                <dd>検索結果の配列です。</dd>
                <dt><code>title</code></dt>
                <dd>会議名、条例名、文書名などです。</dd>
                <dt><code>excerpt</code></dt>
                <dd>該当箇所の抜粋です。検索語は <code>[[[</code> と <code>]]]</code> で囲まれます。</dd>
                <dt><code>detail_url</code></dt>
                <dd>ブラウザで見るための詳細ページです。会議録ではサイト内の全文表示ページ、例規集では原サイト等のURLです。</dd>
                <dt><code>api_document_url</code></dt>
                <dd>その文書の全文をJSONで取得するAPI URLです。</dd>
                <dt><code>body</code></dt>
                <dd><code>/api/document</code> で返る全文テキストです。検索結果の一覧には含まれません。</dd>
                <dt><code>source_url</code></dt>
                <dd>自治体や配信元の原サイトです。最終確認に使います。</dd>
            </dl>
        </section>

        <section class="docs-section">
            <h2>注意点</h2>
            <p>
                このAPIは調査の入口です。AIの回答では、検索結果の抜粋だけで断定せず、
                重要な内容は <code>api_document_url</code> で全文を確認してください。
                必要に応じて <code>source_url</code> の原サイトも確認してください。
                会議録と例規集は性格が違うため、原則として別々に検索します。
            </p>
        </section>
    </main>
</div>
</body>
</html>
