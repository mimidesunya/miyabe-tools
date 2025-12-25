import * as Api from './api.js';
import * as Ui from './ui.js';
import * as MapUtils from './map.js';

// 以下の実装は元の app/assets/js/boards/main.js と同一
// 必要に応じて将来この場所で保守する

// Globals
let map;
let markers = [];
let bounds = L.latLngBounds();
let allRows = [];
let authState = { loggedIn: false, user: null };
let adjustMode = false;
const codeToMarker = new Map();
let slug = ''; // parsed from URL path /boards/[slug]/

// Error handling
(function () {
	function showErr(msg) {
		try {
			let box = document.getElementById('js-error');
			if (!box) {
				box = document.createElement('div');
				box.id = 'js-error';
				box.style.position = 'fixed';
				box.style.left = '8px';
				box.style.bottom = '8px';
				box.style.zIndex = '5000';
				box.style.maxWidth = '90vw';
				box.style.background = 'rgba(255,0,0,0.9)';
				box.style.color = '#fff';
				box.style.padding = '8px 10px';
				box.style.borderRadius = '6px';
				box.style.boxShadow = '0 2px 8px rgba(0,0,0,0.3)';
				box.style.fontSize = '12px';
				document.body.appendChild(box);
			}
			const p = document.createElement('div');
			p.textContent = String(msg);
			box.appendChild(p);
		} catch (_) { /* ignore */ }
	}
	window.addEventListener('error', function (e) {
		const where = (e && (e.filename || '')) + (e && e.lineno ? (':' + e.lineno) : '');
		showErr('エラー: ' + (e && e.message ? e.message : 'unknown') + (where ? ' @ ' + where : ''));
	});
	window.addEventListener('unhandledrejection', function (e) {
		const r = e && e.reason;
		const msg = r && (r.message || r.toString()) || 'unknown rejection';
		showErr('Promiseエラー: ' + msg);
	});
})();

// URL helpers
function getUrlParams() {
	const params = new URLSearchParams(window.location.search);
	return {
		lat: params.get('lat'),
		lng: params.get('lng'),
		zoom: params.get('zoom')
	};
}

function updateUrl(lat, lng, zoom) {
	const url = new URL(window.location);
	url.searchParams.set('lat', Number(lat).toFixed(6));
	url.searchParams.set('lng', Number(lng).toFixed(6));
	url.searchParams.set('zoom', String(zoom));
	window.history.replaceState({}, '', url);
}

// Data fetching
let fetchDebounce = null;
let isFetching = false;

function scheduleDataFetch() {
	if (fetchDebounce) clearTimeout(fetchDebounce);
	fetchDebounce = setTimeout(fetchAndRenderVisible, 250);
}

async function fetchAndRenderVisible() {
	if (isFetching || adjustMode) return;
	isFetching = true;
	const b = map.getBounds();
	const sw = b.getSouthWest();
	const ne = b.getNorthEast();
	const params = new URLSearchParams({
		min_lat: String(sw.lat),
		max_lat: String(ne.lat),
		min_lon: String(sw.lng),
		max_lon: String(ne.lng),
		limit: '10000'
	});
	try {
		const json = await Api.fetchQuery(slug, params);
		const rows = [];
		for (const item of json) {
			const code = (item.code || '').trim();
			const address = (item.address || '').trim();
			const desc = (item.place || '').trim();
			const lat = (item.lat == null ? NaN : Number(item.lat));
			const lng = (item.lon == null ? NaN : Number(item.lon));
			const status = (item.status || 'pending');
			const updatedBy = item.updated_by_line_id || null;
			const hasComment = !!item.has_comment;
			if (address) rows.push({ code, address, desc, lat, lng, status, updatedBy, hasComment });
		}
		allRows = rows;
		loadAll(true);
		updateCounts();
	} catch (err) {
		console.error('bbox fetch failed', err);
	} finally {
		isFetching = false;
	}
}

async function updateCounts() {
	try {
		const j = await Api.fetchStats(slug);
		const totals = j && j.totals ? j.totals : { in_progress: 0, done: 0, issue: 0 };
		const mine = j && j.mine ? j.mine : { in_progress: 0, done: 0, issue: 0 };
		Ui.renderCounts(totals, mine);
	} catch (_) { /* ignore */ }
}

// Load markers
function clearMarkers() {
	markers.forEach(m => map.removeLayer(m));
	markers = [];
	bounds = L.latLngBounds();
	codeToMarker.clear();
}

