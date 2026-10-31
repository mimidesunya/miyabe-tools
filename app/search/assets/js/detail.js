(() => {
    const bootNode = document.getElementById('search-detail-boot');
    const bodyNode = document.getElementById('detail-body');
    if (!bootNode || !bodyNode) {
        return;
    }

    const boot = JSON.parse(bootNode.textContent || '{}');
    const documentBody = String(boot.document?.body || '');
    const refs = {
        input: document.getElementById('detail-search-input'),
        prev: document.getElementById('detail-prev'),
        next: document.getElementById('detail-next'),
        count: document.getElementById('detail-count'),
    };
    const state = {
        matches: [],
        active: 0,
    };

    function escapeRegExp(value) {
        return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }

    /* 全角の英数字・記号を半角に、全角空白を空白に、曲がった二重引用符を " に（検索 API と同じそろえ方） */
    function foldQuery(value) {
        return String(value || '')
            .replace(/[\uFF01-\uFF5E]/g, (char) => String.fromCharCode(char.charCodeAt(0) - 0xFEE0))
            .replace(/\u3000/g, ' ')
            .replace(/[\u201C\u201D]/g, '"');
    }

    /**
     * 光らせる語。検索の書き方（OR・NOT・AND・括弧・| ・頭の -・引用符）を読み、演算子そのものと、
     * NOT や - で外した語（括弧ごと外したときは括弧の中）は光らせない。引用符の中は 1 つの語句。
     */
    function searchTerms(value) {
        const source = foldQuery(value).trim();
        if (!source) {
            return [];
        }
        const tokens = source.match(/"[^"]*"|[()]|[^\s()"]+/g) || [];
        const terms = [];
        const seen = new Set();
        let negateNext = false;
        let negatedDepth = 0;
        let depth = 0;
        for (const raw of tokens) {
            if (raw === '(') {
                depth += 1;
                if (negateNext && negatedDepth === 0) {
                    negatedDepth = depth;
                }
                negateNext = false;
                continue;
            }
            if (raw === ')') {
                if (negatedDepth === depth) {
                    negatedDepth = 0;
                }
                depth = Math.max(0, depth - 1);
                continue;
            }
            const upper = raw.toUpperCase();
            if (upper === 'AND' || upper === 'OR' || /^NEAR(?:\/\d+)?$/.test(upper)) {
                continue;
            }
            if (upper === 'NOT') {
                negateNext = true;
                continue;
            }
            let token = raw;
            while (token.startsWith('-') || token.startsWith('+')) {
                if (token.startsWith('-')) {
                    negateNext = true;
                }
                token = token.slice(1);
            }
            if (token === '') {
                continue;
            }
            const negated = negateNext || negatedDepth > 0;
            negateNext = false;
            if (negated) {
                continue;
            }
            const quoted = token.length >= 2 && token.startsWith('"') && token.endsWith('"');
            const parts = quoted ? [token.slice(1, -1)] : token.replace(/\*+$/, '').split('|');
            for (const part of parts) {
                const term = part.trim();
                const key = term.toLocaleLowerCase();
                if (term !== '' && !seen.has(key)) {
                    seen.add(key);
                    terms.push(term);
                }
            }
        }
        return terms.sort((a, b) => b.length - a.length);
    }

    function appendHighlightedText(parent, text, regex) {
        if (!regex) {
            parent.appendChild(document.createTextNode(text));
            return;
        }
        let cursor = 0;
        let match;
        regex.lastIndex = 0;
        while ((match = regex.exec(text)) !== null) {
            if (match.index > cursor) {
                parent.appendChild(document.createTextNode(text.slice(cursor, match.index)));
            }
            const mark = document.createElement('mark');
            mark.className = 'detail-hit';
            mark.textContent = match[0];
            parent.appendChild(mark);
            state.matches.push(mark);
            cursor = match.index + match[0].length;
            if (match[0].length === 0) {
                regex.lastIndex += 1;
            }
        }
        if (cursor < text.length) {
            parent.appendChild(document.createTextNode(text.slice(cursor)));
        }
    }

    function appendTextWithBreaks(parent, text, regex) {
        const lines = String(text).split('\n');
        lines.forEach((line, index) => {
            if (index > 0) {
                parent.appendChild(document.createElement('br'));
            }
            appendHighlightedText(parent, line, regex);
        });
    }

    function setActiveMatch(index, shouldScroll = true) {
        state.matches.forEach((node) => node.classList.remove('is-active'));
        if (state.matches.length === 0) {
            state.active = 0;
            refs.count.textContent = '0 / 0';
            refs.prev.disabled = true;
            refs.next.disabled = true;
            return;
        }
        state.active = (index + state.matches.length) % state.matches.length;
        const node = state.matches[state.active];
        node.classList.add('is-active');
        refs.count.textContent = `${state.active + 1} / ${state.matches.length}`;
        refs.prev.disabled = false;
        refs.next.disabled = false;
        if (shouldScroll) {
            node.scrollIntoView({ block: 'center', behavior: 'smooth' });
        }
    }

    function renderBody(shouldScroll = false) {
        state.matches = [];
        state.active = 0;
        bodyNode.innerHTML = '';

        const terms = searchTerms(refs.input.value);
        const regex = terms.length
            ? new RegExp(terms.map(escapeRegExp).join('|'), 'giu')
            : null;
        const blocks = documentBody
            .replace(/\r\n/g, '\n')
            .replace(/\r/g, '\n')
            .split(/\n{2,}/)
            .map((block) => block.trim())
            .filter(Boolean);

        if (blocks.length === 0) {
            const empty = document.createElement('p');
            empty.className = 'detail-empty';
            empty.textContent = '本文がありません。';
            bodyNode.appendChild(empty);
        } else {
            blocks.forEach((block) => {
                const paragraph = document.createElement('p');
                appendTextWithBreaks(paragraph, block, regex);
                bodyNode.appendChild(paragraph);
            });
        }
        setActiveMatch(0, shouldScroll && state.matches.length > 0);
    }

    refs.input.value = String(boot.query || '');
    refs.input.addEventListener('input', () => renderBody(false));
    refs.prev.addEventListener('click', () => setActiveMatch(state.active - 1));
    refs.next.addEventListener('click', () => setActiveMatch(state.active + 1));
    // 事務事業評価で事業の一覧を本文より前に出している頁と、事業を指して来た頁では、
    // 開いたとたんに本文の最初の一致へ動かない（一覧や指した事業を飛ばしてしまう）。
    renderBody(!document.getElementById('hyoka-records') && !window.location.hash);
})();

