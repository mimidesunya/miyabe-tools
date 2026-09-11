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
        municipalityFilter: document.getElementById('search-municipality-filter'),
        municipalityFilterStatus: document.getElementById('search-municipality-filter-status'),
        pref: document.getElementById('search-pref'),
        startYear: document.getElementById('search-start-year'),
        endYear: document.getElementById('search-end-year'),
        startDate: document.getElementById('search-start-date'),
        endDate: document.getElementById('search-end-date'),
        startDateStatus: document.getElementById('search-start-date-status'),
        endDateStatus: document.getElementById('search-end-date-status'),
        sort: document.getElementById('search-sort'),
        tabs: Array.from(document.querySelectorAll('[data-doc-type]')),
        stats: document.getElementById('search-stats'),
        message: document.getElementById('message-area'),
        results: document.getElementById('results'),
        pager: document.getElementById('pager'),
        facets: document.getElementById('facet-list'),
        queryHelpOpen: document.querySelector('[data-query-help-open]'),
        queryHelpModal: document.querySelector('[data-query-help-modal]'),
        queryHelpPanel: document.querySelector('[data-query-help-modal] .help-modal-panel'),
        queryHelpCloseButtons: Array.from(document.querySelectorAll('[data-query-help-close]')),
        hitMapPanel: document.getElementById('hit-map-panel'),
        hitMapCanvas: document.getElementById('hit-map'),
        hitMapSummary: document.getElementById('hit-map-summary'),
        hitMapNote: document.getElementById('hit-map-note'),
        hitMapToggle: document.getElementById('hit-map-toggle'),
    };

    const prefNames = new Map((Array.isArray(boot.prefectures) ? boot.prefectures : [])
        .map((item) => [String(item.code || ''), String(item.name || '')]));
    function normalizeMunicipalityList(items) {
        return (Array.isArray(items) ? items : []).map((item) => ({
            slug: String(item.slug || '').trim(),
            code: String(item.code || '').trim(),
            name: String(item.name || '').trim(),
            nameKana: String(item.nameKana || '').trim(),
            fullName: String(item.fullName || '').trim(),
            prefCode: normalizePrefCode(item.prefCode),
            prefName: String(item.prefName || '').trim(),
            label: String(item.label || item.name || '').trim(),
        }))
        .filter((item) => item.slug && item.name);
    }

    let municipalities = normalizeMunicipalityList(boot.municipalities);
    let municipalityBySlug = new Map(municipalities.map((item) => [item.slug, item]));
    let municipalitiesLoading = municipalities.length === 0;
    let municipalitiesLoadFailed = false;
    const initialStartDate = normalizeDate(boot.startDate);
    const initialEndDate = normalizeDate(boot.endDate);

    const state = {
        apiUrl: String(boot.apiUrl || '/api/search'),
        docType: normalizeDocType(boot.docType),
        query: String(boot.query || '').trim(),
        slug: String(boot.slug || '').trim(),
        prefCode: normalizePrefCode(boot.prefCode) || normalizePrefCode(municipalityBySlug.get(String(boot.slug || '').trim())?.prefCode),
        startYear: initialStartDate ? initialStartDate.slice(0, 4) : normalizeYear(boot.startYear),
        endYear: initialEndDate ? initialEndDate.slice(0, 4) : normalizeYear(boot.endYear),
        startDate: initialStartDate,
        endDate: initialEndDate,
        sort: normalizeSort(boot.sort),
        municipalityFilter: '',
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
        return text === 'reiki' ? 'reiki' : 'minutes';
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

    function normalizeDate(value) {
        const text = String(value || '').trim();
        const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
        if (!match) {
            return '';
        }
        const year = Number(match[1]);
        const month = Number(match[2]);
        const day = Number(match[3]);
        const maxDays = [31, isLeapYear(year) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
        if (
            year < 1
            || year > 9999
            || month < 1
            || month > 12
            || day < 1
            || day > maxDays[month - 1]
        ) {
            return '';
        }
        return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    }

    function isLeapYear(year) {
        return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
    }

    function normalizeSort(value) {
        return String(value || '') === 'relevance' ? 'relevance' : 'date';
    }

    function normalizeFilterText(value) {
        return String(value || '')
            .normalize('NFKC')
            .toLowerCase()
            .replace(/\s+/g, '')
            .trim();
    }

    function docTypeLabel(value) {
        switch (value) {
            case 'minutes':
                return '会議録';
            case 'reiki':
                return '例規集';
            default:
                return '会議録';
        }
    }

    function resultKindLabel(value) {
        return value === 'minutes' ? '会議録' : (value === 'reiki' ? '例規集' : '文書');
    }

    function filteredMunicipalities() {
        const query = normalizeFilterText(state.municipalityFilter);
        return municipalities.filter((municipality) => {
            if (state.prefCode && municipality.prefCode !== state.prefCode) {
                return false;
            }
            if (!query) {
                return true;
            }
            const haystack = normalizeFilterText([
                municipality.name,
                municipality.nameKana,
                municipality.fullName,
                municipality.prefName,
                municipality.code,
                municipality.label,
                municipality.slug,
            ].filter(Boolean).join(' '));
            return haystack.includes(query);
        });
    }

    function municipalityOptionLabel(municipality) {
        if (state.prefCode) {
            return municipality.name;
        }
        return municipality.label || (municipality.prefName ? `${municipality.name}（${municipality.prefName}）` : municipality.name);
    }

    function renderMunicipalityOptions() {
        const visibleMunicipalities = filteredMunicipalities();
        const selectedMunicipality = state.slug ? municipalityBySlug.get(state.slug) : null;
        const selectedIsInPrefecture = !selectedMunicipality || !state.prefCode || selectedMunicipality.prefCode === state.prefCode;
        if (!municipalitiesLoading && state.slug && !selectedIsInPrefecture) {
            state.slug = '';
        }
        const selectedIsVisible = Boolean(state.slug && visibleMunicipalities.some((municipality) => municipality.slug === state.slug));
        const selectedFallback = state.slug && selectedMunicipality && selectedIsInPrefecture && !selectedIsVisible
            ? [selectedMunicipality]
            : [];

        const allLabel = state.prefCode
            ? `${prefNames.get(state.prefCode) || '選択中の都道府県'}すべて`
            : '全国';
        const fallbackOptions = municipalitiesLoading
            ? ['<option value="">読み込み中</option>']
            : municipalitiesLoadFailed
                ? ['<option value="">自治体一覧を取得できません</option>']
                : [`<option value="">${escapeHtml(allLabel)}</option>`];
        refs.slug.disabled = municipalitiesLoading || municipalitiesLoadFailed;
        const visibleSlugs = new Set(visibleMunicipalities.map((municipality) => municipality.slug));
        refs.slug.innerHTML = [
            ...fallbackOptions,
            ...selectedFallback.map((municipality) => (
                `<option value="${escapeHtml(municipality.slug)}" data-pref-code="${escapeHtml(municipality.prefCode)}">${escapeHtml(`選択中: ${municipalityOptionLabel(municipality)}`)}</option>`
            )),
            ...visibleMunicipalities.map((municipality) => (
                `<option value="${escapeHtml(municipality.slug)}" data-pref-code="${escapeHtml(municipality.prefCode)}">${escapeHtml(municipalityOptionLabel(municipality))}</option>`
            )),
        ].join('');
        refs.slug.value = state.slug;
        if (refs.municipalityFilter) {
            refs.municipalityFilter.value = state.municipalityFilter;
            refs.municipalityFilter.disabled = municipalitiesLoading || municipalitiesLoadFailed;
        }
        if (refs.municipalityFilterStatus) {
            if (municipalitiesLoading) {
                refs.municipalityFilterStatus.textContent = '自治体一覧を読み込み中';
            } else if (municipalitiesLoadFailed) {
                refs.municipalityFilterStatus.textContent = '自治体一覧を取得できません';
            } else if (state.municipalityFilter) {
                refs.municipalityFilterStatus.textContent = `${visibleSlugs.size}件に絞り込み`;
            } else {
                refs.municipalityFilterStatus.textContent = '';
            }
        }
    }

    function setMunicipalities(items) {
        municipalities = normalizeMunicipalityList(items);
        municipalityBySlug = new Map(municipalities.map((item) => [item.slug, item]));
        municipalitiesLoading = false;
        municipalitiesLoadFailed = municipalities.length === 0;
        if (!state.prefCode && state.slug) {
            state.prefCode = normalizePrefCode(municipalityBySlug.get(state.slug)?.prefCode);
        }
        renderAll();
    }

    async function loadMunicipalities() {
        if (!municipalitiesLoading) {
            return;
        }
        const url = String(boot.municipalitiesApiUrl || '/api/municipalities.php');
        try {
            const response = await fetch(url, {
                headers: { Accept: 'application/json' },
                cache: 'force-cache',
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload.status !== 'ok') {
                throw new Error(String(payload.error || `HTTP ${response.status}`));
            }
            setMunicipalities(payload.municipalities);
        } catch (_error) {
            municipalitiesLoading = false;
            municipalitiesLoadFailed = true;
            renderAll();
        }
    }

    function syncControls() {
        refs.query.value = state.query;
        refs.pref.value = state.prefCode;
        renderMunicipalityOptions();
        refs.startYear.value = state.startYear;
        refs.endYear.value = state.endYear;
        refs.startDate.value = state.startDate;
        refs.endDate.value = state.endDate;
        refs.sort.value = state.sort;
        renderDateStatus(refs.startDateStatus, state.startDate, 'start');
        renderDateStatus(refs.endDateStatus, state.endDate, 'end');
        refs.tabs.forEach((button) => {
            const active = button.getAttribute('data-doc-type') === state.docType;
            button.classList.toggle('is-active', active);
            button.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
    }

    function buildUrl() {
        const params = new URLSearchParams();
        if (state.query) params.set('q', state.query);
        if (state.docType !== 'minutes') params.set('doc_type', state.docType);
        if (state.slug) params.set('slug', state.slug);
        if (state.prefCode) params.set('pref_code', state.prefCode);
        if (state.startDate) {
            params.set('start_date', state.startDate);
        } else if (state.startYear) {
            params.set('start_year', state.startYear);
        }
        if (state.endDate) {
            params.set('end_date', state.endDate);
        } else if (state.endYear) {
            params.set('end_year', state.endYear);
        }
        if (state.sort !== 'date') params.set('sort', state.sort);
        return `${window.location.pathname}${params.toString() ? `?${params.toString()}` : ''}`;
    }

    /* 履歴に残す検索条件。これが変わったときだけ履歴を1つ積む */
    const HISTORY_KEYS = ['query', 'docType', 'slug', 'prefCode', 'startYear', 'endYear', 'startDate', 'endDate', 'sort', 'page'];

    /* 今の履歴項目に書いてある表示。地図の円やタブは state を書き換えてから runSearch を
       呼ぶので、「離れる前の表示」は state からではなくここから取る */
    let historyEntry = null;

    /** 今の検索条件の写し */
    function searchSnapshot() {
        const snapshot = {};
        for (const key of HISTORY_KEYS) {
            snapshot[key] = state[key];
        }
        return snapshot;
    }

    /** 地図の今の位置。「戻る」で同じ場所を見せるために履歴へ入れる */
    function mapViewSnapshot() {
        return hitMap
            ? { center: [hitMap.getCenter().lat, hitMap.getCenter().lng], zoom: hitMap.getZoom() }
            : null;
    }

    function sameSearch(a, b) {
        return HISTORY_KEYS.every((key) => String(a[key] ?? '') === String(b[key] ?? ''));
    }

    /** 履歴項目に写しが無いとき（別版で積まれた項目など）は URL から条件を組み立てる */
    function snapshotFromUrl() {
        const params = new URLSearchParams(window.location.search);
        const startDate = normalizeDate(params.get('start_date'));
        const endDate = normalizeDate(params.get('end_date'));
        return {
            query: String(params.get('q') || '').trim(),
            docType: normalizeDocType(params.get('doc_type') || params.get('type')),
            slug: String(params.get('slug') || '').trim(),
            prefCode: normalizePrefCode(params.get('pref_code') || params.get('pref')),
            startYear: startDate ? startDate.slice(0, 4) : normalizeYear(params.get('start_year')),
            endYear: endDate ? endDate.slice(0, 4) : normalizeYear(params.get('end_year')),
            startDate,
            endDate,
            sort: normalizeSort(params.get('sort')),
            page: 1,
            mapView: null,
            mapCollapsed: hitMapCollapsed,
        };
    }

    /**
     * 履歴を更新する。地図の円を押して絞り込んだあと「戻る」で絞る前の結果と地図の位置へ
     * 帰れるように、条件が変わるときは離れる表示（地図の位置つき）を今の履歴項目へ
     * 書き戻してから新しい表示を積む。同じ条件の引き直しは積まない（戻るを何度も押させない）。
     */
    function commitHistory() {
        const current = { ...searchSnapshot(), mapView: null, mapCollapsed: hitMapCollapsed };
        const url = buildUrl();
        if (historyEntry && !sameSearch(historyEntry, current)) {
            const leaving = { ...historyEntry, mapView: mapViewSnapshot(), mapCollapsed: hitMapCollapsed };
            window.history.replaceState({ search: leaving }, '', window.location.href);
            window.history.pushState({ search: current }, '', url);
        } else {
            window.history.replaceState({ search: current }, '', url);
        }
        historyEntry = current;
    }

    /** 「戻る」「進む」で履歴項目の表示へ戻す。履歴は触らない */
    function restoreSnapshot(snapshot) {
        state.query = String(snapshot.query || '').trim();
        state.docType = normalizeDocType(snapshot.docType);
        state.slug = String(snapshot.slug || '').trim();
        state.prefCode = normalizePrefCode(snapshot.prefCode);
        state.startDate = normalizeDate(snapshot.startDate);
        state.endDate = normalizeDate(snapshot.endDate);
        state.startYear = state.startDate ? state.startDate.slice(0, 4) : normalizeYear(snapshot.startYear);
        state.endYear = state.endDate ? state.endDate.slice(0, 4) : normalizeYear(snapshot.endYear);
        state.sort = normalizeSort(snapshot.sort);
        state.page = Math.max(1, Number(snapshot.page || 1));
        pendingMapView = snapshot.mapView || null;
        setHitMapCollapsed(Boolean(snapshot.mapCollapsed));
        historyEntry = { ...searchSnapshot(), mapView: null, mapCollapsed: hitMapCollapsed };
        syncControls();
        runSearch(state.page, { history: 'none' });
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
        if (state.startDate) {
            params.set('start_date', state.startDate);
        } else if (state.startYear) {
            params.set('start_year', state.startYear);
        }
        if (state.endDate) {
            params.set('end_date', state.endDate);
        } else if (state.endYear) {
            params.set('end_year', state.endYear);
        }
        // 「検索結果の内訳」を出すには集計を明示的に要求する必要がある。
        params.set('include_facets', '1');
        return params;
    }

    function renderDateStatus(element, date, edge) {
        if (!element) {
            return;
        }
        element.innerHTML = date
            ? `<span>日指定: ${escapeHtml(date)}</span><button type="button" data-clear-date="${escapeHtml(edge)}">解除</button>`
            : '';
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
            refs.facets.innerHTML = '<div class="facet-row"><span>結果</span><strong>まだ検索していません</strong></div>';
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
            : '<div class="facet-row"><span>内訳</span><strong>該当なし</strong></div>';
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

    function buildDetailUrl(item) {
        if (item.doc_type === 'minutes' && item.id) {
            const params = new URLSearchParams({
                id: String(item.id),
                doc_type: 'minutes',
            });
            if (state.query) {
                params.set('q', state.query);
            }
            return `/search/detail/?${params.toString()}`;
        }
        return item.detail_url || item.source_url || '#';
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
            const detailUrl = buildDetailUrl(item);
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
                        <a class="result-link" href="${escapeHtml(detailUrl)}" target="_blank" rel="noopener noreferrer">詳細</a>
                        ${sourceUrl ? `<a class="result-link" href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">原サイト</a>` : ''}
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

    /*
     * ---- 該当した自治体を地図に出す ----
     *
     * 一覧を読んでも「どこの話なのか」は掴めない。全国で何百件と当たったとき、
     * 北海道の話なのか西日本に偏っているのかは、並んだ見出しからは見えない。
     *
     * 出すのは検索の集計（aggregations.municipalities）で、表示中の頁の結果ではない。
     * **1頁目に出ている20件ではなく、当たった全体から数えた上位50自治体。**
     *
     * 50 はサーバ側の集計の上限。増やすと点が散るだけで、どこに集中しているかが
     * かえって見えなくなる（1〜2件しか当たっていない自治体まで全部打つことになる）。
     * そのかわり**上限に達したときは画面にそう書く**——「これで全部」に見せない。
     *
     * 座標はトップと同じ municipality-coordinates.js（自治体コードで引く）。
     * 地図とタイルもトップに合わせて Leaflet と地理院タイル。
     */
    const HIT_MAP_CENTER = [37.5, 137.0];
    const HIT_MAP_ZOOM = 4;
    /* サーバ側の自治体集計の上限（opensearch_search.php の terms size）と合わせる */
    const HIT_MAP_BUCKET_LIMIT = 50;
    const coordinatesByCode = (
        window.MIYABE_MUNICIPALITY_COORDINATES
        && typeof window.MIYABE_MUNICIPALITY_COORDINATES === 'object'
    ) ? window.MIYABE_MUNICIPALITY_COORDINATES : {};

    let hitMap = null;
    let hitMarkerLayer = null;
    let hitMapCollapsed = false;
    /* 「戻る」で来たとき、離れたときの地図の位置に戻すための控え。次の描画で使い切る */
    let pendingMapView = null;

    function hitMapAvailable() {
        return Boolean(refs.hitMapPanel && refs.hitMapCanvas && window.L);
    }

    function ensureHitMap() {
        if (hitMap || !hitMapAvailable()) {
            return hitMap;
        }
        hitMap = L.map(refs.hitMapCanvas, {
            zoomControl: true,
            scrollWheelZoom: false, // 頁を繰っているときに地図が拡大するのを防ぐ
        }).setView(HIT_MAP_CENTER, HIT_MAP_ZOOM);
        L.tileLayer('https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png', {
            attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">地理院タイル</a>',
            maxZoom: 16,
        }).addTo(hitMap);
        hitMarkerLayer = L.layerGroup().addTo(hitMap);
        return hitMap;
    }

    /** 件数から円の半径。件数は桁が開くので平方根で潰す */
    function hitRadius(count, max) {
        if (max <= 0) {
            return 5;
        }
        const ratio = Math.sqrt(count) / Math.sqrt(max);
        return 4 + ratio * 14;
    }

    function hitMapBuckets(payload) {
        const aggs = payload && payload.status === 'ok' ? (payload.aggregations || {}) : {};
        const buckets = Array.isArray(aggs.municipalities) ? aggs.municipalities : [];
        return buckets
            .map((bucket) => ({
                slug: String(bucket.key || '').trim(),
                count: Number(bucket.count) || 0,
            }))
            .filter((bucket) => bucket.slug && bucket.count > 0);
    }

    function renderHitMap(payload = state.lastPayload) {
        if (!refs.hitMapPanel) {
            return;
        }
        const buckets = hitMapBuckets(payload);
        if (!state.query || buckets.length === 0 || !hitMapAvailable()) {
            refs.hitMapPanel.hidden = true;
            return;
        }
        refs.hitMapPanel.hidden = false;
        ensureHitMap();
        hitMarkerLayer.clearLayers();

        const max = buckets.reduce((top, bucket) => Math.max(top, bucket.count), 0);
        const points = [];
        let missing = 0;

        for (const bucket of buckets) {
            const municipality = municipalityBySlug.get(bucket.slug);
            const coordinate = municipality ? coordinatesByCode[municipality.code] : null;
            if (!coordinate) {
                /* 座標が無いものは黙って落とさず、あとで件数を出す */
                missing += 1;
                continue;
            }
            points.push(coordinate);
            const marker = L.circleMarker(coordinate, {
                radius: hitRadius(bucket.count, max),
                color: '#8a2b2b',
                weight: 1,
                opacity: 0.9,
                fillColor: '#c0392b',
                fillOpacity: 0.55,
            });
            const label = municipality.label || municipality.name;
            marker.bindTooltip(`${label}　${bucket.count.toLocaleString('ja-JP')}件`, {
                direction: 'top',
                sticky: true,
            });
            /* 押したらその自治体だけに絞って引き直す。地図から掘り下げられるように */
            marker.on('click', () => {
                state.slug = bucket.slug;
                if (refs.slug) {
                    refs.slug.value = bucket.slug;
                }
                runSearch(1);
            });
            marker.addTo(hitMarkerLayer);
        }

        const total = buckets.reduce((sum, bucket) => sum + bucket.count, 0);
        const capped = buckets.length >= HIT_MAP_BUCKET_LIMIT;
        if (refs.hitMapSummary) {
            refs.hitMapSummary.textContent = capped
                ? `上位${buckets.length.toLocaleString('ja-JP')}自治体 / ${total.toLocaleString('ja-JP')}件`
                : `${buckets.length.toLocaleString('ja-JP')}自治体 / ${total.toLocaleString('ja-JP')}件`;
        }
        if (refs.hitMapNote) {
            const notes = ['円の大きさは件数。押すとその自治体だけに絞ります。'];
            if (capped) {
                /* 上限に達したときだけ言う。12自治体しか当たっていないのに
                   「上位」と書くと、それはそれで誤解を招く */
                notes.push('件数の多い順に50自治体まで出しています。これより少ない件数の自治体は地図に出ません。');
            }
            if (missing > 0) {
                /* 黙って減らさない。地図の点の数と自治体の数が合わない理由を書く */
                notes.push(`${missing}自治体は座標が無いため地図に出せません。`);
            }
            refs.hitMapNote.textContent = notes.join(' ');
        }

        const restoreView = pendingMapView;
        pendingMapView = null;
        /* 描画直後は入れ物の大きさが確定していないことがある */
        window.setTimeout(() => {
            hitMap.invalidateSize();
            if (restoreView && Array.isArray(restoreView.center) && Number.isFinite(restoreView.zoom)) {
                /* 「戻る」で来たときは、全体に合わせ直さず離れたときの位置へ */
                hitMap.setView(restoreView.center, restoreView.zoom);
            } else if (points.length > 0) {
                hitMap.fitBounds(L.latLngBounds(points), { padding: [24, 24], maxZoom: 10 });
            }
        }, 0);
    }

    function setHitMapCollapsed(collapsed) {
        hitMapCollapsed = Boolean(collapsed);
        if (!refs.hitMapPanel) {
            return;
        }
        refs.hitMapPanel.classList.toggle('is-collapsed', hitMapCollapsed);
        if (refs.hitMapToggle) {
            refs.hitMapToggle.textContent = hitMapCollapsed ? 'ひらく' : 'たたむ';
            refs.hitMapToggle.setAttribute('aria-expanded', hitMapCollapsed ? 'false' : 'true');
        }
        if (!hitMapCollapsed && hitMap) {
            window.setTimeout(() => hitMap.invalidateSize(), 0);
        }
    }

    function toggleHitMap() {
        setHitMapCollapsed(!hitMapCollapsed);
    }

    function renderAll() {
        syncControls();
        renderStats();
        renderFacets();
        renderHitMap();
        renderResults();
    }

    function openQueryHelp() {
        if (!refs.queryHelpModal) {
            return;
        }
        refs.queryHelpModal.hidden = false;
        document.body.classList.add('has-help-modal');
        refs.queryHelpPanel?.focus();
    }

    function closeQueryHelp() {
        if (!refs.queryHelpModal || refs.queryHelpModal.hidden) {
            return;
        }
        refs.queryHelpModal.hidden = true;
        document.body.classList.remove('has-help-modal');
        refs.queryHelpOpen?.focus();
    }

    /**
     * @param {number} page
     * @param {{history?: 'auto'|'none'}} options history が 'none' なら履歴を触らない（「戻る」からの復元用）
     */
    async function runSearch(page = 1, { history = 'auto' } = {}) {
        state.query = refs.query.value.trim();
        state.prefCode = normalizePrefCode(refs.pref.value);
        // 自治体一覧の読み込み前は select にまだ選択肢が無い。ここでフォーム値を
        // 読むと、URL で渡された slug が初回検索で消えてしまう。
        if (!municipalitiesLoading) {
            state.slug = refs.slug.value.trim();
        }
        state.startYear = normalizeYear(refs.startYear.value);
        state.endYear = normalizeYear(refs.endYear.value);
        state.startDate = normalizeDate(refs.startDate.value);
        state.endDate = normalizeDate(refs.endDate.value);
        if (state.startDate && state.endDate && state.startDate > state.endDate) {
            [state.startDate, state.endDate] = [state.endDate, state.startDate];
        }
        if (state.startDate) {
            state.startYear = state.startDate.slice(0, 4);
        }
        if (state.endDate) {
            state.endYear = state.endDate.slice(0, 4);
        }
        if (!state.startDate && !state.endDate && state.startYear && state.endYear && Number(state.startYear) > Number(state.endYear)) {
            [state.startYear, state.endYear] = [state.endYear, state.startYear];
        }
        state.sort = normalizeSort(refs.sort.value);
        state.page = Math.max(1, Number(page || 1));
        if (history !== 'none') {
            pendingMapView = null;
            commitHistory();
        }

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

    refs.pref.addEventListener('change', () => {
        state.prefCode = normalizePrefCode(refs.pref.value);
        renderMunicipalityOptions();
    });

    refs.hitMapToggle?.addEventListener('click', toggleHitMap);

    refs.municipalityFilter?.addEventListener('input', () => {
        state.municipalityFilter = refs.municipalityFilter.value.trim();
        renderMunicipalityOptions();
    });

    refs.slug.addEventListener('change', () => {
        state.slug = refs.slug.value.trim();
    });

    refs.startYear.addEventListener('change', () => {
        refs.startDate.value = '';
        state.startYear = normalizeYear(refs.startYear.value);
        state.startDate = '';
        renderDateStatus(refs.startDateStatus, '', 'start');
    });

    refs.endYear.addEventListener('change', () => {
        refs.endDate.value = '';
        state.endYear = normalizeYear(refs.endYear.value);
        state.endDate = '';
        renderDateStatus(refs.endDateStatus, '', 'end');
    });

    refs.startDate.addEventListener('change', () => {
        state.startDate = normalizeDate(refs.startDate.value);
        if (state.startDate) {
            state.startYear = state.startDate.slice(0, 4);
            refs.startYear.value = state.startYear;
        }
        renderDateStatus(refs.startDateStatus, state.startDate, 'start');
    });

    refs.endDate.addEventListener('change', () => {
        state.endDate = normalizeDate(refs.endDate.value);
        if (state.endDate) {
            state.endYear = state.endDate.slice(0, 4);
            refs.endYear.value = state.endYear;
        }
        renderDateStatus(refs.endDateStatus, state.endDate, 'end');
    });

    refs.form.addEventListener('click', (event) => {
        const button = event.target.closest('[data-clear-date]');
        if (!button) {
            return;
        }
        const edge = button.getAttribute('data-clear-date');
        if (edge === 'start') {
            refs.startDate.value = '';
            state.startDate = '';
            renderDateStatus(refs.startDateStatus, '', 'start');
        } else if (edge === 'end') {
            refs.endDate.value = '';
            state.endDate = '';
            renderDateStatus(refs.endDateStatus, '', 'end');
        }
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

    refs.queryHelpOpen?.addEventListener('click', openQueryHelp);
    refs.queryHelpCloseButtons.forEach((button) => {
        button.addEventListener('click', closeQueryHelp);
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            closeQueryHelp();
        }
    });

    window.addEventListener('popstate', (event) => {
        const snapshot = event.state && event.state.search ? event.state.search : snapshotFromUrl();
        restoreSnapshot(snapshot);
    });

    syncControls();
    renderAll();
    loadMunicipalities();
    if (state.query) {
        runSearch(1);
    } else {
        /* 検索前でも最初の項目に写しを持たせ、戻ってきたときに URL を読み直さずに済ませる */
        historyEntry = { ...searchSnapshot(), mapView: null, mapCollapsed: hitMapCollapsed };
        window.history.replaceState({ search: historyEntry }, '', window.location.href);
    }
})();
