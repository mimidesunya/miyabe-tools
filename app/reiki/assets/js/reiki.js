function currentSlug() {
    return document.body && document.body.dataset ? (document.body.dataset.reikiSlug || '') : '';
}

function buildPageUrl(extraParams) {
    const url = new URL('/reiki/', window.location.origin);
    const slug = currentSlug();
    if (slug) {
        url.searchParams.set('slug', slug);
    }
    Object.entries(extraParams || {}).forEach(([key, value]) => {
        if (value != null && value !== '') {
            url.searchParams.set(key, value);
        }
    });
    return url.toString();
}

function buildApiUrl(action, extraParams) {
    const url = new URL('/reiki/api.php', window.location.origin);
    url.searchParams.set('action', action);
    const slug = currentSlug();
    if (slug) {
        url.searchParams.set('slug', slug);
    }
    Object.entries(extraParams || {}).forEach(([key, value]) => {
        if (value != null && value !== '') {
            url.searchParams.set(key, value);
        }
    });
    return url.toString();
}

window.linkinyo = function(_sysCode, lawId) {
    if (!lawId) {
        return false;
    }

    const targetFile = `${lawId}_j.html`;
    const url = buildPageUrl({ file: targetFile });
    window.location.href = url;
    return false;
};

document.addEventListener('DOMContentLoaded', function() {
    const toggle = document.getElementById('menu-toggle');
    const sidebar = document.querySelector('.sidebar');
    const sidebarScrollKey = 'reiki:sidebar-scroll-top';

    if (sidebar) {
        const savedScrollTop = window.sessionStorage.getItem(sidebarScrollKey);
        if (savedScrollTop !== null) {
            const parsed = Number.parseInt(savedScrollTop, 10);
            if (!Number.isNaN(parsed)) {
                requestAnimationFrame(() => {
                    sidebar.scrollTop = parsed;
                });
            }
        }

        sidebar.addEventListener('scroll', () => {
            window.sessionStorage.setItem(sidebarScrollKey, String(sidebar.scrollTop));
        }, { passive: true });
    }

    if (toggle && sidebar) {
        toggle.addEventListener('click', function() {
            sidebar.classList.toggle('open');
        });
    }
    
    // Close sidebar when clicking a link in it (on mobile)
    if (sidebar) {
        const links = sidebar.querySelectorAll('a');
        links.forEach(link => {
            link.addEventListener('click', () => {
                window.sessionStorage.setItem(sidebarScrollKey, String(sidebar.scrollTop));
                if (window.innerWidth <= 768) {
                    sidebar.classList.remove('open');
                }
            });
        });

        const forms = sidebar.querySelectorAll('form');
        forms.forEach(form => {
            form.addEventListener('submit', () => {
                window.sessionStorage.setItem(sidebarScrollKey, String(sidebar.scrollTop));
            });
        });
    }

    // ─── Filter checkbox counter ───
    const groups = document.querySelectorAll('[data-filter-group]');
    groups.forEach((group) => {
        const checkboxes = group.querySelectorAll('input[type="checkbox"]');
        const counter = group.querySelector('[data-selected-count]');

        const updateCount = () => {
            const count = Array.from(checkboxes).filter((checkbox) => checkbox.checked).length;
            if (counter) {
                counter.textContent = `${count}件選択`;
            }
        };

        updateCount();
        checkboxes.forEach((checkbox) => checkbox.addEventListener('change', updateCount));
    });

    // ─── Collapsible filters on mobile ───
    const isCompactLayout = window.matchMedia('(max-width: 1024px)').matches;
    document.querySelectorAll('.filter-block').forEach(block => {
        const title = block.querySelector('.filter-title');
        if (!title) return;
        title.addEventListener('click', (e) => {
            e.preventDefault();
            block.classList.toggle('expanded');
        });
        if (!isCompactLayout) {
            block.classList.add('expanded');
        }
    });

    // ─── Collapsible guide on mobile ───
    const guideSection = document.getElementById('guide-section');
    if (guideSection) {
        const guideToggle = guideSection.querySelector('.guide-toggle');
        if (guideToggle) {
            guideToggle.addEventListener('click', () => {
                guideSection.classList.toggle('expanded');
            });
        }
        if (!isCompactLayout) {
            guideSection.classList.add('expanded');
        }
    }

    // ─── Feedback System ───
    const feedbackSection = document.getElementById('feedback-section');
    if (feedbackSection) {
        const filename = feedbackSection.dataset.filename;
        const slug = currentSlug();
        const cookieKey = 'reiki_voted_' + slug + '_' + filename;

        function hasVoted() {
            return document.cookie.split(';').some(c => c.trim().startsWith(cookieKey + '='));
        }

        function setVotedCookie() {
            const expires = new Date(Date.now() + 365 * 24 * 60 * 60 * 1000).toUTCString();
            document.cookie = cookieKey + '=1; path=/; expires=' + expires + '; SameSite=Lax';
        }

        function updateVoteCounts(votes) {
            document.getElementById('count-good').textContent = votes.good;
            document.getElementById('count-bad').textContent = votes.bad;
        }

        function renderComments(comments) {
            const ul = document.getElementById('comments-ul');
            const container = document.getElementById('comments-list');
            ul.innerHTML = '';
            if (!comments || comments.length === 0) {
                container.style.display = 'none';
                return;
            }
            container.style.display = 'block';
            comments.forEach(c => {
                const li = document.createElement('li');
                li.style.cssText = 'padding:4px 0; border-bottom:1px solid #f1f5f9; display:flex; gap:8px; align-items:flex-start;';
                const icon = c.vote === 'good' ? '👍' : '👎';
                const date = c.created_at ? c.created_at.substring(0, 16).replace('T', ' ') : '';
                li.innerHTML = '<span>' + icon + '</span><span style="flex:1; word-break:break-all;">' + escapeHtml(c.comment) + '</span><span style="font-size:11px; color:#94a3b8; white-space:nowrap;">' + escapeHtml(date) + '</span>';
                ul.appendChild(li);
            });
        }

        function escapeHtml(str) {
            const d = document.createElement('div');
            d.textContent = str;
            return d.innerHTML;
        }

        function disableButtons() {
            document.getElementById('btn-good').disabled = true;
            document.getElementById('btn-bad').disabled = true;
            document.getElementById('btn-good').style.opacity = '0.5';
            document.getElementById('btn-bad').style.opacity = '0.5';
            document.getElementById('btn-good').style.cursor = 'default';
            document.getElementById('btn-bad').style.cursor = 'default';
            document.getElementById('comment-input').disabled = true;
            document.getElementById('vote-status').textContent = '投票済み';
        }

        // Load stats
        fetch(buildApiUrl('stats', { filename: filename }))
            .then(r => r.json())
            .then(data => {
                updateVoteCounts(data.votes);
                renderComments(data.comments);
                document.getElementById('view-count').textContent = data.viewCount;
                if (hasVoted()) {
                    disableButtons();
                }
            })
            .catch(() => {});

        // Record view
        fetch(buildApiUrl('view'), {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ slug: slug, filename: filename })
        }).then(r => r.json()).then(data => {
            if (data.viewCount !== undefined) {
                document.getElementById('view-count').textContent = data.viewCount;
            }
        }).catch(() => {});

        // Submit vote (called from onclick)
        window.submitVote = function(vote) {
            if (hasVoted()) {
                document.getElementById('vote-status').textContent = '既に投票済みです';
                return;
            }
            const comment = (document.getElementById('comment-input').value || '').trim();
            fetch(buildApiUrl('vote'), {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ slug: slug, filename: filename, vote: vote, comment: comment })
            })
            .then(r => r.json())
            .then(data => {
                if (data.ok) {
                    updateVoteCounts(data.votes);
                    setVotedCookie();
                    disableButtons();
                    document.getElementById('vote-status').textContent = '投票しました！';
                    fetch(buildApiUrl('stats', { filename: filename }))
                        .then(r => r.json())
                        .then(d => renderComments(d.comments))
                        .catch(() => {});
                } else {
                    document.getElementById('vote-status').textContent = 'エラーが発生しました';
                }
            })
            .catch(() => {
                document.getElementById('vote-status').textContent = '通信エラー';
            });
        };
    }
});
