(() => {
    const bootNode = document.getElementById('search-boot');
    if (!bootNode) {
        return;
    }

    const boot = JSON.parse(bootNode.textContent || '{}');
    const refs = {
        form: document.getElementById('search-form'),
        query: document.getElementById('search-query'),
        slug: document.getElementById('search-slug'),
        pref: document.getElementById('search-pref'),
        startYear: document.getElementById('search-start-year'),
        endYear: document.getElementById('search-end-year'),
        sort: document.getElementById('search-sort'),
        tabs: Array.from(document.querySelectorAll('[data-doc-type]')),
        stats: document.getElementById('search-stats'),
        message: document.getElementById('message-area'),
        results: document.getElementById('results'),
        pager: document.getElementById('pager'),
        facets: document.getElementById('facet-list'),
    };

    const prefNames = new Map((Array.isArray(boot.prefectures) ? boot.prefectures : [])
        .map((item) => [String(item.code || ''), String(item.name || '')]));

    const state = {
        apiUrl: String(boot.apiUrl || '/api/search'),
        docType: normalizeDocType(boot.docType),
        query: String(boot.query || '').trim(),
        slug: String(boot.slug || '').trim(),
        prefCode: normalizePrefCode(boot.prefCode),
        startYear: normalizeYear(boot.startYear),
        endYear: normalizeYear(boot.endYear),
        sort: normalizeSort(boot.sort),
        page: 1,
        perPage: 20,
        loading: false,
        lastPayload: null,
        abortController: null,
    };

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function normalizeDocType(value) {
        const text = String(value || '').trim();
        return ['all', 'minutes', 'reiki'].includes(text) ? text : 'all';
    }

    function normalizePrefCode(value) {
        let text = String(value || '').replace(/[^0-9]/g, '');
        if (text.length === 1) {
            text = `0${text}`;
        }
        return /^\d{2}$/.test(text) ? text : '';
    }

    function normalizeYear(value) {
        const text = String(value || '').trim();
        if (!/^\d{1,4}$/.test(text)) {
            return '';
        }
        const year = Number(text);
        return Number.isInteger(year) && year > 0 && year <= 9999 ? String(year) : '';
    }

    function normalizeSort(value) {
        return String(value || '') === 'date' ? 'date' : 'relevance';
    }

    function docTypeLabel(value) {
        switch (value) {
            case 'minutes':
                return '会議録';
            case 'reiki':
                return '例規集';
            default:
                return '統合';
        }
    }

    function resultKindLabel(value) {
        return value === 'minutes' ? '会議録' : (value === 'reiki' ? '例規集' : '文書');
    }

    function syncControls() {
        refs.query.value = state.query;
        refs.slug.value = state.slug;
        refs.pref.value = state.prefCode;
        refs.startYear.value = state.startYear;
        refs.endYear.value = state.endYear;
        refs.sort.value = state.sort;
        refs.tabs.forEach((button) => {
            const active = button.getAttribute('data-doc-type') === state.docType;
            button.classList.toggle('is-active', active);
            button.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
    }

    function updateUrl() {
        const params = new URLSearchParams();
        if (state.query) params.set('q', state.query);
        if (state.docType !== 'all') params.set('doc_type', state.docType);
        if (state.slug) params.set('slug', state.slug);
        if (state.prefCode) params.set('pref_code', state.prefCode);
        if (state.startYear) params.set('start_year', state.startYear);
        if (state.endYear) params.set('end_year', state.endYear);
        if (state.sort !== 'relevance') params.set('sort', state.sort);
        const url = `${window.location.pathname}${params.toString() ? `?${params.toString()}` : ''}`;
        window.history.replaceState({}, '', url);
    }

    function apiParams(page = state.page) {
        const params = new URLSearchParams({
            q: state.query,
            doc_type: state.docType,
            page: String(page),
            per_page: String(state.perPage),
            sort: state.sort,
        });
        if (state.slug) params.set('slug', state.slug);
        if (state.prefCode) params.set('pref_code', state.prefCode);
        if (state.startYear) params.set('start_year', state.startYear);
        if (state.endYear) params.set('end_year', state.endYear);
        return params;
    }

    function renderMessage(text, tone = '') {
        refs.message.innerHTML = text
            ? `<div class="message ${tone ? `is-${escapeHtml(tone)}` : ''}">${escapeHtml(text)}</div>`
            : '';
    }

    function renderStats(payload = state.lastPayload) {
        if (!payload || payload.status !== 'ok') {
            refs.stats.innerHTML = [
                { label: '検索範囲', value: docTypeLabel(state.docType) },
                { label: '結果', value: state.loading ? '検索中' : '未検索' },
            ].map(renderStat).join('');
            return;
        }
        refs.stats.innerHTML = [
            { label: '検索範囲', value: docTypeLabel(payload.doc_type || state.docType) },
            { label: 'ヒット', value: `${Number(payload.total || 0)}${payload.total_relation === 'gte' ? '+' : ''}` },
            { label: '応答', value: `${Number(payload.took_ms || 0)} ms` },
        ].map(renderStat).join('');
    }

    function renderStat(item) {
        return `<div class="stat"><span>${escapeHtml(item.label)}</span><strong>${escapeHtml(item.value)}</strong></div>`;
    }

    function renderFacets(payload = state.lastPayload) {
        if (!payload || payload.status !== 'ok') {
            refs.facets.innerHTML = '<div class="facet-row"><span>結果</span><strong>未検索</strong></div>';
            return;
        }
        const aggs = payload.aggregations || {};
        const rows = [];
        for (const bucket of Array.isArray(aggs.doc_types) ? aggs.doc_types : []) {
            rows.push([resultKindLabel(bucket.key), bucket.count]);
        }
        for (const bucket of Array.isArray(aggs.prefectures) ? aggs.prefectures.slice(0, 8) : []) {
            rows.push([prefNames.get(String(bucket.key || '')) || String(bucket.key || ''), bucket.count]);
        }
        refs.facets.innerHTML = rows.length
            ? rows.map(([label, count]) => `<div class="facet-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(count)}</strong></div>`).join('')
            : '<div class="facet-row"><span>facet</span><strong>なし</strong></div>';
    }

    function renderExcerpt(value) {
        return escapeHtml(value)
            .replace(/\[\[\[/g, '<mark>')
            .replace(/\]\]\]/g, '</mark>')
            .replace(/\n/g, '<br>');
    }

    function displayDate(item) {
        return item.held_on || item.promulgated_on || item.sort_date || item.updated_at || '';
    }

    function resultMeta(item) {
        const parts = [
            item.pref_name,
            item.municipality_name,
            item.assembly_name,
            item.meeting_name,
            item.category,
            item.year_label,
            item.source_system,
        ];
        return parts.map((part) => String(part || '').trim()).filter(Boolean);
    }

    function renderResults(payload = state.lastPayload) {
        if (state.loading) {
            refs.results.innerHTML = '';
            refs.pager.innerHTML = '';
            renderMessage('検索中です。');
            return;
        }
        if (!state.query) {
            refs.results.innerHTML = '';
            refs.pager.innerHTML = '';
            renderMessage('キーワードを入力してください。');
            return;
        }
        if (!payload || payload.status !== 'ok') {
            refs.results.innerHTML = '';
            refs.pager.innerHTML = '';
            renderMessage(String(payload?.error || '検索結果はまだありません。'), payload?.status === 'ok' ? '' : 'error');
            return;
        }
        const items = Array.isArray(payload.items) ? payload.items : [];
        if (items.length === 0) {
            refs.results.innerHTML = '';
            refs.pager.innerHTML = '';
            renderMessage('該当する文書がありません。');
            return;
        }

        renderMessage('');
        refs.results.innerHTML = items.map((item) => {
            const meta = resultMeta(item);
            const detailUrl = item.detail_url || item.source_url || '#';
            const sourceUrl = item.source_url || '';
            return `
                <article class="result-item">
                    <div class="result-top">
                        <span class="result-kind">${escapeHtml(resultKindLabel(item.doc_type))}</span>
                        <span class="result-date">${escapeHtml(displayDate(item))}</span>
                    </div>
                    <h2 class="result-title">${renderExcerpt(item.title_highlight || item.title || '')}</h2>
                    ${meta.length ? `<div class="result-meta">${meta.map((value) => `<span>${escapeHtml(value)}</span>`).join('')}</div>` : ''}
                    ${item.excerpt ? `<p class="result-excerpt">${renderExcerpt(item.excerpt)}</p>` : ''}
                    <div class="result-actions">
                        <a class="result-link" href="${escapeHtml(detailUrl)}">詳細</a>
                        ${sourceUrl ? `<a class="result-link" href="${escapeHtml(sourceUrl)}" target="_blank" rel="noreferrer">原サイト</a>` : ''}
                    </div>
                </article>
            `;
        }).join('');

        const currentPage = Number(payload.page || 1);
        refs.pager.innerHTML = `
            <button type="button" data-page="${currentPage - 1}" ${currentPage <= 1 ? 'disabled' : ''}>前へ</button>
            <span>${escapeHtml(String(currentPage))}</span>
            <button type="button" data-page="${currentPage + 1}" ${payload.has_more ? '' : 'disabled'}>次へ</button>
        `;
    }

    function renderAll() {
        syncControls();
        renderStats();
        renderFacets();
        renderResults();
    }

    async function runSearch(page = 1) {
        state.query = refs.query.value.trim();
        state.slug = refs.slug.value.trim();
        state.prefCode = normalizePrefCode(refs.pref.value);
        state.startYear = normalizeYear(refs.startYear.value);
        state.endYear = normalizeYear(refs.endYear.value);
        state.sort = normalizeSort(refs.sort.value);
        state.page = Math.max(1, Number(page || 1));
        updateUrl();

        if (state.abortController) {
            state.abortController.abort();
        }
        if (!state.query) {
            state.lastPayload = null;
            state.loading = false;
            renderAll();
            return;
        }

        state.loading = true;
        state.lastPayload = null;
        renderAll();

        const controller = new AbortController();
        state.abortController = controller;
        try {
            const response = await fetch(`${state.apiUrl}?${apiParams(state.page).toString()}`, {
                headers: { Accept: 'application/json' },
                cache: 'no-store',
                signal: controller.signal,
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(String(payload.error || `HTTP ${response.status}`));
            }
            state.lastPayload = payload;
        } catch (error) {
            if (controller.signal.aborted) {
                return;
            }
            state.lastPayload = {
                status: 'error',
                error: error instanceof Error ? error.message : '検索に失敗しました。',
                items: [],
                total: 0,
            };
        } finally {
            if (state.abortController === controller) {
                state.abortController = null;
            }
            if (!controller.signal.aborted) {
                state.loading = false;
                renderAll();
            }
        }
    }

    refs.tabs.forEach((button) => {
        button.addEventListener('click', () => {
            state.docType = normalizeDocType(button.getAttribute('data-doc-type'));
            runSearch(1);
        });
    });

    refs.form.addEventListener('submit', (event) => {
        event.preventDefault();
        runSearch(1);
    });

    refs.pager.addEventListener('click', (event) => {
        const button = event.target.closest('[data-page]');
        if (!button || button.disabled) {
            return;
        }
        const page = Number(button.getAttribute('data-page') || '1');
        if (Number.isFinite(page) && page >= 1) {
            runSearch(page);
        }
    });

    syncControls();
    renderAll();
    if (state.query) {
        runSearch(1);
    }
})();
