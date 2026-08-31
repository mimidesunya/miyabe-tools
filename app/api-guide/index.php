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
    <title>AIから使う方法｜自治体マップ</title>
    <?php echo site_render_page_meta(
        'AIから使う方法｜自治体マップ',
        '自治体マップをClaudeやChatGPTなどから使う方法を、契約や管理者設定による違いも含めて分かりやすく説明します。自治体マップ側の登録やAPIキーは不要です。',
        '/api-guide/'
    ); ?>
    <?php echo site_render_favicon_links(); ?>
    <link rel="stylesheet" href="<?php echo api_guide_h(api_guide_asset_url('css/search.css')); ?>">
</head>
<body>
<div class="app-shell docs-shell">
    <header class="topbar">
        <?php echo site_render_brand('/'); ?>
        <nav class="page-links" aria-label="関連ページ">
            <a href="/">地図から探す</a>
            <a href="/search/">記録を検索</a>
            <a href="/status/">収集・公開状況</a>
            <a href="/privacy/">プライバシー</a>
            <a href="/terms/">利用規約</a>
            <a href="/support/">サポート</a>
        </nav>
    </header>

    <main class="docs-page">
        <section class="docs-hero">
            <p class="kicker">AIに調べてもらう</p>
            <h1>自治体マップをAIにつなぐ方法</h1>
            <p>
                いちばん簡単なのは、このサイトの<a href="/search/">「記録を検索」</a>をそのまま使う方法です。
                AIに探してもらいたい場合は、下のアドレスをAIサービスに登録します。
            </p>
            <pre><code>https://tools.miya.be/mcp</code></pre>
            <p class="docs-note">
                自治体マップ側の登録やAPIキーは不要です。ただし、使っているAIの契約や、
                会社・学校の管理者設定によっては登録できません。
            </p>
        </section>

        <section class="docs-section">
            <h2>これは何をするもの？</h2>
            <p>
                MCPは、AIに「自治体マップの検索ボタン」を渡すためのしくみです。
                つなぐと、AIが会議録や条例・規則を検索し、見つけた文書を読めるようになります。
                ただし、すべてのAIがMCPに対応しているわけではなく、つなぎ方もAIごとに違います。
            </p>
        </section>

        <section class="docs-section">
            <p class="kicker">おすすめ</p>
            <h2>Claudeにつなぐ</h2>
            <ol class="docs-steps">
                <li>Claudeの<strong>「カスタマイズ」</strong>→<strong>「コネクタ」</strong>を開きます（<a href="https://claude.ai/customize/connectors" target="_blank" rel="noopener">直接開く</a>）。</li>
                <li><strong>「＋」</strong>→<strong>「カスタムコネクタを追加」</strong>へ進みます。</li>
                <li>名前を「自治体マップ」、アドレスを <code>https://tools.miya.be/mcp</code> にして追加します。</li>
                <li>新しい会話で<strong>「＋」</strong>→<strong>「コネクタ」</strong>を開き、「自治体マップ」を有効にします。</li>
            </ol>
            <p>
                認証は不要です（OAuthの入力欄は空のままにします）。無料版はカスタムコネクタを1つまで追加できます。
                Team・Enterpriseでは、先に組織のオーナーが組織側へ追加し、そのあと各自が接続します。
                <a href="https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp" target="_blank" rel="noopener">Claude公式の説明</a>
            </p>
        </section>

        <section class="docs-section">
            <h2>ChatGPTにつなぐ</h2>
            <p>
                ChatGPTでは、外部サービスとの接続を「アプリ」と呼びます
                （2026年7月9日に、それまでのアプリディレクトリがプラグインディレクトリへ変わりました）。
                自治体マップは「日本自治体会議録例規集横断調査」という名前で登録しています。
                名前は違いますが、本サイトと同じサービスです。
            </p>
            <p>
                ChatGPTのMCP機能は、契約の種類と管理者の設定によって使える範囲が違います。
                メニューが見当たらなくても、操作を間違えたとは限りません。
            </p>
            <ol class="docs-steps">
                <li>パソコンのブラウザでChatGPTを開きます。現在、この設定はスマートフォン版ではできません。</li>
                <li><strong>「設定」</strong>→<strong>「アプリ」</strong>→<strong>「詳細設定」</strong>で、開発者モードを有効にします。</li>
                <li>アプリを作成する画面で、アドレスに <code>https://tools.miya.be/mcp</code> を指定します。</li>
            </ol>
            <p class="docs-note">
                Business・Enterprise・Eduでは、管理者の許可が必要です。
                Proを含むそれ以外の契約では、開発者モードで検索と本文取得までは使えますが、
                MCPの全機能はBusiness・Enterprise・Eduに限られます。自治体マップは検索と本文取得だけなので、
                開発者モードで使えます。
            </p>
            <p>
                MCPを追加できない場合でも、「GPTを作成」の「アクション」が使える契約なら、
                <a href="/openapi.json">OpenAPI JSON</a>を読み込ませる方法があります。認証は「なし」です。
                なお、1つのGPTで「アプリ」と「アクション」を同時には使えません。
                Proモードの応答ではアクションを使えません。
            </p>
            <p>
                <a href="https://help.openai.com/en/articles/12584461-developer-mode-apps-and-full-mcp-connectors-in-chatgpt-beta" target="_blank" rel="noopener">ChatGPTのMCPに関する公式説明</a>
                ／
                <a href="https://help.openai.com/en/articles/9442513" target="_blank" rel="noopener">GPTアクションの公式説明</a>
            </p>
        </section>

        <section class="docs-section">
            <h2>Grokにつなぐ</h2>
            <ol class="docs-steps">
                <li><a href="https://grok.com/connectors" target="_blank" rel="noopener">grok.com/connectors</a> を開きます。</li>
                <li><strong>「New Connector」</strong>→<strong>「Custom」</strong>へ進みます。</li>
                <li>アドレスに <code>https://tools.miya.be/mcp</code> を入れて追加します。認証は不要です。</li>
            </ol>
            <p>
                Business・Enterpriseでは、先にチームの管理者が用意する必要があります。
                <a href="https://docs.x.ai/grok/connectors" target="_blank" rel="noopener">xAI公式の説明</a>
            </p>
        </section>

        <section class="docs-section">
            <h2>つないだ後の質問例</h2>
            <p>むずかしい命令は必要ありません。いつもの言葉で頼んでください。</p>
            <pre><code>川崎市の議会で「盛土」について話し合われた記録を探して
