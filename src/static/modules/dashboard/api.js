import { secureFetch } from './connection.js';

async function fetchJson(url, options) {
    const response = await secureFetch(url, options);
    return response.json();
}

export const fetchDashboardData = () => fetchJson('/api/data');
export const fetchWorkspaceManifest = () => fetchJson('/api/workspaces/main');
export const fetchSystem = () => fetchJson('/api/system');
export const fetchMedia = () => fetchJson('/api/media');
export const fetchTheme = () => fetchJson('/api/theme');
export const fetchNextTheme = () => fetchJson('/api/theme/next', { method: 'POST' });
export const fetchFont = () => fetchJson('/api/font');
export const fetchVibe = () => fetchJson('/api/vibe');
export async function fetchOffPeakBadge() {
    const response = await secureFetch('/api/off-peak-badge');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}
export const fetchLyricOffset = () => fetchJson('/api/media/offset');

export function postLyricOffset(delta) {
    return fetchJson('/api/media/offset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ delta }),
    });
}

export function postVibe(active) {
    return secureFetch('/api/vibe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ active }),
    });
}
