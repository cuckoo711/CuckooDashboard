import { captureAndSendScreenshot } from '../shared/screenshot.js';
import { websocketUrl } from '../shared/websocket.js';
import { fetchDashboardData, fetchMedia, fetchSystem } from './api.js';
import { applyFont, applyTheme } from './appearance.js';
import { refreshOffPeakBadgeConfig } from './clock.js';
import { noteAlive, refreshHealth, updateLatency } from './connection.js';
import { getDeviceId } from './device.js';
import { navigatePage, softReload } from './navigation.js';
import { state } from './state.js';
import { applyServerVibeState } from './vibe.js';
import { dashboardDataBus } from './workspace/data-bus.js';
import { dashboardSubscriptionClient } from './workspace/subscription-client.js';

const LEGACY_SOURCE_BY_TYPE = Object.freeze({
    system: 'system.snapshot',
    media: 'media.playback',
    github: 'github.contributions',
    dashboard_data: 'dashboard.aggregate',
});

let activeSources = [];
let activeChannels = [];
let activeBus = dashboardDataBus;
let activeSubscriptionClient = dashboardSubscriptionClient;
let activeWorkspaceId = 'main';
let workspaceUpdateHandler = null;
let fallbackTicks = 0;
let viewportObserver = null;
let viewportResizeHandler = null;
let visualViewportResizeHandler = null;
let viewportReportFrame = 0;

function normalizeSources(sources) {
    return [...new Set((sources || []).filter((source) => typeof source === 'string' && source))];
}

function finiteCssNumber(value, fallback = 1) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric) || numeric <= 0) return fallback;
    return Math.round(numeric * 1000) / 1000;
}

export function getWorkspaceViewport() {
    const browserWindow = typeof window === 'undefined' ? null : window;
    const browserDocument = typeof document === 'undefined' ? null : document;
    const host = browserDocument?.getElementById?.('workspaceHost') || null;
    const hostRect = host?.getBoundingClientRect?.() || null;
    const viewportWidth = finiteCssNumber(
        browserWindow?.innerWidth,
        finiteCssNumber(browserDocument?.documentElement?.clientWidth, 1),
    );
    const viewportHeight = finiteCssNumber(
        browserWindow?.innerHeight,
        finiteCssNumber(browserDocument?.documentElement?.clientHeight, 1),
    );
    const workspaceWidth = finiteCssNumber(
        hostRect?.width,
        finiteCssNumber(host?.clientWidth, viewportWidth),
    );
    const workspaceHeight = finiteCssNumber(
        hostRect?.height,
        finiteCssNumber(host?.clientHeight, viewportHeight),
    );
    return {
        width: viewportWidth,
        height: viewportHeight,
        workspace_width: workspaceWidth,
        workspace_height: workspaceHeight,
        device_pixel_ratio: finiteCssNumber(browserWindow?.devicePixelRatio, 1),
        visual_viewport_scale: finiteCssNumber(browserWindow?.visualViewport?.scale, 1),
    };
}

function usesSource(source) {
    return activeSources.includes(source);
}

function publishSource(channel, data, meta = {}, bus = activeBus, subscriptions = activeSubscriptionClient) {
    if (subscriptions?.bus === bus) return subscriptions.routeLegacy(channel, data, meta);
    return bus.publish(channel, data, meta);
}

function publishDashboardAggregate(data, bus = activeBus, subscriptions = activeSubscriptionClient) {
    publishSource('dashboard.aggregate', data, { legacyType: 'dashboard_data' }, bus, subscriptions);
    if (data?.github) {
        publishSource('github.contributions', data.github, {
            legacyType: 'dashboard_data',
            derivedFrom: 'dashboard.aggregate',
        }, bus, subscriptions);
    }
}

async function refreshSystem() {
    if (!usesSource('system.snapshot')) return;
    try { publishSource('system.snapshot', await fetchSystem(), { delivery: 'rest' }); }
    catch (error) { console.error('Sys refresh error:', error); }
}

async function refreshMediaFallback() {
    if (!usesSource('media.playback')) return;
    try { publishSource('media.playback', await fetchMedia(), { delivery: 'rest' }); }
    catch (error) { console.error('Media error:', error); }
}

async function refreshDashboardFallback() {
    if (!usesSource('dashboard.aggregate') && !usesSource('github.contributions')) return;
    try {
        publishDashboardAggregate(await fetchDashboardData());
    } catch (error) {
        console.error('Dashboard refresh error:', error);
    }
}