function loadAll(skipFitBounds = false) {
	clearMarkers();
	for (const r of allRows) {
		if (!r.lat || !r.lng) continue;
		const baseHtml = `<div>
		  <div style="margin-bottom:6px"><b>${Ui.escapeHtml(r.desc)}</b><br>${Ui.escapeHtml(r.address)}</div>
		  <div style="margin:6px 0 8px; font-size:13px;"><a href="https://www.google.com/maps/search/?api=1&query=${r.lat},${r.lng}" target="_blank" rel="noopener" style="color:#1a73e8; text-decoration:none; font-weight:600;">📍 Googleマップで開く</a></div>
		  <div class="task-panel" data-code="${Ui.escapeHtml(r.code)}">
			<div class="task-status" style="${authState.loggedIn ? 'display:none;' : ''}">ステータス: <span class="task-status-text">読み込み中...</span></div>
			<div class="task-select" style="margin-top:6px; ${authState.loggedIn ? '' : 'display:none;'}">
			  <label>ステータス:
				<select class="status-select">
				  <option value="pending">未着手</option>
				  <option value="in_progress">⏳ 着手</option>
				  <option value="done">✅ 掲示</option>
				  <option value="issue">⚠️ 異常</option>
				</select>
			  </label>
			</div>
			<div class="task-comment" style="margin-top:6px; ${authState.loggedIn ? '' : 'display:none;'}">
			  <input type="text" placeholder="コメント" style="width: 180px;" />
			</div>
			<div class="task-last" style="margin-top:6px; font-size: 12px; color: #555;"></div>
		  </div>
		</div>`;
		const codeIcon = L.divIcon({
			className: 'code-marker',
			html: `<div class="label"><span class="status-emoji" aria-hidden="true"></span><span class="code-text">${Ui.escapeHtml(r.code)}</span></div>`,
			iconSize: null,
			iconAnchor: [0, 0]
		});
		const maxPopupWidth = Math.min(460, Math.max(320, window.innerWidth - 32));
		const marker = L.marker([r.lat, r.lng], { icon: codeIcon })
			.addTo(map)
			.bindPopup(baseHtml, {
				maxWidth: maxPopupWidth,
				minWidth: Math.min(320, maxPopupWidth),
				autoPanPaddingTopLeft: [20, 12],
				autoPanPaddingBottomRight: [20, 12]
			});

		// 位置調整モード中はポップアップを表示しない
		marker.on('click', (e) => {
			if (adjustMode) {
				if (e.originalEvent) L.DomEvent.stop(e.originalEvent);
			}
		});
        
		setTimeout(() => {
			const isSelfInit = !!(authState && authState.loggedIn && r.updatedBy && authState.user && authState.user.id === r.updatedBy);
			const hasCommentInit = !!r.hasComment;
			Ui.updateMarkerStatus(r.code, r.status || 'pending', isSelfInit, hasCommentInit, codeToMarker);
			MapUtils.setupDragForMarker(marker, r.code, map, authState, adjustMode, slug);
		}, 0);

		marker.on('popupopen', async (e) => {
			if (adjustMode) {
				map.closePopup();
				return;
			}
			const panel = e.popup.getElement().querySelector('.task-panel');
			if (!panel) return;
			const code = panel.getAttribute('data-code');
			const statusWrap = panel.querySelector('.task-status');
			const statusEl = panel.querySelector('.task-status-text');
			const lastEl = panel.querySelector('.task-last');
			const selectWrap = panel.querySelector('.task-select');
			const commentEl = panel.querySelector('.task-comment');
            
			if (statusWrap) statusWrap.style.display = authState.loggedIn ? 'none' : '';
			if (selectWrap) selectWrap.style.display = authState.loggedIn ? '' : 'none';
			if (commentEl) commentEl.style.display = authState.loggedIn ? '' : 'none';
			if (lastEl) lastEl.style.display = authState.loggedIn ? 'none' : '';
			if (statusEl) statusEl.textContent = '読み込み中...';
            
			const info = await Api.fetchTaskStatus(slug, code);
			if (!info || !info.status) { if (statusEl) statusEl.textContent = '取得失敗'; return; }
            
			{
				const em = Ui.statusEmoji(info.status.status);
				if (statusEl) statusEl.textContent = em ? `${em} ${Ui.statusLabel(info.status.status)}` : Ui.statusLabel(info.status.status);
			}
            
			if (authState && authState.loggedIn && !adjustMode && selectWrap) {
				const sel = selectWrap.querySelector('.status-select');
				if (sel) sel.value = info.status.status || 'pending';
			}
            
			if (lastEl) {
				if (authState && authState.loggedIn && !adjustMode) {
					lastEl.textContent = '';
				} else {
					lastEl.textContent = info.status.last_comment ? `「${Ui.escapeHtml(info.status.last_comment)}」` : '';
				}
			}
            
			const isSelf = !!(authState && authState.loggedIn && info.status.updated_by_line_id && authState.user && authState.user.id === info.status.updated_by_line_id);
			const hasCom = !!(info.status.last_comment && info.status.last_comment.trim() !== '');
			Ui.updateMarkerStatus(code, info.status.status, isSelf, hasCom, codeToMarker);
			Ui.updatePopupStatusStyle(e.popup, info.status.status);
            
			if (authState && authState.loggedIn && !adjustMode && commentEl) {
				const input = commentEl.querySelector('input');
				if (input) input.value = info.status.last_comment || '';
			}
            
			if (selectWrap) {
				const sel = selectWrap.querySelector('.status-select');
				if (sel) {
					sel.onchange = async () => {
						const act = sel.value || 'pending';
						const input = commentEl ? commentEl.querySelector('input') : null;
						const note = input ? (input.value || '').trim() : '';
                        
						try {
							const updated = await Api.setStatus(slug, code, act, note);
							if (updated && updated.status) {
								const em2 = Ui.statusEmoji(updated.status.status);
								if (statusEl) statusEl.textContent = em2 ? `${em2} ${Ui.statusLabel(updated.status.status)}` : Ui.statusLabel(updated.status.status);
								if (lastEl) {
									if (authState && authState.loggedIn) {
										lastEl.textContent = '';
									} else {
										lastEl.textContent = updated.status.last_comment ? `「${Ui.escapeHtml(updated.status.last_comment)}」` : '';
									}
								}
								const isSelf2 = !!(authState && authState.loggedIn && updated.status.updated_by_line_id && authState.user && authState.user.id === updated.status.updated_by_line_id);
								const hasCom2 = !!(updated.status.last_comment && updated.status.last_comment.trim() !== '');
								Ui.updateMarkerStatus(code, updated.status.status, isSelf2, hasCom2, codeToMarker);
								Ui.updatePopupStatusStyle(e.popup, updated.status.status);
								sel.value = updated.status.status || 'pending';
								if (authState && authState.loggedIn && commentEl) {
									const input2 = commentEl.querySelector('input');
									if (input2) input2.value = updated.status.last_comment || '';
								}
								const i = allRows.findIndex(r => r.code === code);
								if (i >= 0) {
									allRows[i].status = updated.status.status || 'pending';
									allRows[i].updatedBy = updated.status.updated_by_line_id || allRows[i].updatedBy || null;
									allRows[i].hasComment = !!(updated.status.last_comment && updated.status.last_comment.trim() !== '');
								}
								updateCounts();
							}
						} catch (e) {
							alert('更新に失敗しました');
						}
					};
				}
			}
            
			if (commentEl) {
				const input = commentEl.querySelector('input');
				if (input) {
					let debounceId = null;
					let lastSent = (info.status.last_comment || '').trim();
					const save = async () => {
						const note = (input.value || '').trim();
						if (!authState || !authState.loggedIn) return;
						if (note === lastSent) return;
                        
						try {
							const updated = await Api.postComment(slug, code, note);
							if (updated && updated.status) {
								lastSent = (updated.status.last_comment || '').trim();
								if (lastEl) {
									if (authState && authState.loggedIn) {
										lastEl.textContent = '';
									} else {
										lastEl.textContent = updated.status.last_comment ? `「${Ui.escapeHtml(updated.status.last_comment)}」` : '';
									}
								}
								const isSelf3 = !!(authState && authState.loggedIn && updated.status.updated_by_line_id && authState.user && authState.user.id === updated.status.updated_by_line_id);
								const hasCom3 = !!(updated.status.last_comment && updated.status.last_comment.trim() !== '');
								Ui.updateMarkerStatus(code, updated.status.status || 'pending', isSelf3, hasCom3, codeToMarker);
								const j = allRows.findIndex(r => r.code === code);
								if (j >= 0) {
									allRows[j].updatedBy = updated.status.updated_by_line_id || allRows[j].updatedBy || null;
									allRows[j].hasComment = hasCom3;
								}
								updateCounts();
							}
						} catch (e) {
							console.warn('コメント保存に失敗');
						}
					};
					input.addEventListener('input', () => {
						if (!authState || !authState.loggedIn) return;
						if (debounceId) clearTimeout(debounceId);
						debounceId = setTimeout(save, 600);
					});
					input.addEventListener('blur', () => {
						if (!authState || !authState.loggedIn) return;
						if (debounceId) { clearTimeout(debounceId); debounceId = null; }
						save();
					});
					input.addEventListener('keydown', (ev) => {
						if (ev.key === 'Enter') {
							ev.preventDefault();
							ev.stopPropagation();
							if (debounceId) { clearTimeout(debounceId); debounceId = null; }
							save();
						}
					});
				}
			}
		});
		markers.push(marker);
		bounds.extend([r.lat, r.lng]);
		if (r.code) codeToMarker.set(r.code, marker);
	}
	if (!bounds.isValid()) {
	} else if (!skipFitBounds) {
		map.fitBounds(bounds, { padding: [30, 30] });
	}
	const center = map.getCenter();
	const zoom = map.getZoom();
	updateUrl(center.lat, center.lng, zoom);
	updateCounts();
}

