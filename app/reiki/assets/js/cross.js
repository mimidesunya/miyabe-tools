(() => {
    const bootNode = document.getElementById('reiki-cross-boot');
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
                page_title: String(item.page_title || item.name || ''),
                url: String(item.url || ''),
                index,
            }))
            .filter((item) => item.slug)
        : [];

    const municipalityBySlug = new Map(municipalities.map((item) => [item.slug, item]));
    const selectedFromBoot = String(boot.selectedSlug || '');

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
        selectedOpenLink: document.getElementById('selected-open-link'),
        resultsSummary: document.getElementById('results-summary'),
        resultsBody: document.getElementById('results-body'),
        resultsPagination: document.getElementById('results-pagination'),
    };

    const state = {
        apiUrl: String(boot.apiUrl || '/reiki/search_api.php'),
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

    function searchSummary() {
        let hitMunicipalities = 0;
        let totalHits = 0;
        let totalHitsApprox = false;
        let errors = 0;

        for (const municipality of municipalities) {
            const result = state.results.get(municipality.slug);
            if (!result) {
                continue;
            }
            if (result.status === 'ok') {
                totalHits += Number(result.total || 0);
                if (result.total_exact === false && Number(result.total || 0) > 0) {
                    totalHitsApprox = true;
                }
                if (Number(result.total || 0) > 0) {
                    hitMunicipalities += 1;
                }
            } else if (result.status === 'error' || result.status === 'query_error' || result.status === 'db_error') {
                errors += 1;
            }
        }

        return {
            hitMunicipalities,
            totalHits,
            totalHitsApprox,
            errors,
            scanned: state.progressDone,
            totalMunicipalities: state.progressTotal,
        };
    }

    function sortedMunicipalities() {
        const items = municipalities.map((municipality) => {
            const result = state.results.get(municipality.slug);
            const total = Number(result?.total || 0);
            const lastDate = String(result?.stats?.last_date || '');
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

            return { ...municipality, result, total, lastDate, mode };
        });

        items.sort((a, b) => {
            const aRank = a.total > 0 ? 3 : (a.mode === 'loading' || a.mode === 'pending' ? 2 : (a.mode === 'error' ? 1 : 0));
            const bRank = b.total > 0 ? 3 : (b.mode === 'loading' || b.mode === 'pending' ? 2 : (b.mode === 'error' ? 1 : 0));
            if (aRank !== bRank) {
                return bRank - aRank;
            }
            if (a.total !== b.total) {
                return b.total - a.total;
            }
            if (a.lastDate !== b.lastDate) {
                return b.lastDate.localeCompare(a.lastDate);
            }
            if (a.code !== b.code) {
                return a.code.localeCompare(b.code);
            }
            return a.index - b.index;
        });

        return items;
    }

    function preferredSlug() {
        const current = state.selectedSlug;
        if (current && municipalityBySlug.has(current)) {
            const currentResult = state.results.get(current);
            if (!state.query || currentResult?.status === 'loading' || currentResult?.status === 'ok') {
                return current;
            }
        }

        const hits = sortedMunicipalities().filter((item) => item.total > 0);
        return hits[0]?.slug || current || '';
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
        return municipality?.url ? municipality.url : '';
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
            refs.progressCopy.textContent = `${summary.scanned}/${summary.totalMunicipalities} 自治体を走査中です。結果が出た自治体から先に切り替えできます。`;
        } else {
            refs.progressCopy.textContent = `${summary.totalMunicipalities} 自治体の走査が終わりました。ヒット自治体を切り替えて確認できます。`;
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
            let helper = item.page_title;
            if (state.query) {
                if (!item.result) {
                    badge = '順番待ち';
                    helper = 'まだこの自治体は走査していません。';
                } else if (item.result.status === 'loading') {
                    badge = '読込中';
                    helper = 'この自治体の結果を展開しています。';
                } else if (item.result.status === 'ok' && item.total > 0) {
                    badge = item.result.total_exact === false ? `${item.total}件以上` : `${item.total}件`;
                    helper = item.result.total_exact === false
                        ? '上位ヒットあり'
                        : (item.lastDate ? `最新制定日 ${item.lastDate}` : 'ヒットあり');
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
                        <div>${escapeHtml(item.page_title)}</div>
                        <div>${escapeHtml(helper)}</div>
                    </div>
                </button>
            `;
        }).join('');
    }

    function renderResultsSummary(selected, result) {
        const summary = searchSummary();
        const cards = [
            { label: 'ヒット自治体', value: `${summary.hitMunicipalities}` },
            { label: '総ヒット件数', value: `${summary.totalHits}${summary.totalHitsApprox ? '+' : ''}` },
            { label: '選択中の自治体', value: selected ? selected.name : '未選択' },
        ];

        if (result?.status === 'ok') {
            cards[2].value = result.total_exact === false ? `${result.total}件以上` : `${result.total}件`;
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

    function renderResults() {
        const selected = state.selectedSlug ? municipalityBySlug.get(state.selectedSlug) : null;
        const result = selected ? state.results.get(selected.slug) : null;

        renderResultsSummary(selected, result);

        if (!state.query) {
            refs.selectedTitle.textContent = 'まずキーワードを入れてください';
            refs.selectedMeta.textContent = '例規集 DB を横断して、該当自治体と上位ヒットを並べます。';
            setOpenLink(selected ? municipalityBrowseUrl(selected) : '');
            renderEmptyState('横断検索の準備ができています。', 'キーワードを入れて実行すると、ヒットした自治体ごとに結果を切り替えられます。');
            return;
        }

        if (!selected) {
            refs.selectedTitle.textContent = state.searching ? '検索対象を走査しています' : '条件に一致する自治体がありません';
            refs.selectedMeta.textContent = state.searching
                ? 'ヒットした自治体が見つかり次第、ここに結果を表示します。'
                : '別のキーワードにすると、ヒットする自治体が見つかるかもしれません。';
            setOpenLink('');
            renderEmptyState(
                state.searching ? '検索中です。' : '該当する例規がありません。',
                state.searching ? '先に見たい自治体があれば左の一覧から選択できます。' : '検索語を少し広げるか、演算子を減らして試してください。',
                state.searching ? 'is-loading' : ''
            );
            return;
        }

        refs.selectedTitle.textContent = selected.name;
        setOpenLink(municipalityBrowseUrl(selected));

        if (!result) {
            refs.selectedMeta.textContent = 'この自治体はまだ走査待ちです。';
            renderEmptyState('この自治体はまだ検索していません。', '左の一覧で順番待ちのままですが、検索が進むとここに結果が入ります。', state.searching ? 'is-loading' : '');
            return;
        }

        if (result.status === 'loading') {
            refs.selectedMeta.textContent = `${selected.page_title} の結果を読み込んでいます。`;
            renderEmptyState('自治体ページを読み込み中です。', '上位ヒットを詳細表示できる形に整えています。', 'is-loading');
            return;
        }

        if (result.status !== 'ok') {
            refs.selectedMeta.textContent = `${selected.page_title} の検索で問題が起きました。`;
            renderEmptyState('この自治体の検索結果を取得できませんでした。', String(result.error || '時間をおいて再度お試しください。'), 'is-error');
            return;
        }

        const rangeCopy = Number(result.end || 0) > 0
            ? `${result.start}-${result.end}件を表示`
            : '該当なし';
        const previewCopy = !result.fullLoaded && Number(result.end || 0) > 0
            ? '上位のみ先行表示'
            : '';
        refs.selectedMeta.textContent = [
            selected.page_title,
            result.total_exact === false ? `${result.total}件以上ヒット` : `${result.total}件ヒット`,
            rangeCopy,
            previewCopy,
            result.stats?.last_date ? `最新制定日 ${result.stats.last_date}` : '',
        ].filter(Boolean).join(' / ');

        if (!Array.isArray(result.rows) || result.rows.length === 0) {
            renderEmptyState('この自治体では該当がありません。', 'ほかの自治体タブへ切り替えるか、検索語を少し広げてください。');
            return;
        }

        refs.resultsBody.innerHTML = `
            <ul class="result-list">
                ${result.rows.map((row, index) => `
                    <li class="result-card">
                        <div class="result-top">
                            <div class="result-rank">結果 ${escapeHtml(String(((Number(result.page || 1) - 1) * Number(result.per_page || 0)) + index + 1))}</div>
                            <div class="badges">
                                ${row.enactment_date ? `<span class="badge">${escapeHtml(row.enactment_date)}</span>` : ''}
                                ${row.document_type ? `<span class="badge">${escapeHtml(row.document_type)}</span>` : ''}
                            </div>
                        </div>
                        <h3 class="result-title">${escapeHtml(row.title || '')}</h3>
                        <div class="result-meta">
                            ${row.responsible_department ? `<div><span class="result-label">所管</span> ${escapeHtml(row.responsible_department)}</div>` : ''}
                            ${row.combined_stance ? `<div><span class="result-label">判定</span> ${escapeHtml(row.combined_stance)}</div>` : ''}
                            ${row.filename ? `<div><span class="result-label">ファイル</span> ${escapeHtml(`${row.filename}.html`)}</div>` : ''}
                        </div>
                        <div class="result-excerpt">${renderExcerpt(row.excerpt || '')}</div>
                        <div class="result-links">
                            <a class="link-pill" href="${escapeHtml(row.detail_url || row.browse_url || '')}">自治体ページで詳細を見る</a>
                            <a class="link-pill is-subtle" href="${escapeHtml(row.browse_url || '')}">自治体別一覧</a>
                            ${row.source_url ? `<a class="link-pill is-subtle" href="${escapeHtml(row.source_url)}" target="_blank" rel="noreferrer">原サイト</a>` : ''}
                        </div>
                    </li>
                `).join('')}
            </ul>
        `;

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
                state.results.set(slug, { ...municipality, ...data, fullLoaded: true, detail_error: '' });
            } else if (!restorePreviewResult(String(data.error || ''))) {
                state.results.set(slug, { ...municipality, ...data, fullLoaded: false });
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
            renderAll();
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
        updateUrl();
        renderAll();

        if (!state.query) {
            return;
        }

        const token = state.requestToken;
        const preferred = state.selectedSlug && municipalityBySlug.has(state.selectedSlug) ? state.selectedSlug : '';
        state.selectedSlug = preferred;
        state.searching = true;
        renderAll();

        const slugs = municipalities.map((item) => item.slug);
        const applyPreview = async (slug) => {
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
                    per_page: '3',
                    slug,
                }));
                if (token !== state.requestToken) {
                    return;
                }
                state.results.set(municipality.slug, { ...municipality, ...data, fullLoaded: false });
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
                });
            } finally {
                setActiveRequest(slug, 'preview', false);
                if (token !== state.requestToken) {
                    return;
                }
                state.progressDone = Math.min(state.progressTotal, state.progressDone + 1);
                if (!state.selectedSlug) {
                    state.selectedSlug = preferredSlug();
                }
                renderAll();
            }
        };

        await runChunkQueue(slugs, 2, applyPreview);
        if (token !== state.requestToken) {
            return;
        }

        state.searching = false;
        state.selectedSlug = preferredSlug();
        renderAll();

        if (state.selectedSlug) {
            const selectedResult = state.results.get(state.selectedSlug);
            if (selectedResult?.status === 'ok' && !selectedResult.fullLoaded) {
                await loadMunicipalityPage(state.selectedSlug, 1);
            }
        }
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