// 事務事業評価の「この文書の事業」を語で絞る。語は空白で区切り、すべてを含む事業だけ残す。
(() => {
    const input = document.getElementById('hyoka-record-filter');
    const list = document.querySelector('.hyoka-record-list');
    if (!input || !list) {
        return;
    }
    const count = document.getElementById('hyoka-record-count');
    const items = Array.from(list.querySelectorAll('.hyoka-record'));
    const texts = items.map((item) => item.textContent.replace(/\s+/g, ' ').toLocaleLowerCase());

    function apply() {
        const terms = input.value.replace(/　/g, ' ').trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
        let shown = 0;
        items.forEach((item, index) => {
            const matched = terms.every((term) => texts[index].includes(term));
            item.hidden = !matched;
            if (matched) {
                shown += 1;
            }
        });
        if (count) {
            count.textContent = terms.length ? `${shown} / ${items.length}件` : `${items.length}件`;
        }
    }

    input.addEventListener('input', apply);
    apply();
})();

// 事業の多い文書は見出しだけを先に出してあるので、開いたときに中身を読む。
(() => {
    const section = document.querySelector('.hyoka-records[data-detail-id]');
    if (!section) {
        return;
    }
    const detailId = section.getAttribute('data-detail-id') || '';
    section.querySelectorAll('details[data-record-id]').forEach((details) => {
        details.addEventListener('toggle', () => {
            const body = details.querySelector('.hyoka-record-body[data-pending]');
            if (!details.open || !body) {
                return;
            }
            body.removeAttribute('data-pending');
            body.textContent = '読み込んでいます。';
            const params = new URLSearchParams({ id: detailId, record: details.getAttribute('data-record-id') || '' });
            fetch(`/api/hyoka-record.php?${params.toString()}`)
                .then((response) => (response.ok ? response.text() : Promise.reject(new Error(String(response.status)))))
                .then((html) => {
                    // 中身はサーバーで文字を逃がして組んだ HTML。
                    body.innerHTML = html;
                })
                .catch(() => {
                    body.setAttribute('data-pending', '');
                    body.textContent = '読み込めませんでした。閉じてもう一度開いてください。';
                });
        });
    });
})();

// 検索結果の一覧の事業の行から来たとき（URL の #record-…）、その事業を開いて見せる。
(() => {
    const hash = decodeURIComponent(window.location.hash.slice(1));
    if (!hash.startsWith('record-')) {
        return;
    }
    const item = document.getElementById(hash);
    const details = item ? item.querySelector('details') : null;
    if (!details) {
        return;
    }
    details.open = true;
    item.classList.add('is-target');
    item.scrollIntoView({ block: 'start' });
})();
