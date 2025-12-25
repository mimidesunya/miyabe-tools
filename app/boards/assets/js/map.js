import * as Api from './api.js';

export function initMap() {
	const map = L.map('map').setView([35.5, 139.4], 11);
	const osmLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
	  attribution: '© OpenStreetMap contributors'
	}).addTo(map);
	const esriSat = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
	  attribution: 'Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community'
	});
	const baseLayers = { '標準地図 (OSM)': osmLayer, '航空写真 (Esri)': esriSat };
	L.control.layers(baseLayers, null, { position: 'topleft' }).addTo(map);
    
	// Basemap switch logic
	const baseRefs = { mapBase: osmLayer, satBase: esriSat };
	const container = document.getElementById('basemap-switch');
	if (container) {
		container.addEventListener('click', (e) => {
			const btn = e.target.closest('button[data-base]');
			if (!btn) return;
			const key = btn.getAttribute('data-base');
			if (!key) return;
			[osmLayer, esriSat].forEach(l => { if (l && map.hasLayer(l)) map.removeLayer(l); });
			const target = baseRefs[key];
			if (target) target.addTo(map);
			const btns = container.querySelectorAll('button[data-base]');
			btns.forEach(b => b.classList.toggle('active', b.getAttribute('data-base') === key));
		});
	}
	return map;
}

export function setupGps(map) {
	let gpsActive = false;
	let gpsMarker = null;
	let gpsAccCircle = null;
	let gpsWatchId = null;
	let lastPannedTo = null;
	let lastPanAt = 0;
	const THROTTLE_MS = 1000;
	const MIN_MOVE_M = 8;
	const gpsBtn = document.getElementById('gps-btn');
	if (!gpsBtn) return;

	gpsBtn.addEventListener('click', function () {
		gpsActive = !gpsActive;
		gpsBtn.textContent = gpsActive ? 'GPS: ON' : 'GPS: OFF';
		gpsBtn.classList.toggle('active', gpsActive);
		if (gpsActive) {
			if (location.protocol !== 'https:' && !/^localhost$|^127\.0\.0\.1$/.test(location.hostname)) {
				alert('位置情報はHTTPS接続でのみ動作する場合があります。');
			}
			if ('geolocation' in navigator) {
				gpsWatchId = navigator.geolocation.watchPosition(
					pos => {
						const { latitude, longitude, accuracy } = pos.coords;
						const latlng = [latitude, longitude];
						if (gpsMarker) {
							gpsMarker.setLatLng(latlng);
						} else {
							const icon = L.divIcon({ className: 'gps-marker', iconSize: [18, 18], iconAnchor: [9, 9] });
							gpsMarker = L.marker(latlng, { icon, zIndexOffset: 1000 }).addTo(map).bindPopup('あなたの現在地');
						}
						if (Number.isFinite(accuracy)) {
							if (gpsAccCircle) {
								gpsAccCircle.setLatLng(latlng).setRadius(Math.max(accuracy, 5));
							} else {
								gpsAccCircle = L.circle(latlng, { radius: Math.max(accuracy, 5), color: '#1e90ff', weight: 1, fillColor: '#1e90ff', fillOpacity: 0.15, interactive: false }).addTo(map);
							}
						}
						const now = Date.now();
						if (!lastPanAt || (now - lastPanAt) >= THROTTLE_MS) {
							const movedEnough = !lastPannedTo || (map.distance(L.latLng(lastPannedTo), L.latLng(latlng)) >= MIN_MOVE_M);
							if (movedEnough) {
								map.panTo(latlng, { animate: true });
								lastPannedTo = latlng;
								lastPanAt = now;
							}
						}
					},
					(err) => {
						alert(err && err.message ? err.message : '位置情報を取得できませんでした');
					},
					{ enableHighAccuracy: true, maximumAge: 30000, timeout: 20000 }
				);
			} else {
				alert('お使いのブラウザは位置情報取得に未対応です');
			}
		} else {
			if (gpsMarker) { map.removeLayer(gpsMarker); gpsMarker = null; }
			if (gpsAccCircle) { map.removeLayer(gpsAccCircle); gpsAccCircle = null; }
			if (gpsWatchId) { navigator.geolocation.clearWatch(gpsWatchId); gpsWatchId = null; }
			lastPannedTo = null;
			lastPanAt = 0;
		}
	});
}

