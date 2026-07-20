import { secureFetch } from './api.js';
import { state } from './state.js';

let onRenderOptionsChanged = null;

export function configureControls(options = {}) {
    onRenderOptionsChanged = typeof options.onRenderOptionsChanged === 'function'
        ? options.onRenderOptionsChanged
        : null;
}

function fmtMs(ms) {
    const value = Math.round(ms || 0);
    return (value >= 0 ? '+' : '') + value + 'ms';
}

export function renderOffsets() {
    const lyric = document.getElementById('lyricOffsetVal');
    const spectrum = document.getElementById('spectrumOffsetVal');
    if (lyric) lyric.textContent = state.lyricOffset.toFixed(1) + 's';
    if (spectrum) spectrum.textContent = fmtMs(state.spectrumOffsetMs);
}

export function applyMusicOffsetData(data) {
    if (!data) return;
    if (typeof data.spectrum_offset_ms === 'number') state.spectrumOffsetMs = data.spectrum_offset_ms;

    const nextFps = typeof data.render_fps === 'number' ? data.render_fps : state.spectrumRenderFps;
    const nextBars = typeof data.render_bars === 'number' ? data.render_bars : state.spectrumRenderBars;
    const renderChanged = nextFps !== state.spectrumRenderFps || nextBars !== state.spectrumRenderBars;
    state.spectrumRenderFps = nextFps;
    state.spectrumRenderBars = nextBars;
    if (renderChanged && onRenderOptionsChanged) onRenderOptionsChanged();
    renderOffsets();
}

export function adjustLyric(delta) {
    return secureFetch('/api/media/offset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ delta }),
    }).then((response) => response.json()).then((data) => {
        state.lyricOffset = Number(data.offset || 0);
        renderOffsets();
    }).catch(() => {});
}

export function pushMusicOffsets(payload) {
    return secureFetch('/api/music/offset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    }).then((response) => response.json()).then(applyMusicOffsetData).catch(() => {});
}

export function loadOffsets() {
    secureFetch('/api/media/offset')
        .then((response) => response.json())
        .then((data) => {
            if (typeof data.offset === 'number') state.lyricOffset = data.offset;
            renderOffsets();
        }).catch(() => {});
    secureFetch('/api/music/offset')
        .then((response) => response.json())
        .then(applyMusicOffsetData)
        .catch(() => {});
}

export function bindActionControls() {
    document.querySelectorAll('[data-action]').forEach((button) => {
        button.addEventListener('click', () => {
            const delta = Number(button.dataset.delta || 0);
            if (button.dataset.action === 'lyric-offset') adjustLyric(delta);
            else if (button.dataset.action === 'spectrum-offset') pushMusicOffsets({ delta_spectrum_offset_ms: delta });
        });
    });
}

export function setupMenu() {
    const dots = document.getElementById('menuDots');
    const panel = document.getElementById('menuPanel');
    if (!dots || !panel) return;
    dots.addEventListener('click', (event) => {
        event.stopPropagation();
        panel.hidden = !panel.hidden;
    });
    document.addEventListener('click', (event) => {
        if (!panel.hidden && !panel.contains(event.target) && event.target !== dots) panel.hidden = true;
    });
}

export function updateStageClock() {
    const element = document.getElementById('stageClock');
    if (!element) return;
    const now = new Date();
    const text = String(now.getHours()).padStart(2, '0') + ':' + String(now.getMinutes()).padStart(2, '0');
    if (element.textContent !== text) {
        element.textContent = text;
        element.setAttribute('datetime', text);
    }
}
