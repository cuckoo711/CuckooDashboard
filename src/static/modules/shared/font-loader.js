export function guessFontFormat(url) {
    const ext = (String(url || '').split('.').pop() || '').toLowerCase();
    if (ext === 'woff2') return 'woff2';
    if (ext === 'woff') return 'woff';
    if (ext === 'otf') return 'opentype';
    return 'truetype';
}

export function createFontLoader({ familyPrefix, fallback }) {
    let styleElement = null;
    let cachedUrl = null;

    return function applyFontUrl(url) {
        url = String(url || '');
        if (cachedUrl === url) return;
        cachedUrl = url;
        if (styleElement && styleElement.parentNode) styleElement.parentNode.removeChild(styleElement);
        styleElement = null;
        if (!url) {
            document.body.style.fontFamily = fallback;
            return;
        }
        const family = `${familyPrefix}_${Date.now()}`;
        const style = document.createElement('style');
        style.textContent = `@font-face{font-family:"${family}";src:url("${url}") format("${guessFontFormat(url)}");font-display:swap;}`;
        document.head.appendChild(style);
        styleElement = style;
        document.body.style.fontFamily = `"${family}",${fallback}`;
    };
}
