import { state } from './state.js';

function rgbArray(source, fallback = [146, 162, 224]) {
    if (!Array.isArray(source) || source.length < 3) source = fallback;
    return source.slice(0, 3).map((value) => Math.max(0, Math.min(255, Math.round(Number(value) || 0))));
}

function clamp01(value) {
    return Math.max(0, Math.min(1, Number(value) || 0));
}

function rgbToHsl(rgb) {
    const r = rgb[0] / 255;
    const g = rgb[1] / 255;
    const b = rgb[2] / 255;
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    let h = 0;
    let s = 0;
    const l = (max + min) / 2;
    if (max !== min) {
        const delta = max - min;
        s = l > 0.5 ? delta / (2 - max - min) : delta / (max + min);
        if (max === r) h = (g - b) / delta + (g < b ? 6 : 0);
        else if (max === g) h = (b - r) / delta + 2;
        else h = (r - g) / delta + 4;
        h /= 6;
    }
    return { h, s, l };
}

function hslToRgb(h, s, l) {
    h = ((h % 1) + 1) % 1;
    s = clamp01(s);
    l = clamp01(l);
    function hue2rgb(p, q, value) {
        if (value < 0) value += 1;
        if (value > 1) value -= 1;
        if (value < 1 / 6) return p + (q - p) * 6 * value;
        if (value < 1 / 2) return q;
        if (value < 2 / 3) return p + (q - p) * (2 / 3 - value) * 6;
        return p;
    }
    let r;
    let g;
    let b;
    if (s === 0) r = g = b = l;
    else {
        const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
        const p = 2 * l - q;
        r = hue2rgb(p, q, h + 1 / 3);
        g = hue2rgb(p, q, h);
        b = hue2rgb(p, q, h - 1 / 3);
    }
    return [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255)];
}

function deriveSpectrum(theme) {
    const hsl = rgbToHsl(theme);
    if (hsl.s < 0.08) {
        const fallback = rgbToHsl([146, 162, 224]);
        hsl.h = fallback.h;
        hsl.s = Math.max(hsl.s, fallback.s * 0.6);
    }
    return hslToRgb(
        hsl.h + 0.5,
        Math.max(0.18, Math.min(0.42, hsl.s * 0.62 + 0.08)),
        Math.max(0.32, Math.min(0.52, 0.42 + (hsl.l - 0.5) * 0.32)),
    );
}

function deriveContrast(spectrum) {
    const hsl = rgbToHsl(spectrum);
    return hslToRgb(
        hsl.h + 0.5,
        Math.max(0.34, Math.min(0.68, hsl.s * 1.18 + 0.08)),
        hsl.l < 0.48 ? 0.72 : 0.30,
    );
}

export function applyCoverPaletteFromData(data, token) {
    if (!data) return;
    const themeRgb = rgbArray(data.cover_theme_rgb || data.cover_palette_rgb);
    const spectrumRgb = rgbArray(data.spectrum_rgb, deriveSpectrum(themeRgb));
    const blockRgb = rgbArray(data.spectrum_block_rgb || data.cover_inverse_rgb, deriveContrast(spectrumRgb));
    const paintToken = (token || '') + '|theme=' + themeRgb.join(',') + '|spectrum=' + spectrumRgb.join(',') + '|block=' + blockRgb.join(',');
    if (paintToken === state.lastPaletteToken) return;
    const toRgba = (source, alpha) => {
        const color = rgbArray(source, themeRgb);
        return `rgba(${color[0]},${color[1]},${color[2]},${alpha})`;
    };

    state.coverTone = { r: themeRgb[0], g: themeRgb[1], b: themeRgb[2] };
    state.spectrumTone = { r: spectrumRgb[0], g: spectrumRgb[1], b: spectrumRgb[2] };
    state.blockTone = { r: blockRgb[0], g: blockRgb[1], b: blockRgb[2] };
    document.documentElement.style.setProperty('--cover-rgb', themeRgb.join(', '));
    document.documentElement.style.setProperty('--spectrum-rgb', spectrumRgb.join(', '));
    document.documentElement.style.setProperty('--spectrum-block-rgb', blockRgb.join(', '));
    document.documentElement.style.setProperty('--cover-1', toRgba(data.cover_palette_1 || themeRgb, 0.13));
    document.documentElement.style.setProperty('--cover-2', toRgba(data.cover_palette_2 || themeRgb, 0.09));
    document.documentElement.style.setProperty('--cover-3', toRgba(data.cover_palette_3 || themeRgb, 0.1));
    state.lastPaletteToken = paintToken;
    state.spectrumPaintCache = null;
}

function applyCoverBackground(background, url) {
    if (!background || !url) return;
    if (background.dataset.coverUrl !== url) {
        background.dataset.coverUrl = url;
        background.style.backgroundImage = `url("${url}")`;
    }
    background.classList.add('on');
    document.body.classList.add('has-cover');
}

function ambientCoverUrl(identity, version) {
    const params = [];
    if (identity) params.push('id=' + encodeURIComponent(identity));
    if (version) params.push('v=' + encodeURIComponent(String(version)));
    return '/api/media/cover/ambient' + (params.length ? '?' + params.join('&') : '');
}

export function setCover(url, playing, force = false, options = {}) {
    const identity = options.identity || url || '';
    const version = options.version || 0;
    const ambientUrl = options.ambientUrl || (url ? ambientCoverUrl(identity, version) : '');
    const disc = document.getElementById('coverDisc');
    const image = document.getElementById('coverImg');
    const background = document.getElementById('coverBg');
    if (!url) {
        if (disc) disc.hidden = true;
        if (background) {
            background.classList.remove('on');
            background.style.backgroundImage = '';
            delete background.dataset.coverUrl;
        }
        document.body.classList.remove('has-cover');
        if (image) {
            image.removeAttribute('src');
            image.onload = null;
        }
        state.lastPaletteToken = '';
        state.lastAmbientIdentity = '';
        return;
    }

    const sameSource = image && image.getAttribute('src') === url;
    const sourceChanged = !sameSource || force;
    const ambientChanged = force || identity !== state.lastAmbientIdentity || (background && background.dataset.coverUrl !== ambientUrl);
    if (ambientChanged && ambientUrl) {
        state.lastAmbientIdentity = identity;
        applyCoverBackground(background, ambientUrl);
    }
    if (image && sourceChanged) {
        image.onload = null;
        image.onerror = () => {};
        image.src = url;
    }
    if (disc) {
        disc.hidden = false;
        disc.classList.toggle('playing', Boolean(playing));
    }
    if (!ambientChanged && background && !background.classList.contains('on') && ambientUrl) {
        applyCoverBackground(background, ambientUrl);
    }
}
