import { getDeviceId } from './device-id.js';

export function readDashboardToken() {
    try { return localStorage.getItem('dashboardToken') || ''; }
    catch (_error) { return ''; }
}

export function readDashboardDeviceId() {
    return getDeviceId();
}

export function createSecureFetch(options = {}) {
    const header = options.header || 'CuckooDashboard';
    const onResponse = typeof options.onResponse === 'function' ? options.onResponse : null;
    const onNetworkError = typeof options.onNetworkError === 'function' ? options.onNetworkError : null;

    return function secureFetch(url, requestOptions = {}) {
        const next = { ...requestOptions };
        const headers = new Headers(requestOptions.headers || {});
        headers.set('X-Requested-With', header);
        const token = readDashboardToken();
        if (token) headers.set('X-Dashboard-Token', token);
        const deviceId = readDashboardDeviceId();
        if (deviceId) headers.set('X-Dashboard-Device', deviceId);
        next.headers = headers;
        return fetch(url, next).then(
            (response) => {
                if (onResponse) onResponse(response);
                return response;
            },
            (error) => {
                if (onNetworkError) onNetworkError(error);
                throw error;
            },
        );
    };
}
