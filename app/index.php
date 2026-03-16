<?php
declare(strict_types=1);

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'municipalities.php';

function h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

$municipalities = municipality_registry();
$featureLabels = [
    'boards' => 'ポスター掲示場',
    'gijiroku' => '会議録',
    'reiki' => '例規集',
];
?><!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>miyabe-tools</title>
    <style>
        * { box-sizing: border-box; }
        :root {
            --bg: #eef2f6;
            --panel: rgba(255, 255, 255, 0.94);
            --line: #d6dde6;
            --text: #17202b;
            --muted: #576575;
            --accent: #114e72;
            --accent-soft: rgba(17, 78, 114, 0.08);
            --ok: #0f766e;
            --warn: #a16207;
        }
        body {
            margin: 0;
            font-family: 'Hiragino Sans', 'Yu Gothic', sans-serif;
            color: var(--text);
            background:
                radial-gradient(circle at 0% 0%, rgba(17, 78, 114, 0.12), transparent 26%),
                radial-gradient(circle at 100% 0%, rgba(196, 143, 72, 0.18), transparent 32%),
                linear-gradient(180deg, #f6f8fb 0%, var(--bg) 42%, #f8fafc 100%);
            padding: 42px 16px 56px;
        }
        .shell {
            max-width: 1080px;
            margin: 0 auto;
            display: grid;
            gap: 18px;
        }
        .hero, .municipality-card {
            background: var(--panel);
            border: 1px solid var(--line);
            border-radius: 22px;
            box-shadow: 0 16px 40px rgba(23, 32, 43, 0.08);
            backdrop-filter: blur(14px);
        }
        .hero {
            padding: 28px;
            display: grid;
            gap: 14px;
        }
        .eyebrow {
            font-size: 12px;
            font-weight: 700;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: var(--accent);
        }
        h1 {
            margin: 0;
            font-size: clamp(28px, 5vw, 46px);
            line-height: 1.05;
            font-family: 'Yu Mincho', 'Hiragino Mincho ProN', serif;
        }
        .hero-copy {
            max-width: 52rem;
            font-size: 15px;
            line-height: 1.8;
            color: #334155;
        }
        .hero-meta {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }
        .hero-meta span {
            padding: 8px 12px;
            border-radius: 999px;
            border: 1px solid var(--line);
            background: #fff;
            font-size: 13px;
            color: var(--muted);
        }
        .municipality-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 18px;
        }
        .municipality-card {
            padding: 22px;
            display: grid;
            gap: 16px;
        }
        .municipality-head {
            display: flex;
            justify-content: space-between;
            align-items: start;
            gap: 12px;
        }
        .municipality-name {
            margin: 0;
            font-size: 24px;
        }
        .municipality-note {
            font-size: 13px;
            color: var(--muted);
            line-height: 1.7;
        }
        .feature-list {
            display: grid;
            gap: 10px;
        }
        .feature-link, .feature-disabled {
            border-radius: 16px;
            border: 1px solid var(--line);
            padding: 14px 16px;
            display: grid;
            gap: 6px;
        }
        .feature-link {
            text-decoration: none;
            color: inherit;
            background: linear-gradient(180deg, #ffffff, #f8fbfd);
            transition: transform 0.14s ease, box-shadow 0.14s ease, border-color 0.14s ease;
        }
        .feature-link:hover {
            transform: translateY(-2px);
            border-color: rgba(17, 78, 114, 0.34);
            box-shadow: 0 14px 28px rgba(17, 78, 114, 0.08);
        }
        .feature-disabled {
            background: linear-gradient(180deg, #fbfbfb, #f5f6f7);
            color: var(--muted);
        }
        .feature-top {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
        }
        .feature-title {
            font-size: 15px;
            font-weight: 700;
        }
        .feature-desc {
            font-size: 13px;
            line-height: 1.7;
        }
        .status {
            flex: 0 0 auto;
            padding: 4px 9px;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 700;
        }
        .status-ready {
            color: var(--ok);
            background: rgba(15, 118, 110, 0.1);
        }
        .status-pending {
            color: var(--warn);
            background: rgba(161, 98, 7, 0.1);
        }
        .legend {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }
        .legend span {
            padding: 7px 10px;
            border-radius: 999px;
            font-size: 12px;
            color: var(--muted);
            background: var(--accent-soft);
        }
        @media (max-width: 720px) {
            body { padding: 20px 12px 32px; }
            .hero, .municipality-card { padding: 18px; border-radius: 18px; }
            .municipality-grid { grid-template-columns: 1fr; }
            .municipality-head, .feature-top { flex-direction: column; align-items: start; }
        }
    </style>
</head>
<body>
    <div class="shell">
        <section class="hero">
            <div class="eyebrow">Municipality Switch Ready</div>
            <h1>自治体ごとに<br>機能を切り替えられる入口</h1>
            <div class="hero-copy">
                ポスター掲示場、会議録、例規集を自治体単位で整理しました。利用可能な機能はそのまま開けて、未整備のものは準備中として見分けられます。
            </div>
            <div class="hero-meta">
                <span>自治体数: <?php echo h((string)count($municipalities)); ?></span>
                <span>切り替え単位: `slug`</span>
                <span>データ参照: 共通レジストリ管理</span>
            </div>
        </section>

        <div class="legend">
            <span>利用可能: 各機能のデータと画面が利用できます</span>
            <span>準備中: 自治体定義はあるが、機能データは未整備です</span>
        </div>

        <section class="municipality-grid">
            <?php foreach ($municipalities as $slug => $municipality): ?>
                <article class="municipality-card">
                    <div class="municipality-head">
                        <div>
                            <h2 class="municipality-name"><?php echo h((string)$municipality['name']); ?></h2>
                            <div class="municipality-note">slug: <?php echo h($slug); ?></div>
                        </div>
                        <div class="municipality-note">
                            利用可能機能:
                            <?php
                            $available = [];
                            foreach ($featureLabels as $featureKey => $label) {
                                if (!empty($municipality[$featureKey]['enabled'])) {
                                    $available[] = $label;
                                }
                            }
                            echo h($available !== [] ? implode(' / ', $available) : 'なし');
                            ?>
                        </div>
                    </div>
                    <div class="feature-list">
                        <?php foreach ($featureLabels as $featureKey => $label): ?>
                            <?php $feature = $municipality[$featureKey] ?? []; ?>
                            <?php if (!empty($feature['enabled'])): ?>
                                <a class="feature-link" href="<?php echo h((string)$feature['url']); ?>">
                                    <div class="feature-top">
                                        <div class="feature-title"><?php echo h($label); ?></div>
                                        <span class="status status-ready">利用可能</span>
                                    </div>
                                    <div class="feature-desc"><?php echo h((string)($feature['title'] ?? '')); ?></div>
                                </a>
                            <?php else: ?>
                                <div class="feature-disabled">
                                    <div class="feature-top">
                                        <div class="feature-title"><?php echo h($label); ?></div>
                                        <span class="status status-pending">準備中</span>
                                    </div>
                                    <div class="feature-desc"><?php echo h((string)($feature['title'] ?? ($municipality['name'] . $label))); ?></div>
                                </div>
                            <?php endif; ?>
                        <?php endforeach; ?>
                    </div>
                </article>
            <?php endforeach; ?>
        </section>
    </div>
</body>
</html>
