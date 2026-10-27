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
});
