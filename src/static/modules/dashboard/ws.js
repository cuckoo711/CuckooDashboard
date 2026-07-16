import { captureAndSendScreenshot } from '../shared/screenshot.js';
import { websocketUrl } from '../shared/websocket.js';
import { fetchMedia, fetchSystem } from './api.js';
import { applyFont, applyTheme } from './appearance.js';
import { refreshOffPeakBadgeConfig } from './clock.js';
import { noteAlive, refreshHealth, updateLatency } from './connection.js';
import { applyLyricFrame, drawLyric } from './lyrics.js';
import { navigatePage, softReload } from './navigation.js';
import { drawGitHub, drawSystem, handleDashboardData } from './render-dashboard.js';
import { state } from './state.js';
import { applyServerVibeState } from './vibe.js';

async function refreshSystem() {
    try { drawSystem(await fetchSystem()); }
    catch (error) { console.error('Sys refresh error:', error); }
}

async function refreshMediaFallback() {
    try { drawLyric(await fetchMedia()); }
    catch (error) { console.error('Media error:', error); }
}

export function routeMessage(message) {
    switch (message.type) {
        case 'system':
            drawSystem(message.data);
            break;
        case 'media':
            drawLyric(message.data);
            break;
        case 'lyric':
            applyLyricFrame(message.data || {});
            break;
        case 'github':
            drawGitHub(message.data?.contributions || {}, message.data?.user || '');
            break;
        case 'dashboard_data':
            handleDashboardData(message.data);
            refreshHealth();
            break;
        case 'reload':
            softReload();
            break;
        case 'navigate':
            navigatePage(message.url || (message.page === 'music' ? '/music' : '/'));
            break;
        case 'config_updated':
            refreshOffPeakBadgeConfig();
            if (state.websocket.socket?.readyState === WebSocket.OPEN) {
                try { state.websocket.socket.send(JSON.stringify({ type: 'init' })); } catch (_error) {}
            }
            break;
        case 'vibe_state':
            applyServerVibeState(message.data || {});
            break;
        case 'pong':
            updateLatency(performance.now() - message.ts);
            break;
        case 'theme':
            applyTheme(message.data);
            break;
        case 'font':
            applyFont(message.data);
            break;
        case 'screenshot':
            captureAndSendScreenshot(message.request_id, () => state.websocket.socket);
            break;
        default:
            break;
    }
}

function startRestFallback() {
    if (state.websocket.fallbackTimer) return;
    refreshSystem();
    refreshMediaFallback();
    state.websocket.fallbackTimer = setInterval(() => {
        refreshSystem();
        refreshMediaFallback();
    }, 2000);
}

function stopRestFallback() {
    if (!state.websocket.fallbackTimer) return;
    clearInterval(state.websocket.fallbackTimer);
    state.websocket.fallbackTimer = null;
}

export function connectWebSocket() {
    const socket = new WebSocket(websocketUrl('/ws'));
    state.websocket.socket = socket;
    socket.onopen = () => {
        state.websocket.retry = 1000;
        stopRestFallback();
        noteAlive();
        try {
            socket.send(JSON.stringify({ type: 'report', page: 'dashboard' }));
            socket.send(JSON.stringify({ type: 'subscribe', channel: 'lyric', active: true }));
        } catch (_error) {}
        console.log('[ws] connected');
    };
    socket.onmessage = (event) => {
        noteAlive();
        try { routeMessage(JSON.parse(event.data)); }
        catch (error) { console.error('[ws] parse error:', error); }
    };
    socket.onclose = () => {
        updateLatency(-1);
        console.log(`[ws] disconnected, retry in ${state.websocket.retry / 1000}s`);
        clearTimeout(state.websocket.reconnectTimer);
        state.websocket.reconnectTimer = setTimeout(connectWebSocket, state.websocket.retry);
        state.websocket.retry = Math.min(state.websocket.retry * 2, 30000);
        startRestFallback();
    };
}

export function sendPing() {
    const socket = state.websocket.socket;
    if (socket?.readyState !== WebSocket.OPEN) return;
    state.websocket.pingTs = performance.now();
    try { socket.send(JSON.stringify({ type: 'ping', ts: state.websocket.pingTs })); } catch (_error) {}
}

export function startWebSocket() {
    connectWebSocket();
    state.timers.ping = setInterval(sendPing, 5000);
    sendPing();
}
