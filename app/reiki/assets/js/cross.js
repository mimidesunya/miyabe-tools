(() => {
    const bootNode = document.getElementById('reiki-cross-boot');
    if (!bootNode) {
        return;
    }

    const boot = JSON.parse(bootNode.textContent || '{}');
    const prefectureOptions = Array.isArray(boot.prefectures)
        ? boot.prefectures
            .map((item) => ({
                code: String(item.code || ''),
                name: String(item.name || ''),
                count: Number(item.count || 0),
            }))
            .filter((item) => item.code && item.name)
        : [];
    const prefectureNameByCode = new Map(prefectureOptions.map((item) => [item.code, item.name]));
    const availablePrefectureCodes = new Set(prefectureOptions.map((item) => item.code));

    function municipalityPrefectureCode(item) {
        const explicit = String(item.pref_code || '').trim();
        if (/^\d{2}$/.test(explicit)) {
            return explicit;
        }
        const match = String(item.code || '').match(/^(\d{2})/);
        return match ? match[1] : '';
    }

    const municipalities = Array.isArray(boot.municipalities)
        ? boot.municipalities
            .map((item, index) => {
                const prefCode = municipalityPrefectureCode(item);
                return {
                    slug: String(item.slug || ''),
                    code: String(item.code || ''),
                    pref_code: prefCode,
                    pref_name: String(item.pref_name || prefectureNameByCode.get(prefCode) || ''),
                    name: String(item.name || ''),
                    page_title: String(item.page_title || item.name || ''),
                    url: String(item.url || ''),
                    index,
                };
            })
            .filter((item) => item.slug)
        : [];

    const municipalityBySlug = new Map(municipalities.map((item) => [item.slug, item]));
    const selectedFromBoot = String(boot.selectedSlug || '');
    const lockedFromBoot = municipalityBySlug.has(String(boot.lockedSlug || '')) ? String(boot.lockedSlug || '') : '';
    const selectedPrefectureFromBoot = String(boot.selectedPrefecture || '');
    const mixedResultLimit = 100;

    const refs = {
        form: document.getElementById('cross-search-form'),
        query: document.getElementById('cross-query'),
        prefecture: document.getElementById('cross-prefecture'),
        searchButton: document.getElementById('cross-search-button'),
        targetCount: document.getElementById('target-municipality-count'),
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
        lockedSlug: lockedFromBoot,
        prefecture: availablePrefectureCodes.has(selectedPrefectureFromBoot) ? selectedPrefectureFromBoot : '',
        searching: false,
        stopped: false,
        requestToken: 0,
        progressDone: 0,
        progressTotal: municipalities.length,
        results: new Map(),
        activeRequests: new Map(),
        abortControllers: new Set(),
    };

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function normalizePrefecture(value) {
        const prefCode = String(value || '').trim();
        return availablePrefectureCodes.has(prefCode) ? prefCode : '';
    }

    function filteredMunicipalities() {
        if (state.lockedSlug) {
            const locked = municipalityBySlug.get(state.lockedSlug);
            return locked ? [locked] : [];
        }
        if (!state.prefecture) {
            return municipalities;
        }
        return municipalities.filter((municipality) => municipality.pref_code === state.prefecture);
    }

    function selectedPrefectureName() {
        return state.prefecture ? (prefectureNameByCode.get(state.prefecture) || '') : '';
    }

    function ensureSelectedSlugInScope() {
        if (state.lockedSlug) {
            state.selectedSlug = state.lockedSlug;
            state.prefecture = '';
            return;
        }
        if (!state.selectedSlug) {
            return;
        }
        const selected = municipalityBySlug.get(state.selectedSlug);
        if (!selected || (state.prefecture && selected.pref_code !== state.prefecture)) {
            state.selectedSlug = '';
        }
    }

    function renderExcerpt(value) {
        return escapeHtml(value).replace(/\[\[\[/g, '<mark>').replace(/\]\]\]/g, '</mark>').replace(/\n/g, '<br>');
    }

    function compactResultMeta(row, showMunicipality) {
        const parts = [];
        if (showMunicipality && row.municipality_name) {
            parts.push(String(row.municipality_name));
        }
        if (row.responsible_department) {
            parts.push(String(row.responsible_department));
        }
        if (row.combined_stance) {
            parts.push(String(row.combined_stance));
        }
        if (row.filename) {
            parts.push(`${row.filename}.html`);
        }
        return parts.filter(Boolean);
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

    function resultLatestEnactmentDate(result) {
        const rows = Array.isArray(result?.rows) ? result.rows : [];
        return rows.reduce((latest, row) => {
            const date = String(row?.enactment_date || '');
            return date > latest ? date : latest;
        }, '');
    }

    function compareMixedRows(a, b) {
        const aDate = String(a?.enactment_date || '');
        const bDate = String(b?.enactment_date || '');
        if (aDate !== bDate) {
            return bDate.localeCompare(aDate);
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

        for (const municipality of filteredMunicipalities()) {
            const result = state.results.get(municipality.slug);
            if (!result || result.status !== 'ok') {
                continue;
            }

            if (result.has_more) {
                hasOverflow = true;
            }

            const sourceRows = Array.isArray(result.rows) ? result.rows : [];
            for (const row of sourceRows) {
                if (!row || typeof row !== 'object') {
                    continue;
                }
                rows.push({
                    ...row,
                    municipality_slug: String(row.municipality_slug || municipality.slug),
                    municipality_name: String(row.municipality_name || municipality.name),
                    page_title: String(row.page_title || municipality.page_title),
                    municipality_code: String(municipality.code || ''),
                });
            }
        }

        rows.sort(compareMixedRows);
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

        for (const municipality of filteredMunicipalities()) {
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
        const items = filteredMunicipalities().map((municipality) => {
            const result = state.results.get(municipality.slug);
            const total = Number(result?.total || 0);
            const latestHitDate = resultLatestEnactmentDate(result);
            const lastDate = latestHitDate || String(result?.stats?.last_date || '');
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

            return { ...municipality, result, total, latestHitDate, lastDate, mode };
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

    function updateUrl() {
        const params = new URLSearchParams();
        if (state.query) {
            params.set('q', state.query);
        }
        if (state.prefecture && !state.lockedSlug) {
            params.set('pref', state.prefecture);
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
        const scopeName = selectedPrefectureName();
        const targetLabel = scopeName ? `${scopeName}の自治体` : '対象自治体';
        const progressPercent = summary.totalMunicipalities > 0
            ? Math.max(0, Math.min(100, (summary.scanned / summary.totalMunicipalities) * 100))
            : 0;

        refs.progressBar.style.width = `${progressPercent}%`;
        refs.progressBar.classList.toggle('is-animated', state.searching);
        refs.progressPanel?.classList.toggle('is-busy', state.searching || state.activeRequests.size > 0);

        if (!state.query) {
            refs.progressCopy.textContent = `キーワードを入れると、${targetLabel}を順に走査します。`;
        } else if (state.searching) {
            refs.progressCopy.textContent = `${summary.scanned}/${summary.totalMunicipalities} 自治体を走査中です。ヒットは結果欄へ順次追加されます。`;
        } else if (state.stopped) {
            refs.progressCopy.textContent = `${summary.scanned}/${summary.totalMunicipalities} 自治体で中断しました。見つかったヒットはこのまま確認できます。`;
        } else {
            refs.progressCopy.textContent = `${summary.totalMunicipalities} 自治体の走査が終わりました。まず横断ヒットを見て、必要な自治体だけ切り替えできます。`;
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
                        <div>${escapeHtml(item.page_title)}</div>
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
            { label: '表示', value: selected ? selected.name : `横断上位${mixedResultLimit}件` },
        ];

        if (result?.status === 'ok') {
            cards[2].value = result.total_exact === false ? `${result.total}件以上` : `${result.total}件`;
        } else if (!selected) {
            cards[2].value = mixed.hasOverflow
                ? `上位${mixedResultLimit}件を表示`
                : `${mixed.rows.length}件を表示`;
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
                ${rows.map((row, index) => {
                    const meta = compactResultMeta(row, showMunicipality);
                    return `
                        <li class="result-card">
                            <div class="result-top">
                                <div class="result-rank">結果 ${escapeHtml(String(rankOffset + index + 1))}</div>
                                <div class="badges">
                                    ${row.enactment_date ? `<span class="badge">${escapeHtml(row.enactment_date)}</span>` : ''}
                                    ${row.document_type ? `<span class="badge">${escapeHtml(row.document_type)}</span>` : ''}
                                </div>
                            </div>
                            <h3 class="result-title">${escapeHtml(row.title || '')}</h3>
                            ${meta.length > 0 ? `
                                <div class="result-meta">
                                    ${meta.map((item) => `<span>${escapeHtml(item)}</span>`).join('')}
                                </div>
                            ` : ''}
                            ${row.excerpt ? `<div class="result-excerpt">${renderExcerpt(row.excerpt || '')}</div>` : ''}
                            <div class="result-links">
                                <a class="link-pill" href="${escapeHtml(row.detail_url || row.browse_url || '')}">詳細を見る</a>
                                ${showMunicipality && row.browse_url ? `<a class="link-pill is-subtle" href="${escapeHtml(row.browse_url)}">一覧</a>` : ''}
                                ${row.source_url ? `<a class="link-pill is-subtle" href="${escapeHtml(row.source_url)}" target="_blank" rel="noreferrer">原サイト</a>` : ''}
                            </div>
                        </li>
                    `;
                }).join('')}
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
            refs.selectedMeta.textContent = '例規集 DB を横断して、見つかったヒットを自治体混合で並べます。';
            setOpenLink(selected ? municipalityBrowseUrl(selected) : '');
            renderEmptyState('横断検索の準備ができています。', 'キーワードを入れて実行すると、見つかったヒットを検索中から順次表示します。');
            return;
        }

        if (!selected) {
            refs.selectedTitle.textContent = state.searching
                ? 'ヒットを集約中です'
                : (mixed.rows.length > 0 ? `横断ヒット ${mixed.rows.length}件` : '条件に一致する例規がありません');
            refs.selectedMeta.textContent = [
                '制定日が新しいものを優先して表示',
                mixed.hasOverflow ? `上位${mixedResultLimit}件を表示中。続きは左の自治体から確認できます。` : '現在見つかったヒットをそのまま表示しています。',
                state.searching ? '結果は検索中もリアルタイムで追加されます。' : '',
            ].filter(Boolean).join(' / ');
            setOpenLink('');

            if (mixed.rows.length === 0) {
                renderEmptyState(
                    state.searching ? '検索中です。' : '該当する例規がありません。',
                    state.searching ? 'ヒットが見つかり次第、ここへ自治体混合で追加していきます。' : '検索語を少し広げるか、演算子を減らして試してください。',
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
        ensureSelectedSlugInScope();
        const currentMunicipalities = filteredMunicipalities();
        state.progressTotal = currentMunicipalities.length;
        if (refs.prefecture) {
            refs.prefecture.value = state.prefecture;
        }
        if (refs.targetCount) {
            refs.targetCount.textContent = String(currentMunicipalities.length);
        }
        refs.searchButton.disabled = false;
        refs.searchButton.textContent = state.searching ? '中断する' : '横断検索する';
        renderProgress();
        renderMunicipalityList();
        renderResults();
    }

    function createAbortController() {
        const controller = new AbortController();
        state.abortControllers.add(controller);
        return controller;
    }

    function releaseAbortController(controller) {
        if (controller) {
            state.abortControllers.delete(controller);
        }
    }

    function abortActiveRequests() {
        for (const controller of state.abortControllers) {
            controller.abort();
        }
        state.abortControllers.clear();
        state.activeRequests.clear();
    }

    function stopSearch() {
        if (!state.searching) {
            return;
        }
        state.requestToken += 1;
        state.searching = false;
        state.stopped = true;
        abortActiveRequests();
        renderAll();
    }

    async function fetchJson(params, controller = null) {
        const response = await fetch(`${state.apiUrl}?${params.toString()}`, {
            headers: { Accept: 'application/json' },
            cache: 'no-store',
            signal: controller?.signal,
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
        const controller = createAbortController();
        try {
            const data = await fetchJson(new URLSearchParams({
                action: 'search',
                slug,
                q: state.query,
                page: String(page),
                per_page: '12',
            }), controller);
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
            releaseAbortController(controller);
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

    async function startSearch(query, prefecture = state.prefecture) {
        state.query = String(query || '').trim();
        state.prefecture = state.lockedSlug ? '' : normalizePrefecture(prefecture);
        state.requestToken += 1;
        state.searching = false;
        state.stopped = false;
        abortActiveRequests();
        state.progressDone = 0;
        state.progressTotal = filteredMunicipalities().length;
        state.results = new Map();
        ensureSelectedSlugInScope();
        updateUrl();
        renderAll();

        if (!state.query) {
            return;
        }

        const token = state.requestToken;
        state.selectedSlug = state.lockedSlug || '';
        state.searching = true;
        renderAll();

        const slugs = filteredMunicipalities().map((item) => item.slug);
        const applyPreview = async (slug) => {
            if (token !== state.requestToken || !state.searching) {
                return;
            }
            const municipality = municipalityBySlug.get(slug);
            if (!municipality) {
                return;
            }
            setActiveRequest(slug, 'preview', true);
            renderAll();
            const controller = createAbortController();
            try {
                const data = await fetchJson(new URLSearchParams({
                    action: 'search_preview',
                    q: state.query,
                    per_page: String(mixedResultLimit),
                    slug,
                }), controller);
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
                releaseAbortController(controller);
                setActiveRequest(slug, 'preview', false);
                if (token !== state.requestToken) {
                    return;
                }
                state.progressDone = Math.min(state.progressTotal, state.progressDone + 1);
                renderAll();
            }
        };

        await runChunkQueue(slugs, 2, applyPreview);
        if (token !== state.requestToken) {
            return;
        }

        state.searching = false;
        state.stopped = false;
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
        if (state.searching) {
            stopSearch();
            return;
        }
        startSearch(refs.query?.value || '', refs.prefecture?.value || '');
    });

    refs.prefecture?.addEventListener('change', () => {
        if (state.lockedSlug) {
            return;
        }
        if (state.searching) {
            stopSearch();
        } else {
            state.requestToken += 1;
            state.searching = false;
            abortActiveRequests();
        }
        state.stopped = false;
        state.prefecture = normalizePrefecture(refs.prefecture?.value || '');
        state.progressDone = 0;
        state.progressTotal = filteredMunicipalities().length;
        state.results = new Map();
        state.selectedSlug = '';
        updateUrl();
        renderAll();
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
        startSearch(state.query, state.prefecture);
    }
})();
