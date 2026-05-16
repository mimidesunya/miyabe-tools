(() => {
    const apiUrl = String(window.HOMEPAGE_API_URL || '/api/home.php');
    const runningSection = document.querySelector('[data-running-section]');
    const runningSummaryList = document.querySelector('[data-running-summary-list]');
    const runningList = document.querySelector('[data-running-list]');
    const grid = document.querySelector('[data-home-grid]');
    const loadingPanel = document.querySelector('[data-home-loading]');
    const filterSection = document.querySelector('[data-home-filter-section]');
    const filterSelect = document.querySelector('[data-home-prefecture-filter]');
    const filterHint = document.querySelector('[data-home-filter-hint]');
    const displayCountElement = document.querySelector('[data-home-display-count]');
    const municipalityCountElement = document.querySelector('[data-home-municipality-count]');
    const generatedAtElement = document.querySelector('[data-home-generated-at]');
    const taskSummariesElement = document.querySelector('[data-home-task-summaries]');
    const defaultPrefecture = '神奈川県';
    let latestPayload = null;
    let selectedPrefecture = '';

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

    function progressCountText(display, label) {
        const rawCurrent = Number(display?.progress_current ?? NaN);
        const rawTotal = Number(display?.progress_total ?? NaN);
        if (!Number.isFinite(rawCurrent) || !Number.isFinite(rawTotal) || rawTotal <= 0) {
            return '';
        }
        const current = Math.max(0, Math.min(rawCurrent, rawTotal));
        return `${label} ${Math.round(current)}/${Math.round(rawTotal)}件`;
    }

    function renderTaskMarkup(display, options = {}) {
        if (!display || typeof display !== 'object') {
            return '';
        }

        const countLabel = String(options.countLabel || 'DL済');
        const countText = progressCountText(display, countLabel);
        const detailLines = String(display.detail || '')
            .split(/\r?\n/)
            .map((line) => line.trim())
            .filter((line) => countText === '' || !/^(DL済|投入済|反映)?\s*\d+(?:\/\d+)?件$/.test(line))
            .filter((line) => line !== '');
        const rawCurrent = Number(display.progress_current ?? NaN);
        const rawTotal = Number(display.progress_total ?? NaN);
        const hasProgress = Number.isFinite(rawCurrent) && Number.isFinite(rawTotal) && rawTotal > 0;
        const current = hasProgress ? Math.max(0, Math.min(rawCurrent, rawTotal)) : 0;
        const width = hasProgress ? (current / rawTotal) * 100 : 0;

        return `
            <div class="task-row">
                <span class="task-state-line">
                    <span class="task-badge ${escapeHtml(display.class || '')}">${escapeHtml(display.label || '')}</span>
                    ${countText ? `<span class="task-count">${escapeHtml(countText)}</span>` : ''}
                </span>
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
                <span class="running-name">${escapeHtml(entry.municipality_name || '')}</span>
                ${renderTaskMarkup(display, { countLabel: entry?.task_key === 'search_rebuild' ? '投入済' : 'DL済' })}
            </div>
        `.trim();
    }

    function renderRunningSummaryCard(entry) {
        if (!entry || typeof entry !== 'object') {
            return '';
        }

        const stats = Array.isArray(entry.stats) ? entry.stats.filter((item) => item && item.label && item.value) : [];
        const tasks = Array.isArray(entry.tasks) ? entry.tasks.filter((item) => item && item.display) : [];
        if (stats.length === 0 && tasks.length === 0) {
            return '';
        }

        return `
            <div class="running-summary-card">
                <div class="running-summary-top">
                    <span class="running-service">${renderFeatureIdentity(entry.icon, entry.label)}</span>
                    <span class="running-summary-state ${escapeHtml(entry.state_class || '')}">${escapeHtml(entry.state_label || '')}</span>
                </div>
                <div class="running-summary-stats">
                    ${stats.map((item) => `
                        <span class="running-summary-stat">
                            <span class="running-summary-stat-label">${escapeHtml(item.label || '')}</span>
                            <span class="running-summary-stat-value">${escapeHtml(item.value || '')}</span>
                        </span>
                    `.trim()).join('')}
                </div>
                ${tasks.length ? `<div class="running-summary-tasks">${tasks.map(renderRunningTask).join('')}</div>` : ''}
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
                        <div class="municipality-note municipality-availability" title="${escapeHtml(`表示中: ${card.available_summary || ''}`)}">
                            ${escapeHtml(`${Number(card.ready_visible_count || 0)}/${Number(card.feature_count || 0)} 検索可`)}
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

    function renderFeatureSummaries(featureSummaries) {
        const summaries = Array.isArray(featureSummaries) ? featureSummaries.filter((item) => item) : [];
        if (summaries.length === 0) {
            return '';
        }
        return summaries.map((item) => `<span>${escapeHtml(item.text || '')}</span>`).join('');
    }

    function readSelectedPrefecture() {
        try {
            const params = new URLSearchParams(window.location.search);
            return String(params.get('prefecture') || '').trim();
        } catch (error) {
            return '';
        }
    }

    function writeSelectedPrefecture(prefectureLabel) {
        try {
            const url = new URL(window.location.href);
            if (prefectureLabel === 'all') {
                url.searchParams.delete('prefecture');
            } else {
                url.searchParams.set('prefecture', prefectureLabel);
            }
            window.history.replaceState(null, '', url.toString());
        } catch (error) {
            console.warn('failed to update prefecture filter state', error);
        }
    }

    function collectPrefectureOptions(groups) {
        return groups
            .map((group) => String(group?.label || '').trim())
            .filter((label) => label !== '');
    }

    function syncPrefectureFilter(groups) {
        const options = collectPrefectureOptions(groups);
        const requested = selectedPrefecture === '' ? readSelectedPrefecture() : selectedPrefecture;
        const preferred = requested !== '' ? requested : defaultPrefecture;
        const nextSelection = options.includes(preferred) ? preferred : 'all';
        selectedPrefecture = nextSelection;

        if (!filterSection || !filterSelect) {
            return;
        }

        filterSection.hidden = options.length <= 1;
        filterSelect.innerHTML = [
            '<option value="all">すべての都道府県</option>',
            ...options.map((label) => `<option value="${escapeHtml(label)}">${escapeHtml(label)}</option>`),
        ].join('');
        filterSelect.value = nextSelection;
    }

    function filterGroups(groups) {
        if (selectedPrefecture === 'all') {
            return groups;
        }
        return groups.filter((group) => String(group?.label || '') === selectedPrefecture);
    }

    function countCards(groups) {
        return groups.reduce((sum, group) => sum + (Array.isArray(group?.cards) ? group.cards.length : 0), 0);
    }

    function renderGrid(municipalities) {
        const groups = groupMunicipalitiesByPrefecture(municipalities);
        syncPrefectureFilter(groups);
        const visibleGroups = filterGroups(groups);
        const visibleCount = countCards(visibleGroups);
        const totalCount = municipalities.length;

        if (displayCountElement) {
            displayCountElement.textContent = selectedPrefecture === 'all'
                ? `表示自治体: ${visibleCount}`
                : `表示自治体: ${visibleCount} / ${totalCount}`;
        }
        if (filterHint) {
            filterHint.textContent = selectedPrefecture === 'all'
                ? `全 ${totalCount} 自治体を都道府県ごとに表示しています。`
                : `${selectedPrefecture} の ${visibleCount} 自治体を表示しています。`;
        }
        writeSelectedPrefecture(selectedPrefecture);

        if (!grid) {
            return;
        }

        if (visibleCount === 0) {
            grid.innerHTML = '<div class="loading-panel">選択中の都道府県で表示できる自治体はありません。</div>';
            return;
        }

        grid.innerHTML = visibleGroups.map(renderPrefectureSection).join('');
    }

    function renderPayload(payload) {
        const municipalities = Array.isArray(payload?.municipalities) ? payload.municipalities : [];
        const runningTasks = Array.isArray(payload?.running_tasks) ? payload.running_tasks : [];
        const taskStateSummaries = Array.isArray(payload?.task_state_summaries) ? payload.task_state_summaries : [];
        latestPayload = payload;

        if (municipalityCountElement) {
            municipalityCountElement.textContent = `自治体マスタ: ${Number(payload?.municipality_count || 0)}`;
        }
        if (generatedAtElement) {
            generatedAtElement.textContent = `更新: ${String(payload?.generated_at || '不明')}`;
        }
        if (taskSummariesElement) {
            taskSummariesElement.innerHTML = renderFeatureSummaries(payload?.feature_summaries);
        }

        if (runningSection && runningList) {
            const tasksByKey = new Map();
            runningTasks.forEach((task) => {
                const key = String(task?.task_key || '');
                if (key === '') return;
                if (!tasksByKey.has(key)) tasksByKey.set(key, []);
                tasksByKey.get(key).push(task);
            });
            const summaryCards = taskStateSummaries.map((summary) => ({
                ...summary,
                tasks: Array.isArray(summary?.tasks)
                    ? summary.tasks
                    : (tasksByKey.get(String(summary?.task_key || '')) || []),
            }));
            runningSection.hidden = summaryCards.length === 0;
            if (runningSummaryList) {
                runningSummaryList.innerHTML = summaryCards.map(renderRunningSummaryCard).join('');
            }
            runningList.innerHTML = '';
            runningList.hidden = true;
        }

        if (!grid) {
            return;
        }
        if (loadingPanel) {
            loadingPanel.remove();
        }

        if (municipalities.length === 0) {
            if (displayCountElement) {
                displayCountElement.textContent = '表示自治体: 0';
            }
            if (filterSection) {
                filterSection.hidden = true;
            }
            grid.innerHTML = '<div class="loading-panel">表示できる自治体はまだありません。</div>';
            return;
        }

        renderGrid(municipalities);
    }

    async function loadPayload() {
        const response = await fetch(apiUrl);
        const responseText = await response.text();
        let payload;
        try {
            payload = JSON.parse(responseText);
        } catch (error) {
            throw new Error(`Invalid JSON from homepage API (HTTP ${response.status})`);
        }
        if (!response.ok) {
            const apiError = payload && typeof payload === 'object' ? payload.error : '';
            throw new Error(String(apiError || `HTTP ${response.status}`));
        }
        return payload;
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
        } catch (error) {
            // 利用者向け文言は抑えめにしつつ、実原因は console で追えるようにする。
            console.error('homepage refresh failed', error);
            if (loadingPanel && loadingPanel.isConnected) {
                loadingPanel.textContent = '自治体一覧の読み込みに失敗しました。しばらくしてから再度お試しください。';
            }
        } finally {
            refreshing = false;
        }
    }

    if (filterSelect) {
        filterSelect.addEventListener('change', () => {
            selectedPrefecture = String(filterSelect.value || 'all');
            if (latestPayload && typeof latestPayload === 'object') {
                renderGrid(Array.isArray(latestPayload.municipalities) ? latestPayload.municipalities : []);
            }
        });
    }

    refresh();
    window.setInterval(refresh, 5000);
})();
