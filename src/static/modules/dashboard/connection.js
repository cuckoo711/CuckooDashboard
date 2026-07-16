import { createSecureFetch } from '../shared/http.js';
import { state } from './state.js';

export function updateLatency(ms) {
    state.websocket.latency = ms;
    const wrap = document.getElementById('hdrLatency');
    if (!wrap) return;
    const value = wrap.querySelector('.latency-val');
    if (ms < 0) {
        wrap.className = 'hdr-latency disconnected';
        if (value) value.textContent = '离线';
        return;
    }
    wrap.className = `hdr-latency ${ms < 20 ? 'good' : ms < 60 ? 'ok' : ms < 150 ? 'warn' : 'bad'}`;
    if (value) value.textContent = `${ms < 1 ? ms.toFixed(2) : ms < 10 ? ms.toFixed(1) : Math.round(ms)}ms`;
}

function applyConnectionUi() {
    document.body.classList.toggle('net-offline', !state.connection.online);
    const banner = document.getElementById('netBanner');
    if (banner) banner.classList.toggle('show', !state.connection.online);
    if (!state.connection.online) updateLatency(-1);
}

function goOffline() {
    if (!state.connection.online) return;
    state.connection.online = false;
    applyConnectionUi();
    console.warn('[conn] offline');
}

function goOnline() {
    if (state.connection.online) return;
    state.connection.online = true;
    applyConnectionUi();
    console.log('[conn] online');
}

export function noteAlive() {
    state.connection.lastAliveTs = Date.now();
    state.connection.restFailStreak = 0;
    if (!state.connection.online) goOnline();
}

function noteRestFail() {
    state.connection.restFailStreak += 1;
}

export const secureFetch = createSecureFetch({
    onResponse: noteAlive,
    onNetworkError: noteRestFail,
});

function updateVibeHealthDot() {
    const element = document.getElementById('dot-vibe');
    if (!element) return;
    const status = (state.health.services[state.health.vibeStatusProvider] || {}).status || 'unknown';
    element.className = `svc-dot ${status}`;
}

export function setVibeStatusProvider(provider) {
    state.health.vibeStatusProvider = provider || null;
    updateVibeHealthDot();
}

export function updateHealthDots(health = {}) {
    state.health.services = health.services || {};
    for (const service of ['system', 'github', 'media']) {
        const element = document.getElementById(`dot-${service}`);
        if (!element) continue;
        const status = (state.health.services[service] || {}).status || 'unknown';
        element.className = `svc-dot ${status}`;
    }
    updateVibeHealthDot();
    const hint = document.getElementById('ghHint');
    if (hint) hint.classList.toggle('visible', !!state.health.services.github?.details?.estimated);
}

export async function refreshHealth() {
    try {
        const response = await secureFetch('/api/health');
        updateHealthDots(await response.json());
    } catch (_error) {}
}

function connectionWatchdog() {
    const connection = state.connection;
    if (connection.online
        && (Date.now() - connection.lastAliveTs > connection.staleMs
            || connection.restFailStreak >= connection.failStreak)) {
        goOffline();
    }
}

export function startConnectionMonitoring() {
    refreshHealth();
    state.timers.health = setInterval(refreshHealth, 15000);
    state.timers.connectionWatchdog = setInterval(connectionWatchdog, 1000);
}
