<?php
declare(strict_types=1);

require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'municipalities.php';
require_once dirname(__DIR__) . DIRECTORY_SEPARATOR . 'lib' . DIRECTORY_SEPARATOR . 'background_tasks.php';

function h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function render_task_markup(?array $taskDisplay): string
{
    if (!is_array($taskDisplay)) {
        return '';
    }

    $badgeClass = trim((string)($taskDisplay['class'] ?? ''));
    $label = (string)($taskDisplay['label'] ?? '');
    $detail = (string)($taskDisplay['detail'] ?? '');
    $progressTotal = (int)($taskDisplay['progress_total'] ?? 0);
    $progressPercent = max(0.0, min(100.0, (float)($taskDisplay['progress_percent'] ?? 0.0)));
    $batchRunning = (bool)($taskDisplay['batch_running'] ?? false);

    ob_start();
    ?>
    <div class="task-row">
        <span class="task-badge <?php echo h($badgeClass); ?>"><?php echo h($label); ?></span>
        <?php if ($detail !== ''): ?>
            <span class="task-detail"><?php echo h($detail); ?></span>
        <?php endif; ?>
    </div>
    <?php if ($progressTotal > 0): ?>
        <div class="task-progress <?php echo $batchRunning ? 'task-progress-live' : ''; ?>" aria-hidden="true">
            <div class="task-progress-bar <?php echo h($badgeClass); ?>" style="width: <?php echo h(number_format($progressPercent, 1, '.', '')); ?>%;"></div>
        </div>
    <?php endif; ?>
    <?php
    return trim((string)ob_get_clean());
}

