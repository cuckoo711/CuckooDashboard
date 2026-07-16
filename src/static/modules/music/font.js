import { createFontLoader } from '../shared/font-loader.js';
import { secureFetch } from './api.js';

const applyFontUrl = createFontLoader({
    familyPrefix: 'CuckooStageFont',
    fallback: '"Segoe UI Variable","Segoe UI","PingFang SC","Microsoft YaHei",sans-serif',
});

export function applyStageFont(data = {}) {
    applyFontUrl(data.url || '');
}

export function loadStageFont() {
    return secureFetch('/api/font')
        .then((response) => response.json())
        .then(applyStageFont)
        .catch(() => {});
}
