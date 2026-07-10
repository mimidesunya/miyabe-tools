// 宮部たつひこマップ 管理ページ。位置提供の ON/OFF と GPS の送信を行う。
(function () {
    'use strict';

    var API_URL = window.TMAP_API_URL || '/tatsuhiko-map/api.php';
    var CSRF = window.TMAP_CSRF || '';
    var MIN_SEND_INTERVAL_MS = 15000;

    var toggleButton = document.querySelector('[data-tmap-toggle]');
    var sharingText = document.querySelector('[data-tmap-sharing-text]');
    var gpsText = document.querySelector('[data-tmap-gps-text]');
    var lastSentText = document.querySelector('[data-tmap-last-sent]');
    var errorBox = document.querySelector('[data-tmap-error]');
    var clearButton = document.querySelector('[data-tmap-clear]');

    var sharing = !!(window.TMAP_INITIAL_STATE && window.TMAP_INITIAL_STATE.sharing);
    var watchId = null;
    var lastSentAt = 0;
    var pendingToggle = false;

    function showError(message) {
        if (!errorBox) return;
        if (!message) {
            errorBox.hidden = true;
            errorBox.textContent = '';
            return;
        }
        errorBox.hidden = false;
        errorBox.textContent = message;
    }

    function post(body) {
        return fetch(API_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Tmap-Csrf': CSRF,
            },
            body: JSON.stringify(body),
        }).then(function (res) {
            return res.json().then(function (json) {
                if (!res.ok) {
                    throw new Error(json && json.error ? json.error : 'HTTP ' + res.status);
                }
                return json;
            });
        });
    }

    function renderSharing() {
        if (toggleButton) {
            toggleButton.textContent = sharing ? 'ON（公開中）' : 'OFF（非公開）';
            toggleButton.setAttribute('aria-pressed', sharing ? 'true' : 'false');
            toggleButton.classList.toggle('is-on', sharing);
            toggleButton.disabled = pendingToggle;
        }
        if (sharingText) {
            sharingText.textContent = sharing
                ? '公開中（地図に現在地が表示されます）'
                : '非公開（座標は配信されません）';
        }
    }

    function setGpsText(text) {
        if (gpsText) gpsText.textContent = text;
    }

    function sendPosition(position, force) {
        var now = Date.now();
        if (!force && now - lastSentAt < MIN_SEND_INTERVAL_MS) return;
        lastSentAt = now;
        post({
            action: 'update_location',
            lat: position.coords.latitude,
            lng: position.coords.longitude,
            accuracy: position.coords.accuracy,
        }).then(function () {
            showError('');
            if (lastSentText) {
                lastSentText.textContent = new Date().toLocaleTimeString('ja-JP', { hour12: false })
                    + '（精度 約' + Math.round(position.coords.accuracy) + 'm）';
            }
        }).catch(function (err) {
            showError('位置の送信に失敗しました: ' + err.message);
        });
    }

    function startGps() {
        if (watchId !== null) return;
        if (!('geolocation' in navigator)) {
            setGpsText('この端末では GPS を利用できません');
            showError('この端末・ブラウザは位置情報取得に対応していません。');
            return;
        }
        setGpsText('取得中…');
        var firstFix = true;
        watchId = navigator.geolocation.watchPosition(
            function (position) {
                setGpsText('取得中（このページを開いている間、自動送信します）');
                sendPosition(position, firstFix);
                firstFix = false;
            },
            function (error) {
                setGpsText('取得できません');
                var reason = error.code === error.PERMISSION_DENIED
                    ? '位置情報の利用が許可されていません。ブラウザの設定を確認してください。'
                    : '位置情報を取得できませんでした（' + error.message + '）。';
                showError(reason);
            },
            { enableHighAccuracy: true, maximumAge: 10000, timeout: 30000 }
        );
    }

    function stopGps() {
        if (watchId !== null) {
            navigator.geolocation.clearWatch(watchId);
            watchId = null;
        }
        setGpsText('停止中');
    }

    function applySharing(next) {
        pendingToggle = true;
        renderSharing();
        post({ action: 'set_sharing', sharing: next })
            .then(function (json) {
                sharing = !!(json.state && json.state.sharing);
                showError('');
                if (sharing) {
                    startGps();
                } else {
                    stopGps();
                }
            })
            .catch(function (err) {
                showError('切り替えに失敗しました: ' + err.message);
            })
            .then(function () {
                pendingToggle = false;
                renderSharing();
            });
    }

    if (toggleButton) {
        toggleButton.addEventListener('click', function () {
            if (!pendingToggle) applySharing(!sharing);
        });
    }

    if (clearButton) {
        clearButton.addEventListener('click', function () {
            post({ action: 'clear_location' })
                .then(function () {
                    showError('');
                    if (lastSentText) lastSentText.textContent = '—（消去済み）';
                })
                .catch(function (err) {
                    showError('消去に失敗しました: ' + err.message);
                });
        });
    }

    renderSharing();
    if (sharing) {
        startGps();
    }
})();
