export function escHtml(value) {
    const div = document.createElement('div');
    div.appendChild(document.createTextNode(value || ''));
    return div.innerHTML;
}

export function fmtTok(value) {
    const number = Number(value) || 0;
    if (number >= 1e12) return `${(number / 1e12).toFixed(2)}T`;
    if (number >= 1e9) return `${(number / 1e9).toFixed(2)}B`;
    if (number >= 1e6) return `${(number / 1e6).toFixed(2)}M`;
    if (number >= 1e3) return `${(number / 1e3).toFixed(1)}K`;
    return number.toLocaleString();
}

export function fmtNum(value) {
    return (Number(value) || 0).toLocaleString();
}

export function cssVar(name) {
    return getComputedStyle(document.body).getPropertyValue(name).trim()
        || getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function fmtBytes(value) {
    const bytes = Number(value) || 0;
    if (bytes >= 1073741824) return `${(bytes / 1073741824).toFixed(1)} GB`;
    if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MB`;
    if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${bytes} B`;
}

export function safeNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : 0;
}

export function currencyPrefix(currency) {
    const code = typeof currency === 'string' ? currency.trim().toUpperCase() : '';
    const symbols = { CNY: '¥', USD: '$', EUR: '€', GBP: '£', JPY: '¥', KRW: '₩' };
    return symbols[code] || (code ? `${code} ` : '');
}