メガソーラーを規制する条例がある自治体を教えて
その会議録の全文を読んで、賛成と反対の意見をまとめて
答えの根拠にした自治体のページも示して</code></pre>
        </section>

        <section class="docs-section">
            <h2>何を調べられる？</h2>
            <div class="docs-choice-grid">
                <div>
                    <h3>会議録</h3>
                    <p>議会で、だれが何を話したかを調べます。</p>
                </div>
                <div>
                    <h3>例規集</h3>
                    <p>その自治体で決まっている条例や規則を調べます。</p>
                </div>
            </div>
            <p>
                AIは検索と本文の読み取りを自動で使い分けます。道具の名前を覚える必要はありません。
            </p>
        </section>

        <section class="docs-section docs-caution">
            <h2>最後は元の資料を確かめてください</h2>
            <p>
                AIは、読み落としたり、もっともらしい間違いを言ったりすることがあります。
                選挙、裁判、契約など大事な判断に使う場合は、回答に出てきた自治体の元ページを開き、
                本文と日付を自分でも確かめてください。
            </p>
        </section>

        <details class="docs-section docs-details">
            <summary>そのほかのAI・開発ツールで使う</summary>
            <div class="docs-details-body">
                <h2>Microsoft Copilot Studio</h2>
                <p>
                    会社などでAIを作る「Copilot Studio」はMCPに対応しています。
                    エージェントの「ツール」→「ツールを追加」→「新しいツール」→「Model Context Protocol」と進み、
                    <code>https://tools.miya.be/mcp</code> を指定します。認証方法は「なし」です。
                    ふつうのMicrosoft Copilotのチャットとは別の機能です。
                    <a href="https://learn.microsoft.com/en-us/microsoft-copilot-studio/mcp-add-existing-server-to-agent" target="_blank" rel="noopener">Microsoft公式の説明</a>
                </p>

                <h2>Gemini CLI</h2>
                <p>
                    プログラム開発者向けのGemini CLIでは、<code>settings.json</code> に次のように書きます。
                    ふつうのGeminiのウェブ画面やスマートフォンアプリの設定ではありません。
                    <a href="https://geminicli.com/docs/tools/mcp-server/" target="_blank" rel="noopener">Gemini CLI公式の説明</a>
                </p>
                <p class="docs-note">
                    無償枠とGoogle One経由のGemini CLIは2026年6月18日で受付を終了し、Antigravity CLIへ移りました。
                    Gemini Code AssistのStandard・Enterprise、または有償APIキー経由なら引き続き使えます。
                    Antigravity CLIでの書き方は公式の説明を確認してください。
                    <a href="https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli" target="_blank" rel="noopener">Google公式の告知</a>
                </p>
                <pre><code>{
  "mcpServers": {
    "自治体マップ": {
      "httpUrl": "https://tools.miya.be/mcp"
    }
  }
}</code></pre>
                <h2>Claude Code</h2>
                <pre><code>claude mcp add --transport http 自治体マップ https://tools.miya.be/mcp</code></pre>
                <p>
                    <a href="https://code.claude.com/docs/en/mcp" target="_blank" rel="noopener">Claude Code公式の説明</a>
                </p>

                <h2>Cursor</h2>
                <p>
                    <code>~/.cursor/mcp.json</code>（プロジェクト単位なら <code>.cursor/mcp.json</code>）に書きます。
                    <a href="https://cursor.com/docs/context/mcp" target="_blank" rel="noopener">Cursor公式の説明</a>
                </p>
                <pre><code>{
  "mcpServers": {
    "自治体マップ": {
      "url": "https://tools.miya.be/mcp"
    }
  }
}</code></pre>

                <h2>VS Code（GitHub Copilot Chat）</h2>
                <p>
                    MCPサーバーの設定に次のように書きます。ふつうのMicrosoft Copilotのチャットとは別の機能です。
                    <a href="https://code.visualstudio.com/docs/copilot/customization/mcp-servers" target="_blank" rel="noopener">VS Code公式の説明</a>
                </p>
                <pre><code>{
  "servers": {
    "自治体マップ": {
      "type": "http",
      "url": "https://tools.miya.be/mcp"
    }
  }
}</code></pre>

                <p>
                    そのほかのツールでも、Streamable HTTP方式のリモートMCPサーバーとして同じアドレスを設定します。
                    Mistral Le Chatも任意のリモートMCPサーバーにつなげますが、
                    公式の画面手順は確認できていません。
                </p>
            </div>
        </details>

        <section class="docs-section">
            <h2>開発者向け：REST API</h2>
            <p>
                ここから先は、プログラムを作る人向けです。直接呼び出すためのREST APIとOpenAPI定義も維持しています。
                新しくAI連携を作る場合はMCPをおすすめします。
            </p>
            <pre><code>GET https://tools.miya.be/api/search?q=盛土%20メガソーラー
