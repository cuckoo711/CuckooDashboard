import { captureAndSendScreenshot } from '../shared/screenshot.js';
import { websocketUrl } from '../shared/websocket.js';
import { fetchDashboardData, fetchMedia, fetchSystem } from './api.js';
import { applyFont, applyTheme } from './appearance.js';
import { refreshOffPeakBadgeConfig } from './clock.js';
import { noteAlive, refreshHealth, updateLatency } from './connection.js';
import { applyLyricFrame } from './lyrics.js';
import { navigatePage, softReload } from './navigation.js';
import { handleDashboardData } from './render-dashboard.js';
import { state } from './state.js';
import { applyServerVibeState } from './vibe.js';
import { dashboardDataBus } from './workspace/data-bus.js';

const LEGACY_SOURCE_BY_TYPE = Object.freeze({
    system: 'system.snapshot',
    media: 'media.playback',
    github: 'github.contributions',
    dashboard_data: 'dashboard.aggregate',
});

const LEGACY_CHANNEL_BY_ID = Object.freeze({
    'media.lyric': 'lyric',
});

let activeSources = [];
let activeChannels = [];
let activeBus = dashboardDataBus;
let fallbackTicks = 0;

function normalizeSources(sources) {
    return [...new Set((sources || []).filter((source) => typeof source === 'string' && source))];
}

async function refreshSystem() {
    try { activeBus.publish('system.snapshot', await fetchSystem()); }
    catch (error) { console.error('Sys refresh error:', error); }
}

async function refreshMediaFallback() {
    try { activeBus.publish('media.playback', await fetchMedia()); }
    catch (error) { console.error('Media error:', error); }
}

async function refreshDashboardFallback() {
    try {
        const data = await fetchDashboardData();
        handleDashboardData(data);
        activeBus.publish('dashboard.aggregate', data);
        if (data?.github) activeBus.publish('github.contributions', data.github);
    } catch (error) {
        console.error('Dashboard refresh error:', error);
    }
}

export function routeMessage(message, bus = activeBus) {
    const source = LEGACY_SOURCE_BY_TYPE[message.type];
    if (source) {
        if (message.type === 'dashboard_data') {
            handleDashboardData(message.data);
            bus.publish(source, message.data);
        } else {
            bus.publish(source, message.data);
        }
        if (message.type === 'dashboard_data' && message.data?.github) {
            bus.publish('github.contributions', message.data.github);
        }
        if (message.type === 'dashboard_data') refreshHealth();
        return;
    }
    switch (message.type) {
        case 'lyric':
            applyLyricFrame(message.data || {});
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
    fallbackTicks = 0;
    refreshSystem();
    refreshMediaFallback();
    refreshDashboardFallback();
    state.websocket.fallbackTimer = setInterval(() => {
        refreshSystem();
        refreshMediaFallback();
        fallbackTicks += 1;
        if (fallbackTicks % 5 === 0) refreshDashboardFallback();
    }, 2000);
}

function stopRestFallback() {
    if (!state.websocket.fallbackTimer) return;
    clearInterval(state.websocket.fallbackTimer);
    state.websocket.fallbackTimer = null;
}

export function connectWebSocket(
    sources = activeSources,
    bus = activeBus,
    channels = activeChannels,
) {
    activeSources = normalizeSources(sources);
    activeChannels = normalizeSources(channels);
    activeBus = bus || dashboardDataBus;
    const socket = new WebSocket(websocketUrl('/ws'));
    state.websocket.socket = socket;
    socket.onopen = () => {
        state.websocket.retry = 1000;
        stopRestFallback();
        noteAlive();
        try {
            socket.send(JSON.stringify({ type: 'subscribe', sources: activeSources, replace: true }));
            socket.send(JSON.stringify({ type: 'report', page: 'dashboard' }));
            activeChannels.forEach((channelId) => {
                const channel = LEGACY_CHANNEL_BY_ID[channelId];
                if (channel) socket.send(JSON.stringify({ type: 'subscribe', channel, active: true }));
            });
        } catch (_error) {}
        console.log('[ws] connected');
    };
    socket.onmessage = (event) => {
        noteAlive();
        try { routeMessage(JSON.parse(event.data), activeBus); }
        catch (error) { console.error('[ws] parse error:', error); }
    };
    socket.onclose = () => {
        updateLatency(-1);
        console.log(`[ws] disconnected, retry in ${state.websocket.retry / 1000}s`);
        clearTimeout(state.websocket.reconnectTimer);
        state.websocket.reconnectTimer = setTimeout(() => connectWebSocket(), state.websocket.retry);
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

export function startWebSocket(sources = [], bus = dashboardDataBus, channels = []) {
    activeSources = normalizeSources(sources);
    activeChannels = normalizeSources(channels);
    activeBus = bus;
    connectWebSocket();
    state.timers.ping = setInterval(sendPing, 5000);
    sendPing();
}
