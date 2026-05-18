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

    function searchTerms(value) {
        const source = String(value || '').replace(/\u3000/g, ' ').trim();
        if (!source) {
            return [];
        }
        const terms = [];
        const seen = new Set();
        const pattern = /"([^"]+)"|'([^']+)'|(\S+)/g;
        let match;
        while ((match = pattern.exec(source)) !== null) {
            const term = String(match[1] || match[2] || match[3] || '').trim();
            const key = term.toLocaleLowerCase();
            if (term !== '' && !seen.has(key)) {
                seen.add(key);
                terms.push(term);
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
    renderBody(true);
})();