// Search
function normalize(s) {
	return (s || '').toString().trim().toLowerCase();
}

function runSearch() {
	const input = document.getElementById('search-input');
	if (!input) return;
	const q = normalize(input.value);
	if (!q) return;
	const exact = allRows.find(r => normalize(r.code) === q);
	if (exact && Number.isFinite(exact.lat) && Number.isFinite(exact.lng)) {
		const m = codeToMarker.get(exact.code);
		if (m) {
			map.setView([exact.lat, exact.lng], Math.max(map.getZoom(), 16));
			setTimeout(() => m.openPopup(), 150);
			return;
		}
	}
	const results = allRows.filter(r => {
		const code = normalize(r.code);
		const addr = normalize(r.address);
		const desc = normalize(r.desc);
		return code.includes(q) || addr.includes(q) || desc.includes(q);
	}).filter(r => Number.isFinite(r.lat) && Number.isFinite(r.lng));
	if (results.length === 0) {
		alert('該当がありません');
		return;
	}
	if (results.length === 1) {
		const r = results[0];
		const m = codeToMarker.get(r.code);
		map.setView([r.lat, r.lng], Math.max(map.getZoom(), 16));
		if (m) setTimeout(() => m.openPopup(), 150);
		return;
	}
	const b = L.latLngBounds();
	results.forEach(r => b.extend([r.lat, r.lng]));
	map.fitBounds(b, { padding: [30, 30] });
}