export function setupDragForMarker(marker, code, map, authState, adjustMode, slug) {
	const el = marker.getElement();
	if (!el) return;
	const label = el.querySelector('.label');
	if (!label) return;
    
	label.onmousedown = null;
	label.ontouchstart = null;
	if (!(authState && authState.loggedIn && adjustMode)) {
		label.style.cursor = '';
		return;
	}

	let startX = 0, startY = 0;
	let startLatLng = null;
	let startPoint = null;
	let moved = false;

	const onMove = (clientX, clientY) => {
		const dx = Math.round(clientX - startX);
		const dy = Math.round(clientY - startY);
		if (!moved && (Math.abs(dx) > 3 || Math.abs(dy) > 3)) moved = true;
		const newPoint = L.point(startPoint.x + dx, startPoint.y + dy);
		const newLatLng = map.layerPointToLatLng(newPoint);
		marker.setLatLng(newLatLng);
	};

	const endDrag = async () => {
		document.removeEventListener('mousemove', mouseMove);
		document.removeEventListener('mouseup', mouseUp);
		document.removeEventListener('touchmove', touchMove);
		document.removeEventListener('touchend', touchEnd);
        
		map.dragging.enable();
		map.touchZoom.enable();
		map.doubleClickZoom.enable();
		map.scrollWheelZoom.enable();
		if (map.boxZoom) map.boxZoom.enable();
		if (map.keyboard) map.keyboard.enable();

		if (moved) {
			const newLatLng = marker.getLatLng();
			try {
				const res = await Api.moveMarker(slug, code, newLatLng.lat, newLatLng.lng);
				if (res && res.error) {
					throw new Error(res.error);
				}
				// Update local data to prevent revert on next fetch
				const event = new CustomEvent('marker-moved', { detail: { code, lat: newLatLng.lat, lng: newLatLng.lng } });
				document.dispatchEvent(event);
			} catch (err) {
				console.error('Marker move failed:', err);
				alert('位置の保存に失敗しました: ' + (err.message || 'Unknown error'));
				marker.setLatLng(startLatLng);
			}
		}
		moved = false;
	};

	const mouseMove = (e) => { e.preventDefault(); onMove(e.clientX, e.clientY); };
	const mouseUp = (e) => { e.preventDefault(); endDrag(); };
	const touchMove = (e) => { if (e.touches && e.touches[0]) { onMove(e.touches[0].clientX, e.touches[0].clientY); e.preventDefault(); } };
	const touchEnd = (_) => { endDrag(); };

	label.style.cursor = 'move';
	label.onmousedown = (e) => {
		e.preventDefault();
		startX = e.clientX; startY = e.clientY;
		startLatLng = marker.getLatLng();
		startPoint = map.latLngToLayerPoint(startLatLng);
		moved = false;
        
		map.dragging.disable();
		map.touchZoom.disable();
		map.doubleClickZoom.disable();
		map.scrollWheelZoom.disable();
		if (map.boxZoom) map.boxZoom.disable();
		if (map.keyboard) map.keyboard.disable();
		document.addEventListener('mousemove', mouseMove);
		document.addEventListener('mouseup', mouseUp);
	};
	label.ontouchstart = (e) => {
		if (!e.touches || !e.touches[0]) return;
		startX = e.touches[0].clientX; startY = e.touches[0].clientY;
		startLatLng = marker.getLatLng();
		startPoint = map.latLngToLayerPoint(startLatLng);
		moved = false;
        
		map.dragging.disable();
		map.touchZoom.disable();
		map.doubleClickZoom.disable();
		map.scrollWheelZoom.disable();
		if (map.boxZoom) map.boxZoom.disable();
		if (map.keyboard) map.keyboard.disable();
		document.addEventListener('touchmove', touchMove, { passive: false });
		document.addEventListener('touchend', touchEnd);
	};
}