$municipalities = municipality_registry();
$featureLabels = [
    'boards' => 'ポスター掲示場',
    'gijiroku' => '会議録',
    'reiki' => '例規集',
];
$backgroundTaskStatuses = [
    'gijiroku' => load_background_task_status('gijiroku'),
    'reiki' => load_background_task_status('reiki'),
];
$activeBackgroundSummaries = [];
$runningTaskGroups = [];
foreach (['gijiroku' => '会議録', 'reiki' => '例規集'] as $featureKey => $label) {
    $taskStatus = $backgroundTaskStatuses[$featureKey] ?? [];
    if (!is_array($taskStatus) || empty($taskStatus) || empty($taskStatus['running'])) {
        continue;
    }
    $activeBackgroundSummaries[] = sprintf(
        '%sスクレイピング: %d/%d 完了',
        $label,
        (int)($taskStatus['completed_count'] ?? 0),
        (int)($taskStatus['total_count'] ?? 0)
    );

    $items = $taskStatus['items'] ?? [];
    $runningItems = [];
    if (is_array($items)) {
        foreach ($items as $item) {
            if (!is_array($item) || (string)($item['status'] ?? '') !== 'running') {
                continue;
            }
            $runningItems[] = [
                'name' => (string)($item['name'] ?? $item['slug'] ?? ''),
                'slug' => (string)($item['slug'] ?? ''),
                'message' => (string)($item['message'] ?? ''),
            ];
        }
    }
    if ($runningItems !== []) {
        $runningTaskGroups[] = [
            'label' => $label,
            'items' => $runningItems,
        ];
    }
}
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
        .activity-board {
            display: grid;
            gap: 12px;
            padding: 14px 16px;
            border-radius: 18px;
            border: 1px solid rgba(17, 78, 114, 0.18);
            background:
                linear-gradient(180deg, rgba(255,255,255,0.9), rgba(241,247,251,0.96)),
                var(--accent-soft);
        }
        .activity-head {
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }
        .activity-title {
            font-size: 14px;
            font-weight: 700;
            color: var(--accent);
        }
        .activity-note {
            font-size: 12px;
            color: var(--muted);
        }
        .activity-groups {
            display: grid;
            gap: 10px;
        }
        .activity-group {
            display: grid;
            gap: 7px;
        }
        .activity-group-label {
            font-size: 13px;
            font-weight: 700;
            color: #1f2937;
        }
        .activity-chip-list {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }
        .activity-chip {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            max-width: 100%;
            padding: 8px 11px;
            border-radius: 999px;
            background: rgba(29, 78, 216, 0.08);
            border: 1px solid rgba(29, 78, 216, 0.18);
        }
        .activity-chip::before {
            content: "";
            width: 10px;
            height: 10px;
            border-radius: 999px;
            border: 2px solid #1d4ed8;
            border-right-color: transparent;
            animation: spin 0.8s linear infinite;
            flex: 0 0 auto;
        }
        .activity-chip-label {
            font-size: 13px;
            font-weight: 700;
            color: #1d4ed8;
            white-space: nowrap;
        }
        .activity-chip-detail {
            font-size: 12px;
            color: var(--muted);
            max-width: 20rem;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
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
        .municipality-card-live {
            border-color: rgba(29, 78, 216, 0.28);
            box-shadow: 0 18px 38px rgba(29, 78, 216, 0.12);
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
        .task-row {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            align-items: center;
            margin-top: 2px;
        }
        .task-slot {
            display: grid;
            gap: 7px;
        }
        .task-badge {
            flex: 0 0 auto;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 9px;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 700;
        }
        .task-running {
            color: #1d4ed8;
            background: rgba(29, 78, 216, 0.1);
        }
        .task-running::before {
            content: "";
            width: 10px;
            height: 10px;
            border-radius: 999px;
            border: 2px solid currentColor;
            border-right-color: transparent;
            animation: spin 0.8s linear infinite;
            flex: 0 0 auto;
        }
        .task-pending {
            color: #7c3aed;
            background: rgba(124, 58, 237, 0.1);
        }
        .task-done {
            color: var(--ok);
            background: rgba(15, 118, 110, 0.1);
        }
        .task-failed {
            color: #b45309;
            background: rgba(180, 83, 9, 0.14);
        }
        .task-stale {
            color: #b91c1c;
            background: rgba(185, 28, 28, 0.12);
        }
        .task-detail {
            font-size: 12px;
            line-height: 1.6;
            color: var(--muted);
        }
        .task-progress {
            position: relative;
            width: 100%;
            height: 7px;
            border-radius: 999px;
            overflow: hidden;
            background: rgba(148, 163, 184, 0.18);
        }
        .task-progress-bar {
            height: 100%;
            border-radius: inherit;
            transition: width 0.35s ease;
        }
        .task-progress-bar.task-running {
            background: linear-gradient(90deg, #2563eb, #60a5fa);
        }
        .task-progress-bar.task-pending {
            background: linear-gradient(90deg, #8b5cf6, #c4b5fd);
        }
        .task-progress-bar.task-done {
            background: linear-gradient(90deg, #0f766e, #34d399);
        }
        .task-progress-bar.task-failed,
        .task-progress-bar.task-stale {
            background: linear-gradient(90deg, #b45309, #f59e0b);
        }
        .task-progress-live .task-progress-bar {
            background-size: 24px 24px;
            background-image:
                linear-gradient(
                    135deg,
                    rgba(255, 255, 255, 0.22) 25%,
                    transparent 25%,
                    transparent 50%,
                    rgba(255, 255, 255, 0.22) 50%,
                    rgba(255, 255, 255, 0.22) 75%,
                    transparent 75%,
                    transparent
                ),
                inherit;
            animation: progress-stripes 1.2s linear infinite;
        }
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        @keyframes progress-stripes {
            from { background-position: 0 0; }
            to { background-position: 24px 0; }
        }
        @media (max-width: 720px) {
            body { padding: 20px 12px 32px; }
            .hero, .municipality-card { padding: 18px; border-radius: 18px; }
            .municipality-grid { grid-template-columns: 1fr; }
            .municipality-head, .feature-top { flex-direction: column; align-items: start; }
            .activity-chip { max-width: 100%; }
            .activity-chip-detail { max-width: 12rem; }
        }
    </style>
</head>
<body>
    <div class="shell">
        <section class="hero">
            <div class="eyebrow">Municipal Data Hub</div>
            <h1>自治体ごとの公開情報を、<br>ひとつの入口で</h1>
            <div class="hero-copy">
                ポスター掲示場、会議録、例規集を自治体単位で整理しています。使える機能はすぐ開けて、準備中のものは進捗つきで追えます。
            </div>
            <div class="hero-meta">
                <span>自治体数: <?php echo h((string)count($municipalities)); ?></span>
                <span>切り替え単位: `slug`</span>
                <span>データ参照: 共通レジストリ管理</span>
                <?php foreach (['gijiroku' => '会議録', 'reiki' => '例規集'] as $featureKey => $label): ?>
                    <?php $taskStatus = $backgroundTaskStatuses[$featureKey] ?? []; ?>
                    <?php $summaryText = sprintf('%sスクレイピング: %d/%d 完了', $label, (int)($taskStatus['completed_count'] ?? 0), (int)($taskStatus['total_count'] ?? 0)); ?>
                    <span data-task-summary="<?php echo h($featureKey); ?>"<?php echo empty($taskStatus['running']) ? ' hidden' : ''; ?>><?php echo h($summaryText); ?></span>
                <?php endforeach; ?>
            </div>
            <div class="activity-board" id="activity-board"<?php echo $runningTaskGroups === [] ? ' hidden' : ''; ?>>
                    <div class="activity-head">
                        <div class="activity-title">現在実行中のスクレイピング</div>
                        <div class="activity-note">ぐるぐる表示の自治体が、いま実際に処理されている対象です。</div>
                    </div>
                    <div class="activity-groups" id="activity-groups">
                        <?php foreach ($runningTaskGroups as $group): ?>
                            <div class="activity-group">
                                <div class="activity-group-label"><?php echo h((string)$group['label']); ?></div>
                                <div class="activity-chip-list">
                                    <?php foreach ((array)$group['items'] as $item): ?>
                                        <div class="activity-chip" title="<?php echo h((string)($item['slug'] ?? '')); ?>">
                                            <span class="activity-chip-label"><?php echo h((string)($item['name'] ?? '')); ?></span>
                                            <?php if ((string)($item['message'] ?? '') !== ''): ?>
                                                <span class="activity-chip-detail"><?php echo h((string)$item['message']); ?></span>
                                            <?php endif; ?>
                                        </div>
                                    <?php endforeach; ?>
                                </div>
                            </div>
                        <?php endforeach; ?>
                    </div>
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
                            <?php $taskDisplay = null; ?>
                            <?php if (isset($backgroundTaskStatuses[$featureKey]) && is_array($backgroundTaskStatuses[$featureKey])): ?>
                                <?php $taskDisplay = background_task_item_display($backgroundTaskStatuses[$featureKey], $slug); ?>
                            <?php endif; ?>
                            <?php if (!empty($feature['enabled'])): ?>
                                <a class="feature-link" href="<?php echo h((string)$feature['url']); ?>">
                                    <div class="feature-top">
                                        <div class="feature-title"><?php echo h($label); ?></div>
                                        <span class="status status-ready">利用可能</span>
                                    </div>
                                    <div class="feature-desc"><?php echo h((string)($feature['title'] ?? '')); ?></div>
                                    <div class="task-slot" data-task-slot="<?php echo h($featureKey); ?>" data-slug="<?php echo h($slug); ?>"><?php echo render_task_markup($taskDisplay); ?></div>
                                </a>
                            <?php else: ?>
                                <div class="feature-disabled">
                                    <div class="feature-top">
                                        <div class="feature-title"><?php echo h($label); ?></div>
                                        <span class="status status-pending">準備中</span>
                                    </div>
                                    <div class="feature-desc"><?php echo h((string)($feature['title'] ?? ($municipality['name'] . $label))); ?></div>
                                    <div class="task-slot" data-task-slot="<?php echo h($featureKey); ?>" data-slug="<?php echo h($slug); ?>"><?php echo render_task_markup($taskDisplay); ?></div>
                                </div>
                            <?php endif; ?>
                        <?php endforeach; ?>
                    </div>
                </article>
            <?php endforeach; ?>
        </section>
    </div>
    <script>
        (() => {
            const taskLabels = { gijiroku: '会議録', reiki: '例規集' };
            const summaryElements = {};
            document.querySelectorAll('[data-task-summary]').forEach((element) => {
                summaryElements[element.dataset.taskSummary] = element;
            });

            function escapeHtml(value) {
                return String(value ?? '')
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;')
                    .replace(/'/g, '&#039;');
            }

            function taskSource(featureKey) {
                return `/data/background_tasks/${featureKey}.json`;
            }

            async function loadTaskStatus(featureKey) {
                try {
                    const response = await fetch(`${taskSource(featureKey)}?t=${Date.now()}`, { cache: 'no-store' });
                    if (!response.ok) {
                        return {};
                    }
                    return await response.json();
                } catch (error) {
                    return {};
                }
            }

            function computeTaskDisplay(taskStatus, slug) {
                const items = taskStatus?.items;
                const item = items && typeof items === 'object' ? items[slug] : null;
                if (!item || typeof item !== 'object') {
                    return null;
                }

                const status = String(item.status || '').trim();
                const running = Boolean(taskStatus?.running);
                const updatedAt = Date.parse(String(taskStatus?.updated_at || ''));
                const stale = running && Number.isFinite(updatedAt) && ((Date.now() - updatedAt) / 1000 > 900);
                const totalCount = Number(taskStatus?.total_count || 0);
                const completedCount = Number(taskStatus?.completed_count || 0);
                const progressPercent = totalCount > 0 ? Math.max(0, Math.min(100, (completedCount / totalCount) * 100)) : 0;
                const timeLabel = String(item.finished_at || item.updated_at || taskStatus?.updated_at || '').trim();
                const detailParts = [];
                if (running && totalCount > 0) {
                    detailParts.push(`バッチ ${completedCount}/${totalCount} 完了`);
                }
                if (timeLabel) {
                    detailParts.push(`更新 ${timeLabel}`);
                }
                let detail = detailParts.join(' / ');

                if (stale && (status === 'pending' || status === 'running')) {
                    return { label: '停止の可能性', className: 'task-stale', detail, batchRunning: running, progressPercent, progressTotal: totalCount };
                }
                if (running && status === 'running') {
                    return { label: 'スクレイピング中', className: 'task-running', detail, batchRunning: running, progressPercent, progressTotal: totalCount };
                }
                if (running && status === 'pending') {
                    return { label: '待機中', className: 'task-pending', detail, batchRunning: running, progressPercent, progressTotal: totalCount };
                }
                if (status === 'done' || status === 'ok') {
                    return { label: '直近完了', className: 'task-done', detail, batchRunning: running, progressPercent, progressTotal: totalCount };
                }
                if (status === 'failed') {
                    if (item.returncode !== undefined && item.returncode !== null && String(item.returncode) !== '') {
                        detail = `${detail}${detail ? ' / ' : ''}rc=${item.returncode}`;
                    }
                    return { label: '直近失敗', className: 'task-failed', detail, batchRunning: running, progressPercent, progressTotal: totalCount };
                }
                return null;
            }

            function renderTaskMarkup(display) {
                if (!display) {
                    return '';
                }
                const progressBar = display.progressTotal > 0
                    ? `<div class="task-progress ${display.batchRunning ? 'task-progress-live' : ''}" aria-hidden="true"><div class="task-progress-bar ${escapeHtml(display.className)}" style="width:${display.progressPercent.toFixed(1)}%;"></div></div>`
                    : '';
                return `
                    <div class="task-row">
                        <span class="task-badge ${escapeHtml(display.className)}">${escapeHtml(display.label)}</span>
                        ${display.detail ? `<span class="task-detail">${escapeHtml(display.detail)}</span>` : ''}
                    </div>
                    ${progressBar}
                `.trim();
            }

            function updateHeroSummaries(statuses) {
                Object.entries(taskLabels).forEach(([featureKey, label]) => {
                    const taskStatus = statuses[featureKey] || {};
                    const element = summaryElements[featureKey];
                    if (!element) {
                        return;
                    }
                    const running = Boolean(taskStatus.running);
                    element.hidden = !running;
                    if (running) {
                        element.textContent = `${label}スクレイピング: ${Number(taskStatus.completed_count || 0)}/${Number(taskStatus.total_count || 0)} 完了`;
                    }
                });
            }

            function updateActivityBoard(statuses) {
                const board = document.getElementById('activity-board');
                const groupsContainer = document.getElementById('activity-groups');
                if (!board || !groupsContainer) {
                    return;
                }

                const groups = Object.entries(taskLabels)
                    .map(([featureKey, label]) => {
                        const taskStatus = statuses[featureKey] || {};
                        const items = taskStatus?.items && typeof taskStatus.items === 'object' ? Object.values(taskStatus.items) : [];
                        const runningItems = items.filter((item) => item && item.status === 'running');
                        return {
                            label,
                            items: runningItems,
                        };
                    })
                    .filter((group) => group.items.length > 0);

                board.hidden = groups.length === 0;
                if (groups.length === 0) {
                    groupsContainer.innerHTML = '';
                    return;
                }

                groupsContainer.innerHTML = groups.map((group) => `
                    <div class="activity-group">
                        <div class="activity-group-label">${escapeHtml(group.label)}</div>
                        <div class="activity-chip-list">
                            ${group.items.map((item) => `
                                <div class="activity-chip" title="${escapeHtml(item.slug || '')}">
                                    <span class="activity-chip-label">${escapeHtml(item.name || item.slug || '')}</span>
                                    ${item.message ? `<span class="activity-chip-detail">${escapeHtml(item.message)}</span>` : ''}
                                </div>
                            `).join('')}
                        </div>
                    </div>
                `).join('');
            }

            function updateTaskSlots(statuses) {
                document.querySelectorAll('[data-task-slot]').forEach((slot) => {
                    const featureKey = slot.dataset.taskSlot || '';
                    const slug = slot.dataset.slug || '';
                    const taskStatus = statuses[featureKey] || {};
                    const display = computeTaskDisplay(taskStatus, slug);
                    slot.innerHTML = renderTaskMarkup(display);
                });

                document.querySelectorAll('.municipality-card').forEach((card) => {
                    const hasRunning = card.querySelector('.task-badge.task-running') !== null;
                    card.classList.toggle('municipality-card-live', hasRunning);
                });
            }

            let refreshing = false;
            async function refreshStatuses() {
                if (refreshing) {
                    return;
                }
                refreshing = true;
                try {
                    const [gijiroku, reiki] = await Promise.all([
                        loadTaskStatus('gijiroku'),
                        loadTaskStatus('reiki'),
                    ]);
                    const statuses = { gijiroku, reiki };
                    updateHeroSummaries(statuses);
                    updateActivityBoard(statuses);
                    updateTaskSlots(statuses);
                } finally {
                    refreshing = false;
                }
            }

            refreshStatuses();
            window.setInterval(refreshStatuses, 5000);
        })();
    </script>
</body>
</html>
