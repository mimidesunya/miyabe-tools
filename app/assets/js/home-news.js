// トップページの「各地のお知らせ」を都道府県で切り替える。
// 全国の一覧は PHP が最初から並べてある。並べた HTML は /api/news-latest.php が返すので、
// ここでは差し替えるだけにする。選んだ都道府県はこのブラウザにだけ覚える。
(function () {
    'use strict';

    var STORAGE_KEY = 'miyabe-home-news-pref';
    var COLLAPSED_COUNT = 8;
    var section = document.querySelector('[data-home-news]');
    if (!section) {
        return;
    }
    var select = section.querySelector('[data-home-news-prefecture]');
    var list = section.querySelector('[data-home-news-list]');
    var updated = section.querySelector('[data-home-news-updated]');
    var more = section.querySelector('[data-home-news-more]');
    if (!select || !list) {
        return;
    }

    var cache = {};
    var current = 0;

    function remember(value) {
        try {
            if (value === 'all') {
                window.localStorage.removeItem(STORAGE_KEY);
            } else {
                window.localStorage.setItem(STORAGE_KEY, value);
            }
        } catch (error) {
            // 覚えられなくても切り替えはできる。
        }
    }

    function recalled() {
        try {
            return window.localStorage.getItem(STORAGE_KEY) || '';
        } catch (error) {
            return '';
        }
    }

    // スマホの幅では先頭の数件だけ出し、残りはボタンで開く（CSS の .is-collapsed）。
    function collapse() {
        if (!more) {
            return;
        }
        var count = list.querySelectorAll('.news-item').length;
        section.classList.add('is-collapsed');
        more.hidden = count <= COLLAPSED_COUNT;
        more.textContent = '残り ' + Math.max(0, count - COLLAPSED_COUNT) + ' 件も見る';
    }

    function show(payload) {
        list.innerHTML = payload.html;
        if (updated) {
            updated.textContent = payload.updated || '';
        }
        collapse();
    }

    function load(value) {
        var request = ++current;
        if (cache[value]) {
            show(cache[value]);
            return;
        }
        section.setAttribute('aria-busy', 'true');
        fetch('/api/news-latest.php?pref=' + encodeURIComponent(value), { headers: { Accept: 'application/json' } })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error('HTTP ' + response.status);
                }
                return response.json();
            })
            .then(function (payload) {
                cache[value] = payload;
                if (request === current) {
                    show(payload);
                }
            })
            .catch(function () {
                if (request === current) {
                    list.innerHTML = '<li class="news-empty">お知らせを読み込めませんでした。</li>';
                }
            })
            .finally(function () {
                if (request === current) {
                    section.removeAttribute('aria-busy');
                }
            });
    }

    if (more) {
        more.addEventListener('click', function () {
            section.classList.remove('is-collapsed');
            more.hidden = true;
        });
    }
    collapse();

    select.addEventListener('change', function () {
        remember(select.value);
        load(select.value);
    });

    var saved = recalled();
    if (/^[0-9]{2}$/.test(saved) && select.querySelector('option[value="' + saved + '"]')) {
        select.value = saved;
        load(saved);
    }
}());
