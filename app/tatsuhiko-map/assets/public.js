// 宮部たつひこマップ 公開ページ。API を定期取得して現在地マーカーを更新する。
(function () {
    'use strict';

    var API_URL = window.TMAP_API_URL || '/tatsuhiko-map/api.php';
    var POLL_MS = 30000;
    // 川崎市役所付近を初期表示にする。
    var KAWASAKI_CENTER = [35.5309, 139.7029];

    var statusEl = document.querySelector('[data-tmap-status]');
    var mapEl = document.getElementById('tmap-map');
    var map = null;
    var marker = null;
    var accuracyCircle = null;
    var hasCenteredOnLocation = false;

    function initMap() {
        if (map || !mapEl || typeof L === 'undefined') return;
        map = L.map(mapEl, { zoomControl: true }).setView(KAWASAKI_CENTER, 12);
        L.tileLayer('https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png', {
            attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">地理院タイル</a>',
            minZoom: 5,
            maxZoom: 18,
        }).addTo(map);
    }

    function formatUpdatedAt(iso) {
        if (!iso) return '';
        var date = new Date(iso);
        if (isNaN(date.getTime())) return '';
        var diffMinutes = Math.floor((Date.now() - date.getTime()) / 60000);
        var absolute = date.toLocaleString('ja-JP', { hour12: false });
        if (diffMinutes < 1) return absolute + '（1分以内）';
        if (diffMinutes < 60) return absolute + '（約' + diffMinutes + '分前）';
        return absolute;
    }

    function setStatus(text, isLive) {
        if (!statusEl) return;
        statusEl.textContent = text;
        statusEl.classList.toggle('is-live', !!isLive);
    }

    function clearMarker() {
        if (marker) { marker.remove(); marker = null; }
        if (accuracyCircle) { accuracyCircle.remove(); accuracyCircle = null; }
    }

    function render(payload) {
        initMap();
        if (!payload || !payload.sharing || !payload.location) {
            clearMarker();
            hasCenteredOnLocation = false;
            setStatus('位置情報の提供は現在オフです。', false);
            return;
        }
        var loc = payload.location;
        var latLng = [loc.lat, loc.lng];
        if (!marker) {
            marker = L.marker(latLng).addTo(map);
        } else {
            marker.setLatLng(latLng);
        }
        marker.bindPopup('宮部たつひこはこの付近にいます');
        if (typeof loc.accuracy === 'number' && loc.accuracy > 0) {
            if (!accuracyCircle) {
                accuracyCircle = L.circle(latLng, {
                    radius: loc.accuracy,
                    color: '#1d6fa5',
                    weight: 1,
                    fillOpacity: 0.12,
                }).addTo(map);
            } else {
                accuracyCircle.setLatLng(latLng);
                accuracyCircle.setRadius(loc.accuracy);
            }
        } else if (accuracyCircle) {
            accuracyCircle.remove();
            accuracyCircle = null;
        }
        if (!hasCenteredOnLocation) {
            map.setView(latLng, 15);
            hasCenteredOnLocation = true;
        }
        var updated = formatUpdatedAt(loc.updated_at);
        setStatus('位置情報を提供中です。' + (updated ? ' 最終更新: ' + updated : ''), true);
    }

    function poll() {
        fetch(API_URL, { cache: 'no-store' })
            .then(function (res) { return res.json(); })
            .then(render)
            .catch(function () {
                setStatus('状態の取得に失敗しました。時間をおいて再読み込みしてください。', false);
            });
    }

    function start() {
        initMap();
        poll();
        setInterval(poll, POLL_MS);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
