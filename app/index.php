<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>miyabe-tools</title>
    <style>
        * { box-sizing: border-box; }
        body {
            margin: 0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans JP', sans-serif;
            background: #f5f7fb;
            color: #1f2937;
            display: flex;
            justify-content: center;
            padding: 60px 16px;
        }
        .container {
            max-width: 600px;
            width: 100%;
        }
        h1 {
            font-size: 24px;
            margin: 0 0 32px;
            color: #0f172a;
        }
        .tool-list {
            list-style: none;
            padding: 0;
            margin: 0;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }
        .tool-list a {
            display: block;
            background: #fff;
            border: 1px solid #e5e7eb;
            border-radius: 10px;
            padding: 20px;
            text-decoration: none;
            color: inherit;
            box-shadow: 0 2px 8px rgba(15,23,42,0.04);
            transition: box-shadow 0.15s, border-color 0.15s;
        }
        .tool-list a:hover {
            border-color: #275ea3;
            box-shadow: 0 4px 16px rgba(39,94,163,0.12);
        }
        .tool-name {
            font-size: 18px;
            font-weight: 600;
            color: #275ea3;
            margin-bottom: 6px;
        }
        .tool-desc {
            font-size: 14px;
            color: #475569;
            line-height: 1.5;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>miyabe-tools</h1>
        <ul class="tool-list">
            <li>
                <a href="/reiki/">
                    <div class="tool-name">川崎市例規集 AI評価ビューア</div>
                    <div class="tool-desc">川崎市の条例・規則 1,396本をAI（Gemini / GPT / Claude）で自動評価。分類・スコア・判定理由を一覧・検索できます。</div>
                </a>
            </li>
        </ul>
    </div>
</body>
</html>