GET https://tools.miya.be/api/search?doc_type=reiki&amp;q=個人情報保護
GET https://tools.miya.be/api/document?id=検索結果のid&amp;doc_type=minutes

OpenAPI JSON: https://tools.miya.be/openapi.json
OpenAPI YAML: https://tools.miya.be/openapi.yaml</code></pre>
            <dl class="docs-params">
                <dt><code>q</code></dt>
                <dd>必須。検索語です。空白区切りはAND、<code>"..."</code> は完全一致、<code>OR</code>・<code>NOT</code>・括弧も使えます。</dd>
                <dt><code>doc_type</code></dt>
                <dd><code>minutes</code>（会議録）または <code>reiki</code>（例規集）。省略すると <code>minutes</code> です。</dd>
                <dt><code>pref_code</code></dt>
                <dd>都道府県コード。例: 神奈川県は <code>14</code>。</dd>
                <dt><code>slug</code></dt>
                <dd>自治体を1つに絞るID。検索結果や検索画面のURLに含まれます。</dd>
                <dt><code>start_date</code> / <code>end_date</code></dt>
                <dd>対象日を絞ります。例: <code>start_date=2020-01-01&amp;end_date=2024-12-31</code>。</dd>
                <dt><code>sort</code></dt>
                <dd><code>date</code>（新しい順）または <code>relevance</code>（関連度順）。</dd>
                <dt><code>page</code> / <code>per_page</code></dt>
                <dd>ページ番号と1ページあたりの件数。<code>per_page</code> は最大100件です。</dd>
            </dl>
            <p>
                検索結果の <code>api_document_url</code> を呼ぶと全文をJSONで取得できます。
                <code>source_url</code> は自治体や配信元の原サイトで、最終確認に使います。
            </p>
        </section>
    </main>
</div>
</body>
</html>
