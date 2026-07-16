import { state } from './state.js';

function ensurePageTransit() {
    let element = document.getElementById('pageTransit');
    if (element) return element;
    element = document.createElement('div');
    element.id = 'pageTransit';
    element.className = 'page-transit';
    element.setAttribute('aria-hidden', 'true');
    element.innerHTML = '<div class="page-transit-inner"><span class="page-transit-dot"></span><span class="page-transit-dot"></span><span class="page-transit-dot"></span></div>';
    document.body.appendChild(element);
    return element;
}

export function navigatePage(url) {
    if (!url || state.navigation.locked) return;
    if (url === location.pathname || url === location.href) return;
    state.navigation.locked = true;
    document.body.classList.add('page-leaving');
    ensurePageTransit().classList.add('show');
    requestAnimationFrame(() => setTimeout(() => { location.href = url; }, 50));
}

export function softReload() {
    if (state.navigation.locked) return;
    state.navigation.locked = true;
    document.body.classList.add('page-leaving');
    ensurePageTransit().classList.add('show');
    requestAnimationFrame(() => setTimeout(() => location.reload(), 50));
}

export function prefetchSiblingPage() {
    const urls = ['/music', '/static/music.css', '/static/modules/music/main.js'];
    urls.forEach((url) => {
        try {
            const link = document.createElement('link');
            link.rel = 'prefetch';
            link.href = url;
            link.as = url.includes('.css') ? 'style' : url.includes('.js') ? 'script' : 'document';
            document.head.appendChild(link);
        } catch (_error) {}
        try { fetch(url, { credentials: 'same-origin', cache: 'force-cache', mode: 'no-cors' }).catch(() => {}); }
        catch (_error) {}
    });
}

export function initNavigation() {
    const idle = window.requestIdleCallback || ((callback) => setTimeout(callback, 1400));
    idle(prefetchSiblingPage, { timeout: 3000 });
    document.addEventListener('click', (event) => {
        const anchor = event.target?.closest?.('a[href]');
        if (!anchor) return;
        const href = anchor.getAttribute('href') || '';
        if (!href || href.startsWith('#') || anchor.target === '_blank') return;
        if (href.includes('://') && !href.startsWith(location.origin)) return;
        if (href === '/' || href.startsWith('/music') || href.startsWith('/static/')) {
            event.preventDefault();
            navigatePage(href);
        }
    });
}
