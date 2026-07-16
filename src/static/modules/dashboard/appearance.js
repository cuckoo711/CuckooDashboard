import { createFontLoader } from '../shared/font-loader.js';
import { fetchFont, fetchNextTheme, fetchTheme } from './api.js';
import { drawRing } from './render-dashboard.js';
import { state } from './state.js';

const applyFontUrl = createFontLoader({
    familyPrefix: 'DashboardFont',
    fallback: '"Segoe UI",-apple-system,BlinkMacSystemFont,sans-serif',
});

export function applyFontSize(data = {}) {
    const root = document.documentElement.style;
    root.setProperty('--fs-offset', `${data.offset || 0}px`);
    root.setProperty('--fs-title', `${data.title || 16}px`);
    root.setProperty('--fs-clock', `${data.clock || 22}px`);
    root.setProperty('--fs-date', `${data.date || 15}px`);
    root.setProperty('--fs-card-head', `${data.card_head || 10}px`);
    root.setProperty('--fs-card-head-c', `${Math.round((data.card_head || 10) * 1.1)}px`);
    root.setProperty('--fs-card-foot', `${data.card_foot || 10}px`);
    root.setProperty('--fs-body', `${data.card_body || 10}px`);
    const title = document.querySelector('.hdr h1');
    if (title) title.textContent = data.title_text || 'Cuckoo Dashboard';
}

export function applyFont(data = {}) {
    applyFontUrl(data.url || '');
    if (data.font_size) applyFontSize(data.font_size);
}

export function applyTheme(data = {}) {
    const name = data.theme || 'dark';
    const background = data.bg || {};
    state.appearance.themeData = data;
    document.body.dataset.theme = name;
    document.body.classList.toggle('mono', name === 'mono');
    if (background.bg_type === 'image' && background.bg_image) {
        document.body.style.background = `url('${background.bg_image}') center/cover no-repeat fixed`;
        document.body.style.backgroundColor = background.bg_color || '#000';
    } else {
        document.body.style.background = background.bg_color || '#0a0618';
    }
    try { localStorage.setItem('themeData', JSON.stringify(data)); } catch (_error) {}
    if (state.dashboard.lastRingArgs) drawRing(...state.dashboard.lastRingArgs);
}

export async function cycleTheme() {
    try { applyTheme(await fetchNextTheme()); }
    catch (error) { console.error('[theme] switch failed:', error); }
}

export async function initAppearance() {
    fetchTheme().then(applyTheme).catch(() => {
        try {
            const cached = JSON.parse(localStorage.getItem('themeData'));
            if (cached) applyTheme(cached);
        } catch (_error) {}
    });
    fetchFont().then(applyFont).catch(() => {});
}