// Initialization
document.addEventListener('DOMContentLoaded', async () => {
	// Parse slug
	const m = window.location.pathname.match(/^\/boards\/([a-z0-9_-]+)\/?/);
	if (m && m[1]) {
		slug = m[1];
	} else {
		console.warn('No slug found, defaulting to', slug);
	}

	map = MapUtils.initMap();
	MapUtils.setupGps(map);

	// Help modal open/close wiring
	(function setupHelp() {
		const helpBtn = document.getElementById('help-btn');
		const helpModal = document.getElementById('help-modal');
		const helpClose = document.getElementById('help-close');
		if (!helpBtn || !helpModal || !helpClose) return;

		helpBtn.addEventListener('click', () => {
			helpModal.style.display = 'flex';
		});

		helpClose.addEventListener('click', () => {
			helpModal.style.display = 'none';
		});

		helpModal.addEventListener('click', (e) => {
			if (e.target === helpModal) {
				helpModal.style.display = 'none';
			}
		});
	})();
    
	// Wire search
	const btn = document.getElementById('search-btn');
	const input = document.getElementById('search-input');
	if (btn) btn.addEventListener('click', runSearch);
	if (input) input.addEventListener('keydown', (e) => {
		if (e.key === 'Enter') {
			e.preventDefault();
			runSearch();
		}
	});

	// Wire offset toggle
	const offsetToggleEl = document.getElementById('offset-toggle');
	if (offsetToggleEl) {
		offsetToggleEl.addEventListener('click', () => {
			if (!authState || !authState.loggedIn) {
				alert('ログインすると位置調整が可能です');
				return;
			}
			adjustMode = !adjustMode;
			map.closePopup();
			offsetToggleEl.textContent = adjustMode ? '位置調整: ON' : '位置調整: OFF';
			offsetToggleEl.classList.toggle('active', adjustMode);
			codeToMarker.forEach((marker, code) => MapUtils.setupDragForMarker(marker, code, map, authState, adjustMode, slug));
		});
	}

	// Listen for marker moves
	document.addEventListener('marker-moved', (e) => {
		const { code, lat, lng } = e.detail;
		const row = allRows.find(r => r.code === code);
		if (row) {
			row.lat = lat;
			row.lng = lng;
		}
	});

	// Map events
	let _urlUpdateTimer = null;
	function scheduleUrlUpdate() {
		if (_urlUpdateTimer) clearTimeout(_urlUpdateTimer);
		_urlUpdateTimer = setTimeout(() => {
			const c = map.getCenter();
			const z = map.getZoom();
			updateUrl(c.lat, c.lng, z);
		}, 200);
	}
	map.on('moveend', () => { scheduleUrlUpdate(); scheduleDataFetch(); });
	map.on('zoomend', () => { scheduleUrlUpdate(); scheduleDataFetch(); });
    
	let isAnyPopupOpen = false;
	map.on('popupopen', () => { isAnyPopupOpen = true; });
	map.on('popupclose', () => { isAnyPopupOpen = false; });

	// Auto refresh
	setInterval(() => {
		if (!isAnyPopupOpen && !adjustMode) {
			fetchAndRenderVisible();
		}
		updateCounts();
	}, 30000);

	// Auth and Initial Load
	try {
		const s = await Api.checkAuth(slug);
		authState = s || { loggedIn: false, user: null };
		Ui.updateAuthUi(authState, adjustMode);

		// Show help if not logged in
		if (!authState.loggedIn) {
			const helpModal = document.getElementById('help-modal');
			if (helpModal) helpModal.style.display = 'flex';
		}
		
		// 位置調整ボタンの表示制御
		const offsetToggle = document.getElementById('offset-toggle');
		const helpOffsetSection = document.getElementById('help-offset-section');
		if (s && s.allowOffset) {
			if (helpOffsetSection) helpOffsetSection.style.display = 'block';
			if (offsetToggle) offsetToggle.style.display = s.loggedIn ? '' : 'none';
		} else {
			if (helpOffsetSection) helpOffsetSection.style.display = 'none';
			if (offsetToggle) offsetToggle.style.display = 'none';
		}
        
		// Add list view link to controls
		const controlsDiv = document.getElementById('controls');
		if (controlsDiv && !document.getElementById('list-view-link')) {
			const listLink = document.createElement('a');
			listLink.id = 'list-view-link';
			listLink.href = `/boards/list.php?slug=${encodeURIComponent(slug)}`;
			listLink.target = '_blank';
			listLink.rel = 'noopener noreferrer';
			listLink.textContent = '📋 一覧表示';
			listLink.style.cssText = 'display:inline-block; padding:6px 12px; background:#34a853; color:#fff; text-decoration:none; border-radius:6px; font-size:14px; margin-left:8px;';
			const authDiv = document.getElementById('auth');
			if (authDiv && authDiv.parentElement) {
				authDiv.parentElement.appendChild(listLink);
			}
		}
	} catch (_) { /* ignore */ }
    
	// Initial fetch
	const urlParams = getUrlParams();
	if (urlParams.lat && urlParams.lng && urlParams.zoom) {
		map.setView([parseFloat(urlParams.lat), parseFloat(urlParams.lng)], parseInt(urlParams.zoom));
		await fetchAndRenderVisible();
		await updateCounts();
	} else {
		try {
			const res = await Api.fetchQuery(slug, new URLSearchParams({ limit: '50000' }));
			const json = res;
			const rows = [];
			for (const item of json) {
				const code = (item.code || '').trim();
				const address = (item.address || '').trim();
				const desc = (item.place || '').trim();
				const lat = (item.lat == null ? NaN : Number(item.lat));
				const lng = (item.lon == null ? NaN : Number(item.lon));
				const status = (item.status || 'pending');
				const updatedBy = item.updated_by_line_id || null;
				const hasComment = !!item.has_comment;
				if (address) rows.push({ code, address, desc, lat, lng, status, updatedBy, hasComment });
			}
			allRows = rows;
			loadAll(false);
			await updateCounts();
			await fetchAndRenderVisible();
		} catch (err) {
			alert('データの読込に失敗しました: ' + err.message);
		}
	}
});

