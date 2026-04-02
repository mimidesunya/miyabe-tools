(() => {
    const apiUrl = String(window.HOMEPAGE_API_URL || '/api/home.php');
    const runningSection = document.querySelector('[data-running-section]');
    const runningList = document.querySelector('[data-running-list]');
    const grid = document.querySelector('[data-home-grid]');
    const loadingPanel = document.querySelector('[data-home-loading]');
    const displayCountElement = document.querySelector('[data-home-display-count]');
    const municipalityCountElement = document.querySelector('[data-home-municipality-count]');
    const generatedAtElement = document.querySelector('[data-home-generated-at]');
    const taskSummariesElement = document.querySelector('[data-home-task-summaries]');

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function progressClass(display) {
        switch (String(display?.class || '')) {
            case 'task-done':
                return 'task-progress-done';
            case 'task-failed':
            case 'task-stale':
                return 'task-progress-alert';
            default:
                return 'task-progress-active';
        }
    }

    function renderFeatureIdentity(icon, label) {
        const iconText = String(icon || '').trim();
        const labelText = String(label || '').trim();
        const parts = [];
        if (iconText !== '') {
            parts.push(`<span class="feature-icon-mark" aria-hidden="true">${escapeHtml(iconText)}</span>`);
        }
        if (labelText !== '') {
            parts.push(`<span class="feature-label-text">${escapeHtml(labelText)}</span>`);
        }
        return parts.join('');
    }

    function renderTaskMarkup(display) {
        if (!display || typeof display !== 'object') {
            return '';
        }

        const detailLines = String(display.detail || '')
            .split(/\r?\n/)
            .map((line) => line.trim())
            .filter((line) => line !== '');
        const rawCurrent = Number(display.progress_current ?? NaN);
        const rawTotal = Number(display.progress_total ?? NaN);
        const hasProgress = Number.isFinite(rawCurrent) && Number.isFinite(rawTotal) && rawTotal > 0;
        const current = hasProgress ? Math.max(0, Math.min(rawCurrent, rawTotal)) : 0;
        const width = hasProgress ? (current / rawTotal) * 100 : 0;

        return `
            <div class="task-row">
                <span class="task-badge ${escapeHtml(display.class || '')}">${escapeHtml(display.label || '')}</span>
                ${hasProgress ? `<span class="task-progress ${escapeHtml(progressClass(display))}" aria-hidden="true"><span class="task-progress-bar" style="width: ${width.toFixed(2)}%"></span></span>` : ''}
                ${detailLines.length ? `<span class="task-detail">${detailLines.map((line) => `<span class="task-detail-line">${escapeHtml(line)}</span>`).join('')}</span>` : ''}
            </div>
        `.trim();
    }

    function renderRunningTask(entry) {
        const display = entry?.display;
        if (!display || typeof display !== 'object') {
            return '';
        }

        return `
            <div class="running-item">
                <span class="running-service">${renderFeatureIdentity(entry?.feature_icon, entry?.feature_label)}</span>
                <span class="running-name">${escapeHtml(entry.municipality_name || '')}</span>
                ${renderTaskMarkup(display)}
            </div>
        `.trim();
    }

    function renderFeature(feature) {
        const classes = String(feature.mode || '') === 'link' ? 'feature-card' : 'feature-disabled';
        const actionMarkup = String(feature.mode || '') === 'link'
            ? `<a class="feature-action" href="${escapeHtml(feature.url || '')}" target="_blank" rel="noopener">開く</a>`
            : '';

        return `
            <div class="${classes}" title="${escapeHtml(feature.title || '')}">
                <div class="feature-top">
                    <div class="feature-title">${renderFeatureIdentity(feature.icon, feature.label)}</div>
                    <div class="feature-actions">
                        <span class="status ${escapeHtml(feature.status_class || '')}">${escapeHtml(feature.status_label || '')}</span>
                        ${actionMarkup}
                    </div>
                </div>
                ${renderTaskMarkup(feature.display)}
            </div>
        `.trim();
    }

    function renderMunicipalityCard(card) {
        const features = Array.isArray(card.features) ? card.features : [];
        return `
            <article class="municipality-card ${features.some((feature) => String(feature?.display?.class || '') === 'task-running') ? 'municipality-card-live' : ''}">
                <div class="municipality-head">
                    <h2 class="municipality-name">${escapeHtml(card.name || '')}</h2>
                    <div class="municipality-meta">
                        <div class="municipality-note">${escapeHtml(card.slug || '')}</div>
                        <div class="municipality-note municipality-availability" title="${escapeHtml(`表示中: ${card.available_summary || ''}`)}">
                            ${escapeHtml(`${Number(card.ready_visible_count || 0)}/${Number(card.feature_count || 0)} 利用可`)}
                        </div>
                    </div>
                </div>
                <div class="feature-list">
                    ${features.map(renderFeature).join('')}
                </div>
            </article>
        `.trim();
    }

    function groupMunicipalitiesByPrefecture(municipalities) {
        const groups = [];
        const groupMap = new Map();
        municipalities.forEach((card) => {
            const prefectureLabel = String(card?.prefecture_label || 'その他').trim() || 'その他';
            if (!groupMap.has(prefectureLabel)) {
                const group = { label: prefectureLabel, cards: [] };
                groupMap.set(prefectureLabel, group);
                groups.push(group);
            }
            groupMap.get(prefectureLabel).cards.push(card);
        });
        return groups;
    }

    function renderPrefectureSection(group) {
        const cards = Array.isArray(group?.cards) ? group.cards : [];
        return `
            <section class="prefecture-section">
                <div class="prefecture-head">
                    <h2 class="prefecture-title">${escapeHtml(group?.label || 'その他')}</h2>
                    <span class="prefecture-count">${escapeHtml(`${cards.length}自治体`)}</span>
                </div>
                <div class="prefecture-grid">
                    ${cards.map(renderMunicipalityCard).join('')}
                </div>
            </section>
        `.trim();
    }

    function renderTaskSummaries(taskSummaries) {
        const summaries = Array.isArray(taskSummaries) ? taskSummaries.filter((item) => item && item.running) : [];
        if (summaries.length === 0) {
            return '';
        }
        return summaries.map((item) => `<span>${escapeHtml(item.text || '')}</span>`).join('');
    }

    function renderPayload(payload) {
        const municipalities = Array.isArray(payload?.municipalities) ? payload.municipalities : [];
        const runningTasks = Array.isArray(payload?.running_tasks) ? payload.running_tasks : [];

        if (displayCountElement) {
            displayCountElement.textContent = `表示自治体: ${Number(payload?.display_municipality_count || 0)}`;
        }
        if (municipalityCountElement) {
            municipalityCountElement.textContent = `自治体マスタ: ${Number(payload?.municipality_count || 0)}`;
        }
        if (generatedAtElement) {
            generatedAtElement.textContent = `更新: ${String(payload?.generated_at || '不明')}`;
        }
        if (taskSummariesElement) {
            taskSummariesElement.innerHTML = renderTaskSummaries(payload?.task_summaries);
        }

        if (runningSection && runningList) {
            runningSection.hidden = runningTasks.length === 0;
            runningList.innerHTML = runningTasks.map(renderRunningTask).join('');
        }

        if (!grid) {
            return;
        }
        if (loadingPanel) {
            loadingPanel.remove();
        }

        if (municipalities.length === 0) {
            grid.innerHTML = '<div class="loading-panel">表示できる自治体はまだありません。</div>';
            return;
        }

        grid.innerHTML = groupMunicipalitiesByPrefecture(municipalities).map(renderPrefectureSection).join('');
    }

    async function loadPayload() {
        const response = await fetch(`${apiUrl}?t=${Date.now()}`, { cache: 'no-store' });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        return await response.json();
    }

    let refreshing = false;
    async function refresh() {
        if (refreshing) {
            return;
        }
        refreshing = true;
        try {
            const payload = await loadPayload();
            renderPayload(payload);
        } catch (_error) {
            if (loadingPanel && loadingPanel.isConnected) {
                loadingPanel.textContent = '自治体一覧の読み込みに失敗しました。しばらくしてから再度お試しください。';
            }
        } finally {
            refreshing = false;
        }
    }

    refresh();
    window.setInterval(refresh, 5000);
})();
