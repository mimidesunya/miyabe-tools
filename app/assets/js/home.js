(() => {
    const apiUrl = String(window.HOMEPAGE_API_URL || '/api/home.php');
    const taskStatusApiUrl = String(window.HOMEPAGE_TASK_STATUS_API_URL || '/api/task-status.php');

    const mapElement = document.querySelector('[data-coverage-map]');
    const loadingPanel = document.querySelector('[data-home-loading]');
    const statbar = document.querySelector('[data-home-statbar]');
    const detailPanel = document.querySelector('[data-home-detail]');
    const resultList = document.querySelector('[data-home-grid]');
    const displayCountElement = document.querySelector('[data-home-display-count]');
    const municipalityCountElement = document.querySelector('[data-home-municipality-count]');
    const generatedAtElement = document.querySelector('[data-home-generated-at]');
    const taskSummariesElement = document.querySelector('[data-home-task-summaries]');
    const filterHint = document.querySelector('[data-home-filter-hint]');
    const prefectureSelect = document.querySelector('[data-home-prefecture-filter]');
    const issueFilterSelect = document.querySelector('[data-home-issue-filter]');
    const searchInput = document.querySelector('[data-home-search]');
    const featureButtons = Array.from(document.querySelectorAll('[data-feature-filter]'));
    const runningSection = document.querySelector('[data-running-section]');
    const runningSummaryList = document.querySelector('[data-running-summary-list]');
    const runningList = document.querySelector('[data-running-list]');

    const featureKeys = ['gijiroku', 'reiki', 'boards'];
    const featureMeta = {
        gijiroku: { label: '会議録', shortLabel: '会', color: '#2563eb' },
        reiki: { label: '例規集', shortLabel: '例', color: '#b45309' },
        boards: { label: '掲示板', shortLabel: '掲', color: '#15803d' },
    };
    const detailedMarkerMinZoom = 7;

    const prefectures = [
        ['01', '北海道', '北海道', 43.06417, 141.34694],
        ['02', '青森県', '青森', 40.82444, 140.74],
        ['03', '岩手県', '岩手', 39.70361, 141.1525],
        ['04', '宮城県', '宮城', 38.26889, 140.87194],
        ['05', '秋田県', '秋田', 39.71861, 140.1025],
        ['06', '山形県', '山形', 38.24056, 140.36333],
        ['07', '福島県', '福島', 37.75, 140.46778],
        ['08', '茨城県', '茨城', 36.34139, 140.44667],
        ['09', '栃木県', '栃木', 36.56583, 139.88361],
        ['10', '群馬県', '群馬', 36.39111, 139.06083],
        ['11', '埼玉県', '埼玉', 35.85694, 139.64889],
        ['12', '千葉県', '千葉', 35.60472, 140.12333],
        ['13', '東京都', '東京', 35.68944, 139.69167],
        ['14', '神奈川県', '神奈川', 35.44778, 139.6425],
        ['15', '新潟県', '新潟', 37.90222, 139.02361],
        ['16', '富山県', '富山', 36.69528, 137.21139],
        ['17', '石川県', '石川', 36.59444, 136.62556],
        ['18', '福井県', '福井', 36.06528, 136.22194],
        ['19', '山梨県', '山梨', 35.66389, 138.56833],
        ['20', '長野県', '長野', 36.65139, 138.18111],
        ['21', '岐阜県', '岐阜', 35.39111, 136.72222],
        ['22', '静岡県', '静岡', 34.97694, 138.38306],
        ['23', '愛知県', '愛知', 35.18028, 136.90667],
        ['24', '三重県', '三重', 34.73028, 136.50861],
        ['25', '滋賀県', '滋賀', 35.00444, 135.86833],
        ['26', '京都府', '京都', 35.02139, 135.75556],
        ['27', '大阪府', '大阪', 34.68639, 135.52],
        ['28', '兵庫県', '兵庫', 34.69139, 135.18306],
        ['29', '奈良県', '奈良', 34.68528, 135.83278],
        ['30', '和歌山県', '和歌山', 34.22611, 135.1675],
        ['31', '鳥取県', '鳥取', 35.50361, 134.23833],
        ['32', '島根県', '島根', 35.47222, 133.05056],
        ['33', '岡山県', '岡山', 34.66167, 133.935],
        ['34', '広島県', '広島', 34.39639, 132.45944],
        ['35', '山口県', '山口', 34.18583, 131.47139],
        ['36', '徳島県', '徳島', 34.06583, 134.55944],
        ['37', '香川県', '香川', 34.34028, 134.04333],
        ['38', '愛媛県', '愛媛', 33.84167, 132.76611],
        ['39', '高知県', '高知', 33.55972, 133.53111],
        ['40', '福岡県', '福岡', 33.60639, 130.41806],
        ['41', '佐賀県', '佐賀', 33.24944, 130.29889],
        ['42', '長崎県', '長崎', 32.74472, 129.87361],
        ['43', '熊本県', '熊本', 32.78972, 130.74167],
        ['44', '大分県', '大分', 33.23806, 131.6125],
        ['45', '宮崎県', '宮崎', 31.91111, 131.42389],
        ['46', '鹿児島県', '鹿児島', 31.56028, 130.55806],
        ['47', '沖縄県', '沖縄', 26.2125, 127.68111],
    ].map(([code, name, shortName, lat, lon]) => ({ code, name, shortName, lat, lon }));

    const prefectureByCode = new Map(prefectures.map((item) => [item.code, item]));
    const prefectureByName = new Map(prefectures.map((item) => [item.name, item]));

    const municipalityCoordinates = (
        window.MIYABE_MUNICIPALITY_COORDINATES
        && typeof window.MIYABE_MUNICIPALITY_COORDINATES === 'object'
    ) ? window.MIYABE_MUNICIPALITY_COORDINATES : {};

    const defaultFeature = 'gijiroku';

    const state = {
        payload: null,
        feature: normalizeFeature(readQueryParam('feature')),
        prefecture: normalizePrefecture(readQueryParam('prefecture') || 'all'),
        issue: normalizeIssue(readQueryParam('status') || 'all'),
        query: readQueryParam('q') || '',
        selectedSlug: '',
        openPopupSlug: '',
        latestTaskStatusEtag: '',
        latestProcessingStatusPayload: null,
    };

    let map = null;
    let municipalityLayer = null;
    let selectedMarker = null;
    let searchDebounceTimer = 0;
    let renderedMarkerMode = '';
    let markersBySlug = new Map();
    let moveRenderTimer = 0;
    let lastPayloadRaw = '';
    let hasFitOnce = false;

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function readQueryParam(name) {
        try {
            return String(new URLSearchParams(window.location.search).get(name) || '').trim();
        } catch (error) {
            return '';
        }
    }

    function writeQueryState() {
        try {
            const url = new URL(window.location.href);
            state.feature === defaultFeature ? url.searchParams.delete('feature') : url.searchParams.set('feature', state.feature);
            state.prefecture === 'all' ? url.searchParams.delete('prefecture') : url.searchParams.set('prefecture', state.prefecture);
            state.issue === 'all' ? url.searchParams.delete('status') : url.searchParams.set('status', state.issue);
            state.query === '' ? url.searchParams.delete('q') : url.searchParams.set('q', state.query);
            window.history.replaceState(null, '', url.toString());
        } catch (error) {
            console.warn('failed to update home query state', error);
        }
    }

    function normalizeFeature(value) {
        if (value === 'minutes') return 'gijiroku';
        if (value === 'poster' || value === 'posters') return 'boards';
        return ['gijiroku', 'reiki', 'boards'].includes(value) ? value : defaultFeature;
    }

    function normalizeIssue(value) {
        return [
            'all',
            'ready',
            'issues',
            'errors',
            'warnings',
            'pending',
            'runtime_error',
            'warning',
            'unacquired',
            'excluded',
            'review_required',
            'unsupported',
            'source_unresolved',
            'publish_pending',
            'search_pending',
            'suspended',
            'partial_planned',
            'index_pending',
            'partial_error',
            'partial_recent_only',
            'update_error',
            'coverage_unknown',
        ].includes(value) ? value : 'all';
    }

    function normalizePrefecture(value) {
        const raw = String(value || '').trim();
        if (raw === '' || raw === 'all' || raw === '全国') return 'all';
        const code = raw.padStart(2, '0').slice(0, 2);
        if (/^\d{2}$/.test(code) && prefectureByCode.has(code)) return code;
        const matched = prefectures.find((prefecture) => prefecture.name === raw || prefecture.shortName === raw);
        return matched ? matched.code : 'all';
    }

    function prefCodeFromCard(card) {
        const explicit = String(card?.prefecture_code || '').padStart(2, '0').slice(0, 2);
        if (/^\d{2}$/.test(explicit) && prefectureByCode.has(explicit)) return explicit;
        const slugMatch = String(card?.slug || '').match(/^(\d{2})/);
        if (slugMatch && prefectureByCode.has(slugMatch[1])) return slugMatch[1];
        const matched = prefectureByName.get(String(card?.prefecture_label || ''));
        return matched ? matched.code : '';
    }

    function municipalityCodeFromCard(card) {
        const explicit = String(card?.municipality_code || card?.code || '').trim();
        if (/^\d{5}$/.test(explicit)) return explicit;
        const slugMatch = String(card?.slug || '').match(/^(\d{5})/);
        return slugMatch ? slugMatch[1] : '';
    }

    function featureByKey(card, key) {
        const features = Array.isArray(card?.features) ? card.features : [];
        return features.find((feature) => String(feature?.feature_key || '') === key) || null;
    }

    function featureSearchCoverage(card, key = 'gijiroku') {
        const coverage = featureByKey(card, key)?.search_coverage;
        if (!coverage || typeof coverage !== 'object') return null;
        const from = String(coverage.from || '').trim();
        const to = String(coverage.to || '').trim();
        if (!/^\d{4}-\d{2}-\d{2}$/.test(from) || !/^\d{4}-\d{2}-\d{2}$/.test(to)) return null;
        return {
            from,
            to,
            documentCount: Math.max(0, Number(coverage.document_count || 0)),
        };
    }

    function formatCoverageDate(value) {
        const match = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
        if (!match) return String(value || '');
        return `${match[1]}年${Number(match[2])}月${Number(match[3])}日`;
    }

    function searchCoverageText(coverage, options = {}) {
        if (!coverage) return '';
        const from = options.compact === true ? coverage.from : formatCoverageDate(coverage.from);
        const to = options.compact === true ? coverage.to : formatCoverageDate(coverage.to);
        const range = from === to ? from : `${from}〜${to}`;
        if (options.includeCount !== false && coverage.documentCount > 0) {
            return `${range}（${coverage.documentCount}件）`;
        }
        return range;
    }

    function hasFeature(card, key) {
        return featureByKey(card, key) !== null;
    }

    function featureReady(card, key) {
        const feature = featureByKey(card, key);
        return feature !== null && String(feature.mode || '') === 'link';
    }

    function cardReadyCount(card) {
        return featureKeys.filter((key) => featureReady(card, key)).length;
    }

    function featureAvailabilityState(feature) {
        const explicit = String(feature?.availability_state || '').trim();
        if (explicit !== '') return explicit;
        if (feature?.has_error === true) return 'runtime_error';
        if (feature?.has_warning === true) return 'warning';
        return String(feature?.mode || '') === 'link' ? 'ready' : 'unacquired';
    }

    function cardHasIssue(card, key = state.feature) {
        const feature = featureByKey(card, key);
        return feature?.has_error === true || feature?.has_warning === true;
    }

    function cardIsPending(card, key = state.feature) {
        const feature = featureByKey(card, key);
        return feature !== null && featureAvailabilityState(feature) !== 'ready';
    }

    function cardMatchesFilters(card) {
        const prefCode = prefCodeFromCard(card);
        if (state.prefecture !== 'all' && prefCode !== state.prefecture) return false;
        if (!hasFeature(card, state.feature)) return false;
        const selectedFeature = featureByKey(card, state.feature);
        const availabilityState = featureAvailabilityState(selectedFeature);
        if (state.issue === 'ready' && !featureReady(card, state.feature)) return false;
        if (state.issue === 'issues' && !cardHasIssue(card)) return false;
        if (state.issue === 'errors' && availabilityState !== 'runtime_error') return false;
        if (state.issue === 'warnings' && availabilityState !== 'warning') return false;
        if (state.issue === 'pending' && !cardIsPending(card)) return false;
        const directStates = [
            'runtime_error',
            'warning',
            'unacquired',
            'excluded',
            'review_required',
            'unsupported',
            'source_unresolved',
            'publish_pending',
            'search_pending',
            'suspended',
            'partial_planned',
            'index_pending',
            'partial_error',
            'partial_recent_only',
            'update_error',
            'coverage_unknown',
        ];
        if (directStates.includes(state.issue) && availabilityState !== state.issue) return false;
        const query = state.query.trim().toLowerCase();
        if (query !== '') {
            const haystack = [
                card?.name,
                card?.prefecture_label,
                card?.slug,
                card?.available_summary,
                selectedFeature?.status_label,
                selectedFeature?.system_type,
                selectedFeature?.display?.detail,
            ].join(' ').toLowerCase();
            if (!haystack.includes(query)) return false;
        }
        return true;
    }

    function allCards() {
        return Array.isArray(state.payload?.municipalities) ? state.payload.municipalities : [];
    }

    function visibleCards() {
        return allCards().filter(cardMatchesFilters);
    }

    function groupByPrefecture(cards) {
        const groups = new Map();
        for (const card of cards) {
            const prefCode = prefCodeFromCard(card);
            if (!groups.has(prefCode)) groups.set(prefCode, []);
            groups.get(prefCode).push(card);
        }
        return groups;
    }

    function fallbackCoordinate(card) {
        const pref = prefectureByCode.get(prefCodeFromCard(card));
        if (!pref) return [36.2048, 138.2529];
        return [pref.lat, pref.lon];
    }

    function coordinateForCard(card) {
        const coordinate = municipalityCoordinates[municipalityCodeFromCard(card)];
        return Array.isArray(coordinate) ? coordinate : fallbackCoordinate(card);
    }

    function hasKnownCoordinate(card) {
        return Array.isArray(municipalityCoordinates[municipalityCodeFromCard(card)]);
    }

    // マーカー表示は選択中の機能だけに絞る。他機能の対応状況は詳細パネルと一覧で確認する。
    function markerTitle(card) {
        const coordinateNote = hasKnownCoordinate(card) ? '' : '（概略位置）';
        const meta = featureMeta[state.feature] || { label: state.feature };
        const status = featureReady(card, state.feature) ? '' : '準備中';
        return `${card?.prefecture_label || ''} ${card?.name || ''}${coordinateNote}: ${meta.label}${status}`;
    }

    function markerMode() {
        return map && map.getZoom() >= detailedMarkerMinZoom ? 'detailed' : 'simple';
    }

    function markerFillColor() {
        return featureMeta[state.feature]?.color || '#64748b';
    }

    function simpleMarkerStyle(card, selected) {
        return {
            radius: selected ? 8 : 6,
            color: selected ? '#111827' : '#ffffff',
            weight: selected ? 3 : 1.5,
            fillColor: markerFillColor(),
            fillOpacity: featureReady(card, state.feature) ? 0.82 : 0.48,
            opacity: 1,
            dashArray: hasKnownCoordinate(card) ? null : '3 3',
        };
    }

    function simpleMarker(card, coordinate, selected) {
        return L.circleMarker(coordinate, simpleMarkerStyle(card, selected));
    }

    // ツールチップとポップアップは全マーカー分を事前生成せず、触られたときに初めて作る。
    function attachMarkerInteractions(marker, card) {
        marker.__homeSlug = String(card.slug || '');
        marker.on('mouseover', () => {
            if (!marker.getTooltip()) {
                marker.bindTooltip(markerTitle(card), { direction: 'top', sticky: true });
            }
            marker.openTooltip();
        });
        marker.on('click', () => selectCard(marker.__homeSlug, { pan: false }));
    }

    function openMarkerPopup(slug, card) {
        const marker = markersBySlug.get(slug);
        if (!marker) {
            state.openPopupSlug = slug;
            return;
        }
        state.openPopupSlug = '';
        // autoPan は再構築時の再表示と連鎖して地図が勝手に流れるので使わない。
        if (!marker.getPopup()) marker.bindPopup(renderPopup(card), { maxWidth: 340, autoPan: false });
        marker.openPopup();
    }

    function markerIcon(card, selected) {
        const key = state.feature;
        const ready = featureReady(card, key);
        const meta = featureMeta[key] || { shortLabel: '?' };
        const badge = `<span class="map-pin-dot map-pin-dot-${escapeHtml(key)}${ready ? '' : ' is-pending'}">${escapeHtml(meta.shortLabel)}</span>`;
        const feature = featureByKey(card, key);
        const hasIssue = card?.has_error === true || card?.has_warning === true
            || feature?.has_error === true || feature?.has_warning === true;
        const classes = [
            'map-pin',
            selected ? 'is-selected' : '',
            hasIssue ? 'is-issue' : '',
            ready ? '' : 'is-pending',
            hasKnownCoordinate(card) ? '' : 'is-approximate',
        ].filter(Boolean).join(' ');
        return L.divIcon({
            className: 'municipality-pin-icon',
            html: `<div class="${classes}">${badge}</div>`,
            iconSize: [34, 32],
            iconAnchor: [17, 16],
            popupAnchor: [0, -16],
            tooltipAnchor: [0, -18],
        });
    }

    function initMap() {
        if (map || !mapElement || typeof L === 'undefined') return;
        map = L.map(mapElement, {
            zoomControl: true,
            preferCanvas: true,
        }).setView([36.2048, 138.2529], 5);

        const gsiStd = L.tileLayer('https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png', {
            attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">地理院タイル</a>',
            minZoom: 2,
            maxZoom: 18,
        }).addTo(map);
        const gsiPale = L.tileLayer('https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png', {
            attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">地理院タイル</a>',
            minZoom: 2,
            maxZoom: 18,
        });
        municipalityLayer = L.layerGroup().addTo(map);
        L.control.layers({
            '国土地理院 標準地図': gsiStd,
            '国土地理院 淡色地図': gsiPale,
        }, null, { position: 'topleft' }).addTo(map);

        // 詳細ピンは表示範囲内だけ描画するので、移動・ズームのたびに対象を組み直す。
        // 簡易マーカー(ズームアウト時)はモードが変わらない限り再描画しない。
        map.on('moveend', () => {
            if (!state.payload) return;
            const nextMode = markerMode();
            if (nextMode === renderedMarkerMode && nextMode !== 'detailed') return;
            window.clearTimeout(moveRenderTimer);
            moveRenderTimer = window.setTimeout(() => renderMap({ fit: false }), 120);
        });
    }

    function renderMap(options = {}) {
        initMap();
        if (!map || !municipalityLayer) return;
        if (selectedMarker && typeof selectedMarker.isPopupOpen === 'function'
            && selectedMarker.isPopupOpen() && state.openPopupSlug === '') {
            state.openPopupSlug = state.selectedSlug;
        }
        municipalityLayer.clearLayers();
        markersBySlug = new Map();
        selectedMarker = null;

        const cards = visibleCards();
        const bounds = L.latLngBounds();
        const mode = markerMode();

        // DOM を伴う詳細ピンは、表示範囲(+余白)に入っている自治体だけ生成する。
        const cullBounds = mode === 'detailed' ? map.getBounds().pad(0.3) : null;

        for (const card of cards) {
            const coordinate = coordinateForCard(card);
            const [lat, lon] = coordinate;
            bounds.extend([lat, lon]);
            if (cullBounds && !cullBounds.contains(coordinate)) continue;
            const selected = String(card.slug || '') === state.selectedSlug;
            const marker = mode === 'simple'
                ? simpleMarker(card, coordinate, selected)
                : L.marker([lat, lon], {
                    icon: markerIcon(card, selected),
                    keyboard: true,
                    riseOnHover: true,
                    title: markerTitle(card),
                    zIndexOffset: selected ? 500 : cardReadyCount(card) * 20,
                });
            attachMarkerInteractions(marker, card);
            marker.addTo(municipalityLayer);
            markersBySlug.set(String(card.slug || ''), marker);
            if (selected) selectedMarker = marker;
        }

        if (options.fit !== false && bounds.isValid()) {
            map.fitBounds(bounds, {
                padding: window.innerWidth < 700 ? [24, 24] : [42, 42],
                maxZoom: cards.length <= 2 ? 9 : 7,
            });
        } else if (options.fit !== false) {
            map.setView([36.2048, 138.2529], 5);
        }
        if (state.openPopupSlug !== '') {
            const popupCard = cards.find((card) => String(card.slug || '') === state.openPopupSlug);
            if (!popupCard) {
                // 絞り込みで対象が消えたら予約を捨てる。残すと次の選択のポップアップを潰す。
                state.openPopupSlug = '';
            } else if (markersBySlug.has(state.openPopupSlug)) {
                openMarkerPopup(state.openPopupSlug, popupCard);
            }
        }
        renderedMarkerMode = mode;
    }

    function applyMarkerSelection(prevSlug, nextSlug) {
        for (const slug of new Set([prevSlug, nextSlug])) {
            if (!slug) continue;
            const marker = markersBySlug.get(slug);
            if (!marker) continue;
            const card = allCards().find((item) => String(item.slug || '') === slug);
            if (!card) continue;
            const selected = slug === nextSlug;
            if (typeof marker.setStyle === 'function' && typeof marker.setRadius === 'function') {
                const style = simpleMarkerStyle(card, selected);
                marker.setStyle(style);
                marker.setRadius(style.radius);
            } else {
                marker.setIcon(markerIcon(card, selected));
                marker.setZIndexOffset(selected ? 500 : cardReadyCount(card) * 20);
            }
            if (selected) selectedMarker = marker;
        }
    }

    function renderPopup(card) {
        const key = state.feature;
        const ready = featureReady(card, key);
        const meta = featureMeta[key] || { label: key };
        const coordinateNote = hasKnownCoordinate(card) ? '' : '<div class="popup-count">位置は都道府県内の概略表示です</div>';
        const service = `<span class="popup-service popup-service-${escapeHtml(key)} ${ready ? '' : 'is-pending'}">${escapeHtml(meta.label)}</span>`;
        return `
            <div class="coverage-popup">
                <strong>${escapeHtml(card?.name || '')}</strong>
                <span>${escapeHtml(card?.prefecture_label || '')}</span>
                <div class="popup-services">${service}</div>
                <div class="popup-count">${escapeHtml(ready ? '利用可能' : '準備中')}</div>
                ${coordinateNote}
            </div>
        `.trim();
    }

    function renderAll(options = {}) {
        renderStats();
        renderMap(options);
        renderList();
        writeQueryState();
    }

    function statForFeature(key) {
        const cards = allCards();
        if (key === 'all') {
            return {
                label: '公開自治体',
                ready: cards.filter((card) => cardReadyCount(card) > 0).length,
                total: cards.length,
            };
        }
        const scoped = cards.filter((card) => hasFeature(card, key));
        const summary = Array.isArray(state.payload?.feature_summaries)
            ? state.payload.feature_summaries.find((item) => String(item?.feature_key || '') === key)
            : null;
        return {
            label: featureMeta[key].label,
            ready: scoped.filter((card) => featureReady(card, key)).length,
            total: scoped.length,
            target: Number(summary?.target_count || 0),
        };
    }

    function renderStats() {
        if (!statbar) return;
        const stats = ['all', 'gijiroku', 'reiki', 'boards'].map(statForFeature);
        statbar.innerHTML = stats.map((stat, index) => {
            const ratio = stat.total > 0 ? Math.min(100, Math.round((stat.ready / stat.total) * 100)) : 0;
            const denominator = stat.target && stat.target > stat.total ? stat.target : stat.total;
            return `
                <div class="stat-card">
                    <span class="stat-label">${escapeHtml(stat.label)}</span>
                    <strong>${escapeHtml(`${stat.ready} / ${denominator}`)}</strong>
                    <span class="stat-note">${index === 0 ? '全機能の合算' : `${ratio}% 利用可能`}</span>
                    <span class="stat-bar" aria-hidden="true"><span style="width: ${ratio}%"></span></span>
                </div>
            `.trim();
        }).join('');
    }

    function renderFeatureDot(key, card) {
        const feature = featureByKey(card, key);
        if (!feature) return `<span class="service-dot service-dot-empty">${escapeHtml(featureMeta[key].shortLabel)}</span>`;
        const availabilityState = featureAvailabilityState(feature).replace(/[^a-z_]/g, '');
        const label = `${featureMeta[key].label}: ${String(feature?.status_label || '')}`;
        return `<span class="service-dot service-dot-${key} service-dot-state-${escapeHtml(availabilityState)}" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}">${escapeHtml(featureMeta[key].shortLabel)}</span>`;
    }

    function renderDetail(card) {
        if (!detailPanel) return;
        if (!card) {
            detailPanel.innerHTML = `
                <div class="detail-empty">
                    <h2>自治体を選択</h2>
                    <p>地図上のマーカーを選ぶと、対応している機能と公開先を確認できます。</p>
                </div>
            `.trim();
            return;
        }

        const features = Array.isArray(card.features) ? card.features : [];
        detailPanel.innerHTML = `
            <div class="detail-head">
                <span class="detail-pref">${escapeHtml(card.prefecture_label || '')}</span>
                <h2>${escapeHtml(card.name || '')}</h2>
                <div class="service-dots">
                    ${renderFeatureDot('gijiroku', card)}
                    ${renderFeatureDot('reiki', card)}
                    ${renderFeatureDot('boards', card)}
                </div>
            </div>
            <div class="detail-features">
                ${features.map(renderFeatureDetail).join('')}
            </div>
            <div class="detail-actions">
                <a href="/search/?slug=${encodeURIComponent(card.slug || '')}">この自治体を検索</a>
            </div>
        `.trim();
    }

    function renderFeatureDetail(feature) {
        const key = String(feature?.feature_key || '');
        const meta = featureMeta[key] || { label: feature?.label || key, color: '#64748b' };
        const detail = String(feature?.display?.detail || '').trim();
        const systemType = String(feature?.system_type || '').trim();
        const coverage = key === 'gijiroku'
            ? featureSearchCoverage({ features: [feature] }, key)
            : null;
        const hasSearchableMinutes = key === 'gijiroku' && String(feature?.mode || '') === 'link';
        const action = String(feature?.mode || '') === 'link' && String(feature?.url || '') !== ''
            ? `<a class="feature-open" href="${escapeHtml(feature.url)}">開く</a>`
            : '<span class="feature-open feature-open-disabled">待機</span>';
        return `
            <div class="feature-detail feature-detail-${escapeHtml(key)}">
                <div class="feature-detail-top">
                    <span class="feature-name"><i style="background:${escapeHtml(meta.color)}"></i>${escapeHtml(meta.label)}</span>
                    <span class="status ${escapeHtml(feature?.status_class || '')}">${escapeHtml(feature?.status_label || '')}</span>
                    ${action}
                </div>
                ${systemType !== '' ? `<p class="feature-system-type">取得形式: ${escapeHtml(systemType)}</p>` : ''}
                ${hasSearchableMinutes ? `<p class="feature-search-coverage"><strong>検索できる会議日</strong><span>${coverage ? escapeHtml(searchCoverageText(coverage)) : '日付情報がないため範囲を表示できません'}</span></p>` : ''}
                ${detail !== '' ? `<p>${escapeHtml(detail).replace(/\n/g, '<br>')}</p>` : ''}
            </div>
        `.trim();
    }

    function renderList() {
        if (!resultList) return;
        const cards = visibleCards();
        if (displayCountElement) {
            const total = Number(state.payload?.display_municipality_count || allCards().length || 0);
            displayCountElement.textContent = `表示自治体: ${cards.length} / ${total}`;
        }
        if (filterHint) {
            const featureLabel = featureMeta[state.feature]?.label || state.feature;
            const prefLabel = state.prefecture === 'all' ? '全国' : (prefectureByCode.get(state.prefecture)?.name || state.prefecture);
            filterHint.textContent = `${prefLabel} / ${featureLabel} / ${cards.length}自治体`;
        }
        if (cards.length === 0) {
            resultList.innerHTML = '<div class="loading-panel">条件に合う自治体はありません。</div>';
            renderDetail(null);
            return;
        }

        const groups = groupByPrefecture(cards);
        const sections = Array.from(groups.entries()).sort(([a], [b]) => a.localeCompare(b)).map(([code, prefCards]) => {
            const prefecture = prefectureByCode.get(code);
            return `
                <section class="pref-result">
                    <h3>${escapeHtml(prefecture?.name || prefCards[0]?.prefecture_label || 'その他')}</h3>
                    <div class="pref-result-grid">
                        ${prefCards.map(renderMunicipalityRow).join('')}
                    </div>
                </section>
            `.trim();
        });
        resultList.innerHTML = sections.join('');
        updateListSelection();
    }

    function renderMunicipalityRow(card) {
        const slug = String(card.slug || '');
        const coverage = state.feature === 'gijiroku' ? featureSearchCoverage(card) : null;
        const hasSearchableMinutes = state.feature === 'gijiroku' && featureReady(card, 'gijiroku');
        return `
            <div class="municipality-index-card${slug === state.selectedSlug ? ' is-selected' : ''}" data-index-slug="${escapeHtml(slug)}">
                <button class="municipality-row${slug === state.selectedSlug ? ' is-selected' : ''}" type="button" data-slug="${escapeHtml(slug)}" aria-expanded="${slug === state.selectedSlug ? 'true' : 'false'}">
                    <span class="municipality-row-copy">
                        <span class="municipality-row-name">${escapeHtml(card.name || '')}</span>
                    </span>
                    <span class="service-dots">
                        ${renderFeatureDot('gijiroku', card)}
                        ${renderFeatureDot('reiki', card)}
                        ${renderFeatureDot('boards', card)}
                    </span>
                    ${hasSearchableMinutes ? `<span class="municipality-row-range${coverage ? '' : ' is-unknown'}">会議日 ${coverage ? escapeHtml(searchCoverageText(coverage, { includeCount: false, compact: true })) : '日付情報なし'}</span>` : ''}
                </button>
            </div>
        `.trim();
    }

    function renderMunicipalityInlineDetail(card) {
        const features = Array.isArray(card?.features) ? card.features : [];
        const coverage = featureSearchCoverage(card);
        const hasSearchableMinutes = featureReady(card, 'gijiroku');
        return `
            <div class="municipality-inline-detail" data-home-inline-detail>
                <div class="municipality-inline-statuses">
                    ${features.map((feature) => `<span>${escapeHtml(feature?.label || '')}: <strong>${escapeHtml(feature?.status_label || '')}</strong></span>`).join('')}
                </div>
                ${hasSearchableMinutes ? `<p><strong>検索できる会議日</strong>${coverage ? escapeHtml(searchCoverageText(coverage)) : '日付情報がないため範囲を表示できません'}</p>` : ''}
                <a href="/search/?slug=${encodeURIComponent(card?.slug || '')}">${escapeHtml(card?.name || '')}の記録を検索 <span aria-hidden="true">→</span></a>
            </div>
        `.trim();
    }

    function renderFeatureSummaries(featureSummaries) {
        const summaries = Array.isArray(featureSummaries) ? featureSummaries.filter((item) => item) : [];
        return summaries.map((item) => `<span>${escapeHtml(item.text || '')}</span>`).join('');
    }

    function renderTaskDisplay(display) {
        if (!display || typeof display !== 'object') return '';
        const current = Number(display.progress_current ?? display.count_current ?? NaN);
        const total = Number(display.progress_total ?? display.count_total ?? NaN);
        const hasProgress = Number.isFinite(current) && Number.isFinite(total) && total > 0;
        const width = hasProgress ? Math.max(0, Math.min(100, (current / total) * 100)) : 0;
        return `
            <div class="task-line">
                <span class="task-badge ${escapeHtml(display.class || '')}">${escapeHtml(display.label || '')}</span>
                ${hasProgress ? `<span class="task-mini-bar"><span style="width:${width.toFixed(2)}%"></span></span>` : ''}
            </div>
        `.trim();
    }

    function renderProcessingStatus(payload) {
        const summaries = Array.isArray(payload?.task_state_summaries) ? payload.task_state_summaries : [];
        const runningTasks = Array.isArray(payload?.running_tasks) ? payload.running_tasks : [];
        if (summaries.length === 0 && runningTasks.length === 0 && state.latestProcessingStatusPayload) return;
        if (summaries.length > 0 || runningTasks.length > 0) state.latestProcessingStatusPayload = payload;
        if (!runningSection || !runningSummaryList) return;
        runningSection.hidden = summaries.length === 0 && runningTasks.length === 0;
        runningSummaryList.innerHTML = summaries.map((summary) => {
            const stats = Array.isArray(summary?.stats) ? summary.stats : [];
            return `
                <article class="operation-card">
                    <div class="operation-card-top">
                        <strong>${escapeHtml(summary?.label || '')}</strong>
                        <span class="${escapeHtml(summary?.state_class || '')}">${escapeHtml(summary?.state_label || '')}</span>
                    </div>
                    <div class="operation-stats">
                        ${stats.map((stat) => `<span><b>${escapeHtml(stat.label || '')}</b>${escapeHtml(stat.value || '')}</span>`).join('')}
                    </div>
                </article>
            `.trim();
        }).join('');
        if (runningList) {
            runningList.hidden = runningTasks.length === 0;
            runningList.innerHTML = runningTasks.map((task) => `
                <article class="running-item">
                    <strong>${escapeHtml(task?.municipality_name || '')}</strong>
                    ${renderTaskDisplay(task?.display)}
                </article>
            `.trim()).join('');
        }
    }

    function syncControls() {
        state.feature = normalizeFeature(state.feature);
        state.issue = normalizeIssue(state.issue);
        state.prefecture = normalizePrefecture(state.prefecture);
        featureButtons.forEach((button) => {
            button.classList.toggle('is-active', String(button.dataset.featureFilter || '') === state.feature);
        });
        if (issueFilterSelect) issueFilterSelect.value = state.issue;
        if (searchInput) searchInput.value = state.query;
        if (prefectureSelect) {
            const current = prefectureSelect.value || 'all';
            prefectureSelect.innerHTML = [
                '<option value="all">全国</option>',
                ...prefectures.map((prefecture) => `<option value="${prefecture.code}">${escapeHtml(prefecture.name)}</option>`),
            ].join('');
            prefectureSelect.value = prefectures.some((prefecture) => prefecture.code === state.prefecture)
                ? state.prefecture
                : current;
        }
    }

    // 選択の切り替えでは地図と一覧を作り直さず、対象マーカーと行のスタイルだけ更新する。
    function selectCard(slug, options = {}) {
        const card = allCards().find((item) => String(item.slug || '') === String(slug || '')) || null;
        const prevSlug = state.selectedSlug;
        state.selectedSlug = card ? String(card.slug || '') : '';
        renderDetail(card);
        updateListSelection();
        applyMarkerSelection(prevSlug, state.selectedSlug);
        if (card && map && options.pan !== false) {
            const coordinate = coordinateForCard(card);
            map.setView(coordinate, Math.max(map.getZoom(), 9), { animate: true });
        }
        if (card && options.openPopup !== false) {
            openMarkerPopup(state.selectedSlug, card);
        }
    }

    function updateListSelection() {
        if (!resultList) return;
        resultList.querySelectorAll('[data-home-inline-detail]').forEach((detail) => detail.remove());
        resultList.querySelectorAll('.municipality-index-card.is-selected').forEach((card) => {
            card.classList.remove('is-selected');
        });
        resultList.querySelectorAll('.municipality-row.is-selected').forEach((row) => {
            row.classList.remove('is-selected');
            row.setAttribute('aria-expanded', 'false');
        });
        if (state.selectedSlug === '') return;
        const rows = resultList.querySelectorAll('.municipality-row[data-slug]');
        for (const row of rows) {
            if (row.getAttribute('data-slug') === state.selectedSlug) {
                row.classList.add('is-selected');
                row.setAttribute('aria-expanded', 'true');
                const container = row.closest('.municipality-index-card');
                const card = allCards().find((item) => String(item.slug || '') === state.selectedSlug) || null;
                if (container && card) {
                    container.classList.add('is-selected');
                    container.insertAdjacentHTML('beforeend', renderMunicipalityInlineDetail(card));
                }
                break;
            }
        }
    }

    // 自動で先頭自治体を選ばない。ユーザーが選ぶまでは案内を表示し、
    // 絞り込みで選択中の自治体が消えた場合は選択を解除する。
    function ensureSelection() {
        const cards = visibleCards();
        if (!cards.some((card) => String(card.slug || '') === state.selectedSlug)) {
            state.selectedSlug = '';
            renderDetail(null);
            return;
        }
        renderDetail(cards.find((card) => String(card.slug || '') === state.selectedSlug));
    }

    function renderPayload(payload) {
        state.payload = payload;
        if (loadingPanel) loadingPanel.remove();
        if (municipalityCountElement) municipalityCountElement.textContent = `自治体マスタ: ${Number(payload?.municipality_count || 0)}`;
        if (generatedAtElement) generatedAtElement.textContent = `更新: ${String(payload?.generated_at || '不明')}`;
        if (taskSummariesElement) taskSummariesElement.innerHTML = renderFeatureSummaries(payload?.feature_summaries);
        renderProcessingStatus(payload);
        syncControls();
        ensureSelection();
        // 視点の自動リセットは初回描画だけ。定期更新では現在の表示位置を保つ。
        renderAll({ fit: !hasFitOnce });
        hasFitOnce = true;
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
        if (!response.ok) throw new Error(String(payload?.error || `HTTP ${response.status}`));
        return { payload, raw: responseText };
    }

    async function loadTaskStatus() {
        const headers = {};
        if (state.latestTaskStatusEtag !== '') headers['If-None-Match'] = state.latestTaskStatusEtag;
        const response = await fetch(taskStatusApiUrl, { cache: 'no-store', headers });
        if (response.status === 304) return null;
        const responseText = await response.text();
        let payload;
        try {
            payload = JSON.parse(responseText);
        } catch (error) {
            throw new Error(`Invalid JSON from task status API (HTTP ${response.status})`);
        }
        if (!response.ok) throw new Error(String(payload?.error || `HTTP ${response.status}`));
        state.latestTaskStatusEtag = String(response.headers.get('ETag') || '');
        return payload;
    }

    async function refresh() {
        try {
            const { payload, raw } = await loadPayload();
            if (raw === lastPayloadRaw) return;
            lastPayloadRaw = raw;
            renderPayload(payload);
        } catch (error) {
            console.error('homepage refresh failed', error);
            if (loadingPanel && loadingPanel.isConnected) loadingPanel.textContent = '自治体データの読み込みに失敗しました。';
        }
    }

    async function refreshTaskStatus() {
        try {
            const payload = await loadTaskStatus();
            if (payload && typeof payload === 'object') renderProcessingStatus(payload);
        } catch (error) {
            console.error('task status refresh failed', error);
        }
    }

    featureButtons.forEach((button) => {
        button.addEventListener('click', () => {
            state.feature = normalizeFeature(String(button.dataset.featureFilter || 'all'));
            syncControls();
            ensureSelection();
            renderAll();
        });
    });

    if (prefectureSelect) {
        prefectureSelect.addEventListener('change', () => {
            state.prefecture = String(prefectureSelect.value || 'all');
            syncControls();
            ensureSelection();
            renderAll();
        });
    }

    if (issueFilterSelect) {
        issueFilterSelect.addEventListener('change', () => {
            state.issue = normalizeIssue(String(issueFilterSelect.value || 'all'));
            syncControls();
            ensureSelection();
            renderAll();
        });
    }

    if (searchInput) {
        searchInput.addEventListener('input', () => {
            state.query = String(searchInput.value || '').trim();
            window.clearTimeout(searchDebounceTimer);
            searchDebounceTimer = window.setTimeout(() => {
                ensureSelection();
                renderAll();
            }, 220);
        });
    }

    if (resultList) {
        resultList.addEventListener('click', (event) => {
            const row = event.target.closest?.('[data-slug]');
            if (!row) return;
            selectCard(row.getAttribute('data-slug'), { pan: false, openPopup: false });
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        initMap();
        refresh().finally(() => refreshTaskStatus());
        window.setInterval(refreshTaskStatus, 3000);
        window.setInterval(refresh, 60000);
    });
})();
