(() => {
    const bootNode = document.getElementById('minutes-cross-boot');
    if (!bootNode) {
        return;
    }

    const boot = JSON.parse(bootNode.textContent || '{}');
    const municipalities = Array.isArray(boot.municipalities)
        ? boot.municipalities
            .map((item, index) => ({
                slug: String(item.slug || ''),
                code: String(item.code || ''),
                name: String(item.name || ''),
                assembly_name: String(item.assembly_name || item.name || ''),
                url: String(item.url || ''),
                index,
            }))
            .filter((item) => item.slug)
        : [];

    const municipalityBySlug = new Map(municipalities.map((item) => [item.slug, item]));
    const selectedFromBoot = String(boot.selectedSlug || '');
    const mixedResultLimit = 100;

    const refs = {
        form: document.getElementById('cross-search-form'),
        query: document.getElementById('cross-query'),
        searchButton: document.getElementById('cross-search-button'),
        progressCopy: document.getElementById('search-progress-copy'),
        progressBar: document.getElementById('search-progress-bar'),
        progressSummary: document.getElementById('search-progress-summary'),
        progressPanel: document.querySelector('.progress-panel'),
        activeCount: document.getElementById('search-active-count'),
        activeList: document.getElementById('search-active-list'),
        municipalityList: document.getElementById('municipality-list'),
        selectedTitle: document.getElementById('selected-title'),
        selectedMeta: document.getElementById('selected-meta'),
        selectedMixedButton: document.getElementById('selected-mixed-button'),
        selectedOpenLink: document.getElementById('selected-open-link'),
        resultsSummary: document.getElementById('results-summary'),
        resultsBody: document.getElementById('results-body'),
        resultsPagination: document.getElementById('results-pagination'),
    };

    const state = {
        apiUrl: String(boot.apiUrl || '/gijiroku/api.php'),
        query: String(boot.query || '').trim(),
        selectedSlug: municipalityBySlug.has(selectedFromBoot) ? selectedFromBoot : '',
        searching: false,
        requestToken: 0,
        progressDone: 0,
        progressTotal: municipalities.length,
        results: new Map(),
        activeRequests: new Map(),
    };

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function renderExcerpt(value) {
        return escapeHtml(value).replace(/\[\[\[/g, '<mark>').replace(/\]\]\]/g, '</mark>').replace(/\n/g, '<br>');
    }

    function setActiveRequest(slug, phase, enabled) {
        if (!municipalityBySlug.has(slug)) {
            return;
        }
        if (enabled) {
            state.activeRequests.set(slug, {
                slug,
                phase,
                startedAt: Date.now(),
            });
            return;
        }
        state.activeRequests.delete(slug);
    }

    function activeRequestEntries() {
        return Array.from(state.activeRequests.values())
            .map((item) => ({
                ...item,
                municipality: municipalityBySlug.get(item.slug),
            }))
            .filter((item) => item.municipality)
            .sort((a, b) => a.startedAt - b.startedAt);
    }

    function renderActiveSearches() {
        const items = activeRequestEntries();
        if (refs.activeCount) {
            if (!state.query) {
                refs.activeCount.textContent = '待機中';
            } else if (items.length > 0) {
                refs.activeCount.textContent = `${items.length}件`;
            } else if (state.searching) {
                refs.activeCount.textContent = '調整中';
            } else {
                refs.activeCount.textContent = '完了';
            }
        }
        if (!refs.activeList) {
            return;
        }
        if (items.length === 0) {
            refs.activeList.innerHTML = state.searching
                ? `<div class="active-search-empty">${state.progressDone > 0 ? 'いま走っている自治体の応答を待っています。まもなく次の自治体へ進みます。' : '検索キューを準備しています。検索対象の自治体がここに順次並びます。'}</div>`
                : (state.query
                    ? '<div class="active-search-empty">検索は完了しました。ヒットした自治体を切り替えて確認できます。</div>'
                    : '<div class="active-search-empty">キーワードを入れると、ここに現在検索中の自治体が表示されます。</div>');
            return;
        }

        refs.activeList.innerHTML = items.map((item) => {
            const municipality = item.municipality;
            const phaseLabel = item.phase === 'detail' ? '詳細を展開中' : '自治体を走査中';
            return `
                <div class="active-search-chip">
                    <div class="active-search-chip-top">
                        <span class="active-search-dot" aria-hidden="true"></span>
                        <span class="active-search-name">${escapeHtml(municipality?.name || item.slug)}</span>
                    </div>
                    <div class="active-search-meta">${escapeHtml(phaseLabel)}</div>
                </div>
            `;
        }).join('');
    }

    function resultLatestHitDate(result) {
        const latest = String(result?.stats?.latest_hit_date || '');
        if (latest) {
            return latest;
        }
        const rows = Array.isArray(result?.preview_rows) && result.preview_rows.length > 0
            ? result.preview_rows
            : (Array.isArray(result?.rows) ? result.rows : []);
        return String(rows[0]?.held_on || '');
    }

    function compareRecentRows(a, b) {
        const aHeldOn = String(a?.held_on || '');
        const bHeldOn = String(b?.held_on || '');
        if (aHeldOn !== bHeldOn) {
            return bHeldOn.localeCompare(aHeldOn);
        }

        const aCode = String(a?.municipality_code || '');
        const bCode = String(b?.municipality_code || '');
        if (aCode !== bCode) {
            return aCode.localeCompare(bCode);
        }

        const aId = Number(a?.id || 0);
        const bId = Number(b?.id || 0);
        if (aId !== bId) {
            return bId - aId;
        }

        return String(a?.title || '').localeCompare(String(b?.title || ''));
    }

    function mixedResultsInfo() {
        const rows = [];
        let hasOverflow = false;

        for (const municipality of municipalities) {
            const result = state.results.get(municipality.slug);
            if (!result || result.status !== 'ok') {
                continue;
            }

            if (result.has_more) {
                hasOverflow = true;
            }

            const sourceRows = Array.isArray(result.preview_rows) && result.preview_rows.length > 0
                ? result.preview_rows
                : (Array.isArray(result.rows) ? result.rows : []);
            for (const row of sourceRows) {
                if (!row || typeof row !== 'object') {
                    continue;
                }
                rows.push({
                    ...row,
                    municipality_slug: String(row.municipality_slug || municipality.slug),
                    municipality_name: String(row.municipality_name || municipality.name),
                    assembly_name: String(row.assembly_name || municipality.assembly_name),
                    municipality_code: String(municipality.code || ''),
                });
            }
        }

        rows.sort(compareRecentRows);
        if (rows.length > mixedResultLimit) {
            hasOverflow = true;
        }

        return {
            rows: rows.slice(0, mixedResultLimit),
            hasOverflow,
        };
    }

    function searchSummary() {
        let hitMunicipalities = 0;
        let totalHits = 0;
        let totalHitsApprox = false;
        let errors = 0;
        let completed = 0;

        for (const municipality of municipalities) {
            const result = state.results.get(municipality.slug);
            if (!result) {
                continue;
            }
            if (result.status === 'ok') {
                completed += 1;
                totalHits += Number(result.total || 0);
                if (result.total_exact === false && Number(result.total || 0) > 0) {
                    totalHitsApprox = true;
                }
                if (Number(result.total || 0) > 0) {
                    hitMunicipalities += 1;
                }
            } else if (result.status === 'error' || result.status === 'query_error' || result.status === 'db_error') {
                completed += 1;
                errors += 1;
            } else if (result.status === 'missing_db') {
                completed += 1;
            }
        }

        return {
            hitMunicipalities,
            totalHits,
            errors,
            completed,
            scanned: state.progressDone,
            totalMunicipalities: state.progressTotal,
            totalHitsApprox,
        };
    }

    function sortedMunicipalities() {
        const items = municipalities.map((municipality) => {
            const result = state.results.get(municipality.slug);
            const total = Number(result?.total || 0);
            const latestHitDate = resultLatestHitDate(result);
            let mode = 'idle';
            if (state.query) {
                if (!result) {
                    mode = 'pending';
                } else if (result.status === 'ok' && total > 0) {
                    mode = 'hit';
                } else if (result.status === 'ok') {
                    mode = 'zero';
                } else if (result.status === 'loading') {
                    mode = 'loading';
                } else {
                    mode = 'error';
                }
            }

            return { ...municipality, result, total, latestHitDate, mode };
        });

        items.sort((a, b) => {
            const aRank = a.total > 0 ? 3 : (a.mode === 'loading' || a.mode === 'pending' ? 2 : (a.mode === 'error' ? 1 : 0));
            const bRank = b.total > 0 ? 3 : (b.mode === 'loading' || b.mode === 'pending' ? 2 : (b.mode === 'error' ? 1 : 0));
            if (aRank !== bRank) {
                return bRank - aRank;
            }
            if (a.latestHitDate !== b.latestHitDate) {
                return b.latestHitDate.localeCompare(a.latestHitDate);
            }
            if (a.total !== b.total) {
                return b.total - a.total;
            }
            if (a.code !== b.code) {
                return a.code.localeCompare(b.code);
            }
            return a.index - b.index;
        });

        return items;
    }

    function updateUrl() {
        const params = new URLSearchParams();
        if (state.query) {
            params.set('q', state.query);
        }
        if (state.selectedSlug) {
            params.set('slug', state.selectedSlug);
        }
        const next = `${window.location.pathname}${params.toString() ? `?${params.toString()}` : ''}`;
        window.history.replaceState({}, '', next);
    }

    function municipalityBrowseUrl(municipality) {
        if (!municipality?.url) {
            return '';
        }
        if (!state.query) {
            return municipality.url;
        }
        const joiner = municipality.url.includes('?') ? '&' : '?';
        return `${municipality.url}${joiner}q=${encodeURIComponent(state.query)}`;
    }

    function setMixedButtonEnabled(enabled) {
        if (!refs.selectedMixedButton) {
            return;
        }
        refs.selectedMixedButton.disabled = !enabled;
        refs.selectedMixedButton.classList.toggle('is-disabled', !enabled);
        refs.selectedMixedButton.setAttribute('aria-disabled', enabled ? 'false' : 'true');
    }

    function setOpenLink(url) {
        if (url) {
            refs.selectedOpenLink.href = url;
            refs.selectedOpenLink.classList.remove('is-disabled');
            refs.selectedOpenLink.setAttribute('aria-disabled', 'false');
        } else {
            refs.selectedOpenLink.href = '#';
            refs.selectedOpenLink.classList.add('is-disabled');
            refs.selectedOpenLink.setAttribute('aria-disabled', 'true');
        }
    }

    function renderProgress() {
        const summary = searchSummary();
        const progressPercent = summary.totalMunicipalities > 0
            ? Math.max(0, Math.min(100, (summary.scanned / summary.totalMunicipalities) * 100))
            : 0;

        refs.progressBar.style.width = `${progressPercent}%`;
        refs.progressBar.classList.toggle('is-animated', state.searching);
        refs.progressPanel?.classList.toggle('is-busy', state.searching || state.activeRequests.size > 0);

        if (!state.query) {
            refs.progressCopy.textContent = 'キーワードを入れると、対象自治体を順に走査します。';
        } else if (state.searching) {
            refs.progressCopy.textContent = `${summary.scanned}/${summary.totalMunicipalities} 自治体を走査中です。最新ヒットは自治体混合で先に並びます。`;
        } else {
            refs.progressCopy.textContent = `${summary.totalMunicipalities} 自治体の走査が終わりました。まず最新ヒットを見て、必要な自治体だけ切り替えできます。`;
        }

        refs.progressSummary.innerHTML = [
            { label: '走査済み', value: `${summary.scanned}/${summary.totalMunicipalities}` },
            { label: 'ヒット自治体', value: `${summary.hitMunicipalities}` },
            { label: '総ヒット件数', value: `${summary.totalHits}${summary.totalHitsApprox ? '+' : ''}` },
        ].map((item) => `
            <div class="summary-card">
                <span>${escapeHtml(item.label)}</span>
                <strong>${escapeHtml(item.value)}</strong>
            </div>
        `).join('');
        renderActiveSearches();
    }

    function renderMunicipalityList() {
        const items = sortedMunicipalities();
        if (items.length === 0) {
            refs.municipalityList.innerHTML = '<div class="municipality-empty">検索可能な自治体がまだありません。</div>';
            return;
        }

        refs.municipalityList.innerHTML = items.map((item) => {
            let badge = '待機';
            let helper = `${item.assembly_name}`;
            if (state.query) {
                if (!item.result) {
                    badge = '順番待ち';
                    helper = 'まだこの自治体は走査していません。';
                } else if (item.result.status === 'loading') {
                    badge = '読込中';
                    helper = 'この自治体の結果を展開しています。';
                } else if (item.result.status === 'ok' && item.total > 0) {
                    badge = item.result.total_exact === false
                        ? `${item.total}件以上`
                        : `${item.total}件`;
                    helper = item.result.total_exact === false
                        ? (item.latestHitDate ? `最新ヒット ${item.latestHitDate}` : '上位ヒットあり')
                        : (item.latestHitDate ? `最新ヒット ${item.latestHitDate}` : 'ヒットあり');
                } else if (item.result.status === 'ok') {
                    badge = '0件';
                    helper = 'この条件では該当なし';
                } else {
                    badge = 'エラー';
                    helper = String(item.result.error || '検索に失敗しました。');
                }
            }

            const classes = [
                'municipality-button',
                item.slug === state.selectedSlug ? 'is-active' : '',
                item.mode === 'hit' ? 'is-hit' : '',
                item.mode === 'zero' ? 'is-zero' : '',
                item.mode === 'error' ? 'is-error' : '',
                item.mode === 'pending' || item.mode === 'loading' ? 'is-pending' : '',
            ].filter(Boolean).join(' ');

            return `
                <button type="button" class="${classes}" data-slug="${escapeHtml(item.slug)}">
                    <div class="municipality-button-top">
                        <div class="municipality-button-title">${escapeHtml(item.name)}</div>
                        <span class="municipality-button-badge">${escapeHtml(badge)}</span>
                    </div>
                    <div class="municipality-button-meta">
                        <div>${escapeHtml(item.assembly_name)}</div>
                        <div>${escapeHtml(helper)}</div>
                    </div>
                </button>
            `;
        }).join('');
    }

    function renderResultsSummary(selected, result) {
        const summary = searchSummary();
        const mixed = mixedResultsInfo();
        const cards = [
            { label: 'ヒット自治体', value: `${summary.hitMunicipalities}` },
            { label: '総ヒット件数', value: `${summary.totalHits}${summary.totalHitsApprox ? '+' : ''}` },
            { label: '表示', value: selected ? selected.name : `混合上位${mixedResultLimit}件` },
        ];

        if (result?.status === 'ok') {
            cards[2].value = result.total_exact === false ? `${selected?.name || ''} ${result.total}件以上` : `${selected?.name || ''} ${result.total}件`;
        } else if (!selected) {
            cards[2].value = mixed.hasOverflow
                ? `最新${mixedResultLimit}件を表示`
                : `最新${mixed.rows.length}件を表示`;
        }

        refs.resultsSummary.innerHTML = cards.map((item) => `
            <div class="summary-card">
                <span>${escapeHtml(item.label)}</span>
                <strong>${escapeHtml(item.value)}</strong>
            </div>
        `).join('');
    }

    function renderEmptyState(title, body, tone = '') {
        refs.resultsBody.innerHTML = `
            <div class="empty-state ${tone}">
                <strong>${escapeHtml(title)}</strong>
                <span>${escapeHtml(body)}</span>
            </div>
        `;
        refs.resultsPagination.innerHTML = '';
    }

    function renderResultCards(rows, { showMunicipality = false, rankOffset = 0 } = {}) {
        return `
            <ul class="result-list">
                ${rows.map((row, index) => `
                    <li class="result-card">
                        <div class="result-top">
                            <div class="result-rank">結果 ${escapeHtml(String(rankOffset + index + 1))}</div>
                            <div class="badges">
                                ${row.held_on ? `<span class="badge">${escapeHtml(row.held_on)}</span>` : ''}
                                ${row.year_label ? `<span class="badge">${escapeHtml(row.year_label)}</span>` : ''}
                            </div>
                        </div>
                        <h3 class="result-title">${escapeHtml(row.title || '')}</h3>
                        <div class="result-meta">
                            ${showMunicipality && row.municipality_name ? `<div><span class="result-label">自治体</span> ${escapeHtml(row.municipality_name)}</div>` : ''}
                            ${row.meeting_name ? `<div><span class="result-label">会議</span> ${escapeHtml(row.meeting_name)}</div>` : ''}
                            ${row.rel_path ? `<div><span class="result-label">ファイル</span> ${escapeHtml(row.rel_path)}</div>` : ''}
                        </div>
                        <div class="result-excerpt">${renderExcerpt(row.excerpt || '')}</div>
                        <div class="result-links">
                            <a class="link-pill" href="${escapeHtml(row.detail_url || row.browse_url || '')}">自治体ページで詳細を見る</a>
                            ${showMunicipality && row.browse_url ? `<a class="link-pill is-subtle" href="${escapeHtml(row.browse_url)}">この自治体の一覧を見る</a>` : ''}
                            ${row.source_url ? `<a class="link-pill is-subtle" href="${escapeHtml(row.source_url)}" target="_blank" rel="noreferrer">原サイト</a>` : ''}
                        </div>
                    </li>
                `).join('')}
            </ul>
        `;
    }

    function renderResults() {
        const selected = state.selectedSlug ? municipalityBySlug.get(state.selectedSlug) : null;
        const result = selected ? state.results.get(selected.slug) : null;
        const mixed = mixedResultsInfo();

        renderResultsSummary(selected, result);

        if (!state.query) {
            refs.selectedTitle.textContent = 'まずキーワードを入れてください';
            refs.selectedMeta.textContent = '会議録 DB を横断して、最新 100 件を自治体混合で並べます。続きを見たい自治体だけ左から切り替えます。';
            setMixedButtonEnabled(false);
            setOpenLink('');
            renderEmptyState('横断検索の準備ができています。', 'キーワードを入れて実行すると、まず最新ヒットを自治体混合で並べます。');
            return;
        }

        if (!selected) {
            refs.selectedTitle.textContent = state.searching ? `最新ヒット ${mixedResultLimit}件を集約中です` : `最新ヒット ${mixed.rows.length}件`;
            refs.selectedMeta.textContent = [
                '新しい開催日順',
                mixed.hasOverflow ? `最新${mixedResultLimit}件を表示中。続きは左の自治体から確認できます。` : '現在見つかったヒットをそのまま表示しています。',
                state.searching ? '結果は検索中もリアルタイムで追加されます。' : '',
            ].filter(Boolean).join(' / ');
            setMixedButtonEnabled(false);
            setOpenLink('');

            if (mixed.rows.length === 0) {
                renderEmptyState(
                    state.searching ? '検索中です。' : '該当する会議録がありません。',
                    state.searching ? '新しいヒットが見つかり次第、ここへ自治体混合で追加していきます。' : '検索語を少し広げるか、演算子を減らして試してください。',
                    state.searching ? 'is-loading' : ''
                );
                return;
            }

            refs.resultsBody.innerHTML = renderResultCards(mixed.rows, {
                showMunicipality: true,
                rankOffset: 0,
            });
            refs.resultsPagination.innerHTML = '';
            return;
        }

        refs.selectedTitle.textContent = selected.name;
        setMixedButtonEnabled(true);
        setOpenLink(municipalityBrowseUrl(selected));

        if (!result) {
            refs.selectedMeta.textContent = 'この自治体はまだ走査待ちです。';
            renderEmptyState('この自治体はまだ検索していません。', '左の一覧で順番待ちのままですが、検索が進むとここに結果が入ります。', state.searching ? 'is-loading' : '');
            return;
        }

        if (result.status === 'loading') {
            refs.selectedMeta.textContent = `${selected.assembly_name} の結果を読み込んでいます。`;
            renderEmptyState('自治体ページを読み込み中です。', '上位ヒットを詳細表示できる形に整えています。', 'is-loading');
            return;
        }

        if (result.status !== 'ok') {
            refs.selectedMeta.textContent = `${selected.assembly_name} の検索で問題が起きました。`;
            renderEmptyState('この自治体の検索結果を取得できませんでした。', String(result.error || '時間をおいて再度お試しください。'), 'is-error');
            return;
        }

        const rangeCopy = Number(result.end || 0) > 0
            ? `${result.start}-${result.end}件を表示`
            : '該当なし';
        const previewCopy = !result.fullLoaded && Number(result.end || 0) > 0
            ? `最新${Math.min(mixedResultLimit, Number(result.end || 0))}件の先行表示`
            : '';
        refs.selectedMeta.textContent = [
            `${selected.assembly_name}`,
            result.total_exact === false ? `${result.total}件以上ヒット` : `${result.total}件ヒット`,
            rangeCopy,
            previewCopy,
            result.stats?.latest_hit_date ? `最新ヒット ${result.stats.latest_hit_date}` : '',
        ].filter(Boolean).join(' / ');

        if (!Array.isArray(result.rows) || result.rows.length === 0) {
            renderEmptyState('この自治体では該当がありません。', 'ほかの自治体タブへ切り替えるか、検索語を少し広げてください。');
            return;
        }

        refs.resultsBody.innerHTML = renderResultCards(result.rows, {
            showMunicipality: false,
            rankOffset: ((Number(result.page || 1) - 1) * Number(result.per_page || 0)),
        });

        if (!result.fullLoaded) {
            refs.resultsPagination.innerHTML = '';
            return;
        }

        const currentPage = Number(result.page || 1);
        const hasMore = Boolean(result.has_more);
        if (currentPage <= 1 && !hasMore) {
            refs.resultsPagination.innerHTML = '';
            return;
        }

        refs.resultsPagination.innerHTML = `
            <button type="button" data-page="${Math.max(1, currentPage - 1)}" ${currentPage <= 1 ? 'disabled' : ''}>前へ</button>
            <span>${escapeHtml(String(currentPage))}${hasMore ? ' / 続きあり' : ''}</span>
            <button type="button" data-page="${currentPage + 1}" ${!hasMore ? 'disabled' : ''}>次へ</button>
        `;
    }

    function renderAll() {
        refs.searchButton.disabled = state.searching;
        refs.searchButton.textContent = state.searching ? '検索中...' : '横断検索する';
        renderProgress();
        renderMunicipalityList();
        renderResults();
    }

    async function fetchJson(params) {
        const response = await fetch(`${state.apiUrl}?${params.toString()}`, {
            headers: { Accept: 'application/json' },
            cache: 'no-store',
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(String(data.error || `HTTP ${response.status}`));
        }
        return data;
    }

    async function loadMunicipalityPage(slug, page = 1) {
        const municipality = municipalityBySlug.get(slug);
        if (!municipality || !state.query) {
            return;
        }

        const current = state.results.get(slug);
        if (current?.status === 'ok' && current?.fullLoaded && Number(current.page || 1) === page) {
            return;
        }

        function restorePreviewResult(errorMessage = '') {
            // preview で成功している自治体は、詳細読込の失敗で
            // 「検索失敗」に見せず、先に分かった結果をそのまま使う。
            if (current?.status !== 'ok') {
                return false;
            }
            state.results.set(slug, {
                ...municipality,
                ...current,
                fullLoaded: false,
                detail_error: errorMessage,
            });
            return true;
        }

        state.results.set(slug, { ...municipality, ...current, status: 'loading' });
        setActiveRequest(slug, 'detail', true);
        renderAll();

        const token = state.requestToken;
        try {
            const data = await fetchJson(new URLSearchParams({
                action: 'search',
                slug,
                q: state.query,
                page: String(page),
                per_page: '12',
            }));
            if (token !== state.requestToken) {
                return;
            }
            if (data.status === 'ok') {
                state.results.set(slug, {
                    ...municipality,
                    ...data,
                    preview_rows: Array.isArray(current?.preview_rows) ? current.preview_rows : [],
                    fullLoaded: true,
                    detail_error: '',
                });
            } else if (!restorePreviewResult(String(data.error || ''))) {
                state.results.set(slug, {
                    ...municipality,
                    ...data,
                    preview_rows: Array.isArray(current?.preview_rows) ? current.preview_rows : [],
                    fullLoaded: false,
                });
            }
            renderAll();
        } catch (error) {
            if (token !== state.requestToken) {
                return;
            }
            if (!restorePreviewResult(error instanceof Error ? error.message : '検索に失敗しました。')) {
                state.results.set(slug, {
                    ...municipality,
                    ...current,
                    status: 'error',
                    error: error instanceof Error ? error.message : '検索に失敗しました。',
                });
            }
        } finally {
            setActiveRequest(slug, 'detail', false);
            if (token === state.requestToken) {
                renderAll();
            }
        }
    }

    async function runChunkQueue(chunks, limit, handler) {
        let cursor = 0;
        const workers = Array.from({ length: Math.min(limit, chunks.length) }, async () => {
            while (cursor < chunks.length) {
                const current = chunks[cursor];
                cursor += 1;
                await handler(current);
            }
        });
        await Promise.all(workers);
    }

    async function startSearch(query) {
        state.query = String(query || '').trim();
        state.requestToken += 1;
        state.searching = false;
        state.progressDone = 0;
        state.progressTotal = municipalities.length;
        state.results = new Map();
        state.activeRequests.clear();
        state.selectedSlug = '';
        updateUrl();
        renderAll();

        if (!state.query) {
            return;
        }

        const token = state.requestToken;
        state.searching = true;
        renderAll();

        const slugs = municipalities.map((item) => item.slug);
        const applyBatch = async (slug) => {
            const municipality = municipalityBySlug.get(slug);
            if (!municipality) {
                return;
            }
            setActiveRequest(slug, 'preview', true);
            renderAll();
            try {
                const data = await fetchJson(new URLSearchParams({
                    action: 'search_preview',
                    q: state.query,
                    per_page: String(mixedResultLimit),
                    slug,
                }));
                if (token !== state.requestToken) {
                    return;
                }
                state.results.set(municipality.slug, {
                    ...municipality,
                    ...data,
                    preview_rows: Array.isArray(data.rows) ? data.rows : [],
                    fullLoaded: false,
                });
            } catch (error) {
                if (token !== state.requestToken) {
                    return;
                }
                state.results.set(slug, {
                    ...municipality,
                    status: 'error',
                    error: error instanceof Error ? error.message : '検索に失敗しました。',
                    rows: [],
                    total: 0,
                    total_exact: true,
                    preview_rows: [],
                });
            } finally {
                setActiveRequest(slug, 'preview', false);
                if (token !== state.requestToken) {
                    return;
                }
                state.progressDone = Math.min(state.progressTotal, state.progressDone + 1);
                renderAll();
            }
        };

        await runChunkQueue(slugs, 2, applyBatch);
        if (token !== state.requestToken) {
            return;
        }

        state.searching = false;
        state.activeRequests.clear();
        renderAll();
    }

    refs.form?.addEventListener('submit', (event) => {
        event.preventDefault();
        startSearch(refs.query?.value || '');
    });

    document.querySelectorAll('[data-example-query]').forEach((element) => {
        element.addEventListener('click', () => {
            const query = String(element.getAttribute('data-example-query') || '');
            if (refs.query) {
                refs.query.value = query;
                refs.query.focus();
            }
            startSearch(query);
        });
    });

    refs.municipalityList?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-slug]');
        if (!button) {
            return;
        }
        const slug = String(button.getAttribute('data-slug') || '');
        if (!municipalityBySlug.has(slug)) {
            return;
        }
        state.selectedSlug = slug;
        updateUrl();
        renderAll();
        const current = state.results.get(slug);
        if (state.query && current?.status === 'ok' && !current.fullLoaded) {
            loadMunicipalityPage(slug, 1);
        }
    });

    refs.selectedMixedButton?.addEventListener('click', () => {
        state.selectedSlug = '';
        updateUrl();
        renderAll();
    });

    refs.resultsPagination?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-page]');
        if (!button || !state.selectedSlug) {
            return;
        }
        const page = Number(button.getAttribute('data-page') || '1');
        if (!Number.isFinite(page) || page < 1) {
            return;
        }
        loadMunicipalityPage(state.selectedSlug, page);
    });

    renderAll();
    if (state.query) {
        startSearch(state.query);
    }
})();
