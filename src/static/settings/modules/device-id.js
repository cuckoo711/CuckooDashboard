const STORAGE_KEY = 'cuckoo.dashboard.device_id';

function uuidv4() {
    if (globalThis.crypto?.randomUUID) {
        return globalThis.crypto.randomUUID();
    }
    const bytes = new Uint8Array(16);
    if (globalThis.crypto?.getRandomValues) {
        globalThis.crypto.getRandomValues(bytes);
    } else {
        for (let index = 0; index < bytes.length; index += 1) {
            bytes[index] = Math.floor(Math.random() * 256);
        }
    }
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map((value) => value.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function getDeviceId() {
    try {
        const existing = window.localStorage.getItem(STORAGE_KEY);
        if (existing && /^[0-9a-f-]{36}$/i.test(existing)) {
            return existing.toLowerCase();
        }
        const created = uuidv4().toLowerCase();
        window.localStorage.setItem(STORAGE_KEY, created);
        return created;
    } catch (_error) {
        return uuidv4().toLowerCase();
    }
}

export function deviceHeaders() {
    return {
        'X-Dashboard-Device': getDeviceId(),
    };
}

export function deviceIdentity(extra = {}) {
    return {
        device_id: getDeviceId(),
        display_name: (typeof navigator !== 'undefined' ? navigator.userAgent : '').slice(0, 80) || '',
        page: 'dashboard',
        ...extra,
    };
}

export function withDeviceId(payload = {}) {
    return {
        ...payload,
        device_id: payload.device_id || getDeviceId(),
    };
}