export function routeMessage(message, bus = activeBus, subscriptions = activeSubscriptionClient) {
    if (!message || typeof message !== 'object') return;
    if (message.type === 'data.snapshot') {
        subscriptions.routeSnapshot(message);
        return;
    }
    const source = LEGACY_SOURCE_BY_TYPE[message.type];
    if (source) {
        if (message.type === 'dashboard_data') publishDashboardAggregate(message.data, bus, subscriptions);
        else publishSource(source, message.data, { legacyType: message.type }, bus, subscriptions);
        if (message.type === 'dashboard_data') refreshHealth();
        return;
    }
    switch (message.type) {
        case 'workspace_source':
            if (typeof message.source_id === 'string' && message.source_id) {
                publishSource(message.source_id, message.data, { legacyType: message.type }, bus, subscriptions);
            }
            break;
        case 'lyric':
            publishSource('media.lyric', message.data || {}, { legacyType: message.type }, bus, subscriptions);
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
        case 'workspace_updated': {
            const update = { ...message, ...(message.data || {}) };
            if (String(update.workspace_id || '') === activeWorkspaceId) {
                reconcileWorkspace('workspace_updated', update);
            }
            break;
        }
        case 'device_status':
        case 'device_updated':
            if (message.data && message.data.approved === false) {
                window.location.reload();
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

export function sendWorkspaceViewport(socket = state.websocket.socket) {
    const openState = typeof WebSocket === 'undefined' ? 1 : WebSocket.OPEN;
    if (socket?.readyState !== openState) return false;
    try {
        socket.send(JSON.stringify({
            type: 'report',
            page: 'dashboard',
            workspace_id: activeWorkspaceId,
            device_id: getDeviceId(),
            viewport: getWorkspaceViewport(),
        }));
        return true;
    } catch (_error) {
        return false;
    }
}

function scheduleWorkspaceViewportReport() {
    if (viewportReportFrame) return;
    const flush = () => {
        viewportReportFrame = 0;
        sendWorkspaceViewport();
    };
    if (typeof requestAnimationFrame === 'function') {
        viewportReportFrame = requestAnimationFrame(flush);
    } else {
        viewportReportFrame = setTimeout(flush, 0);
    }
}

function installWorkspaceViewportReporter() {
    if (viewportObserver || typeof window === 'undefined') return;
    const host = document.getElementById('workspaceHost');
    if (typeof ResizeObserver === 'function' && host) {
        viewportObserver = new ResizeObserver(scheduleWorkspaceViewportReport);
        viewportObserver.observe(host);
    }
    viewportResizeHandler = scheduleWorkspaceViewportReport;
    window.addEventListener('resize', viewportResizeHandler, {passive: true});
    const visualViewport = window.visualViewport;
    if (visualViewport?.addEventListener) {
        visualViewportResizeHandler = scheduleWorkspaceViewportReport;
        visualViewport.addEventListener('resize', visualViewportResizeHandler, {passive: true});
    }
}

export function uninstallWorkspaceViewportReporter() {
    viewportObserver?.disconnect?.();
    viewportObserver = null;
    if (typeof window !== 'undefined' && viewportResizeHandler) {
        window.removeEventListener('resize', viewportResizeHandler);
    }
    if (typeof window !== 'undefined' && visualViewportResizeHandler) {
        window.visualViewport?.removeEventListener?.('resize', visualViewportResizeHandler);
    }
    viewportResizeHandler = null;
    visualViewportResizeHandler = null;
    if (viewportReportFrame) {
        if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(viewportReportFrame);
        else clearTimeout(viewportReportFrame);
        viewportReportFrame = 0;
    }
}

function sendWorkspaceState(socket) {
    activeSubscriptionClient.attach(socket, { replay: false });
    activeSubscriptionClient.sendReplace();
    sendWorkspaceViewport(socket);
    try {
        socket.send(JSON.stringify({
            type: 'init',
            device_id: getDeviceId(),
            page: 'dashboard',
            workspace_id: activeWorkspaceId,
        }));
    } catch (_error) {}
}

export function setWorkspaceUpdateHandler(handler) {
    workspaceUpdateHandler = typeof handler === 'function' ? handler : null;
}

function reconcileWorkspace(reason, update = {}) {
    if (!workspaceUpdateHandler) return;
    Promise.resolve(workspaceUpdateHandler({
        workspace_id: activeWorkspaceId,
        reason,
        ...update,
    })).catch((error) => {
        console.error(`[workspace] reconciliation failed (${reason}):`, error);
    });
}

export function updateWebSocketWorkspace(sources = [], channels = [], workspaceId = activeWorkspaceId) {
    activeSources = normalizeSources(sources);
    activeChannels = normalizeSources(channels);
    activeWorkspaceId = String(workspaceId || 'main');
    const socket = state.websocket.socket;
    if (socket?.readyState !== WebSocket.OPEN) return;
    try {
        sendWorkspaceState(socket);
        socket.send(JSON.stringify({ type: 'init' }));
    } catch (_error) {}
}

export function connectWebSocket(
    sources = activeSources,
    bus = activeBus,
    channels = activeChannels,
    workspaceId = activeWorkspaceId,
) {
    activeSources = normalizeSources(sources);
    activeChannels = normalizeSources(channels);
    activeBus = bus || dashboardDataBus;
    activeWorkspaceId = String(workspaceId || 'main');
    const socket = new WebSocket(websocketUrl('/ws'));
    state.websocket.socket = socket;
    socket.onopen = () => {
        state.websocket.retry = 1000;
        stopRestFallback();
        noteAlive();
        try {
            sendWorkspaceState(socket);
            socket.send(JSON.stringify({ type: 'init' }));
        } catch (_error) {}
        reconcileWorkspace('websocket_open');
        console.log('[ws] connected');
    };
    socket.onmessage = (event) => {
        noteAlive();
        try { routeMessage(JSON.parse(event.data), activeBus); }
        catch (error) { console.error('[ws] parse error:', error); }
    };
    socket.onclose = () => {
        activeSubscriptionClient.detach(socket);
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

export function startWebSocket(
    sources = [],
    bus = dashboardDataBus,
    channels = [],
    workspaceId = 'main',
    onWorkspaceUpdated = null,
    subscriptions = dashboardSubscriptionClient,
) {
    activeSources = normalizeSources(sources);
    activeChannels = normalizeSources(channels);
    activeBus = bus;
    activeSubscriptionClient = subscriptions || dashboardSubscriptionClient;
    activeWorkspaceId = String(workspaceId || 'main');
    setWorkspaceUpdateHandler(onWorkspaceUpdated);
    installWorkspaceViewportReporter();
    connectWebSocket();
    state.timers.ping = setInterval(sendPing, 5000);
    sendPing();
}
