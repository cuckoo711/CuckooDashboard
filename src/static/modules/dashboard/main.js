import { initAppearance } from './appearance.js';
import { fetchWorkspaceManifest } from './api.js';
import { startClock } from './clock.js';
import { secureFetch, startConnectionMonitoring } from './connection.js';
import { handshakeDevice, showDeviceGate } from './device.js';
import { bindDashboardActions } from './events.js';
import { initLyrics } from './lyrics.js';
import { initNavigation } from './navigation.js';
import { applyVibeUi, initVibe } from './vibe.js';
import {
    startWebSocket,
    uninstallWorkspaceViewportReporter,
    updateWebSocketWorkspace,
} from './ws.js';
import { dashboardDataBus } from './workspace/data-bus.js';
import { DEFAULT_WORKSPACE_MANIFEST } from './workspace/default-manifest.js';
import { createWorkspaceHost } from './workspace/host.js';
import { loadRuntimeExtensions } from './workspace/extension-loader.js';
import { componentRegistry } from './workspace/registry.js';
import { dashboardSubscriptionClient } from './workspace/subscription-client.js';

let workspaceRequestSequence = 0;
let lyricsInitialized = false;
let vibeInitialized = false;
let websocketStarted = false;
let approvedWorkspaceId = null;

export function workspaceIdFromPath(pathname = window.location.pathname) {
    const normalized = String(pathname || '/');
    if (normalized === '/') return 'main';
    const match = /^\/workspaces\/([^/]+)\/?$/.exec(normalized);
    if (!match) return 'main';
    try { return decodeURIComponent(match[1]) || 'main'; }
    catch (_error) { return match[1] || 'main'; }
}

function showWorkspaceError(root, workspaceId, error) {
    root.dataset.workspaceError = 'true';
    let element = root.querySelector(':scope > .workspace-error');
    if (!element) {
        element = document.createElement('div');
        element.className = 'workspace-error';
        element.setAttribute('role', 'alert');
        const title = document.createElement('strong');
        title.textContent = '工作区加载失败';
        const detail = document.createElement('span');
        element.append(title, detail);
        root.appendChild(element);
    }
    const detail = element.querySelector('span');
    if (detail) detail.textContent = `无法加载工作区“${workspaceId}”：${error?.message || '未知错误'}`;
}

function clearWorkspaceError(root) {
    delete root.dataset.workspaceError;
    root.querySelector(':scope > .workspace-error')?.remove();
}

function applyDeviceScale(session) {
    if (!session?.approved) return;
    const scaleMode = session.scale_mode || 'auto';
    const scale = Number(session.scale || 1);
    document.documentElement.dataset.deviceScaleMode = scaleMode;
    if (scaleMode === 'fixed' && Number.isFinite(scale) && scale > 0) {
        document.documentElement.style.setProperty('--device-scale', String(scale));
        document.body.style.zoom = String(scale);
    } else {
        document.documentElement.style.removeProperty('--device-scale');
        document.body.style.zoom = '';
    }
}

function initializeMountedFeatures(summary) {
    const types = new Set(summary.widgetTypes || []);
    if (!lyricsInitialized && types.has('builtin.dashboard.player')) {
        lyricsInitialized = true;
        initLyrics();
    }
    if (types.has('builtin.dashboard.vibe')) {
        if (!vibeInitialized) {
            vibeInitialized = true;
            initVibe();
        } else {
            applyVibeUi();
        }
    }
}

async function fetchWorkspaceForMount(workspaceId) {
    try {
        return await fetchWorkspaceManifest(workspaceId);
    } catch (error) {
        if (workspaceId !== 'main') throw error;
        console.warn('[workspace] main manifest fetch failed, using local fallback:', error);
        return DEFAULT_WORKSPACE_MANIFEST;
    }
}

export async function reloadWorkspace(host, workspaceId) {
    const requestSequence = ++workspaceRequestSequence;
    try {
        const manifest = await fetchWorkspaceForMount(workspaceId);
        if (requestSequence !== workspaceRequestSequence) return null;
        let summary;
        try {
            summary = host.mount(manifest);
        } catch (error) {
            if (workspaceId !== 'main' || manifest === DEFAULT_WORKSPACE_MANIFEST) throw error;
            console.warn('[workspace] invalid main manifest, using local fallback:', error);
            summary = host.mount(DEFAULT_WORKSPACE_MANIFEST);
        }
        clearWorkspaceError(host.root);
        initializeMountedFeatures(summary);
        if (websocketStarted) {
            updateWebSocketWorkspace(summary.sources, summary.channels, workspaceId);
        }
        return summary;
    } catch (error) {
        if (requestSequence !== workspaceRequestSequence) return null;
        console.error(`[workspace] failed to load ${workspaceId}:`, error);
        showWorkspaceError(host.root, workspaceId, error);
        return host.summary();
    }
}

export async function bootstrapDashboard() {
    const root = document.getElementById('workspaceHost');
    if (!root) throw new Error('Dashboard workspace host is missing');

    const pathWorkspaceId = workspaceIdFromPath();
    let session;
    try {
        session = await handshakeDevice(secureFetch, {
            page: 'dashboard',
            viewport: {
                width: window.innerWidth || 1,
                height: window.innerHeight || 1,
            },
        });
    } catch (error) {
        console.error('[device] handshake failed:', error);
        showDeviceGate(root, { reason: 'device_pending' });
        return null;
    }
    if (!session?.approved) {
        showDeviceGate(root, session);
        return null;
    }

    applyDeviceScale(session);
    approvedWorkspaceId = String(session.workspace_id || pathWorkspaceId || 'main');
    const extensionSummary = await loadRuntimeExtensions({ registry: componentRegistry });
    if (extensionSummary.catalogError || extensionSummary.failed.length) {
        console.warn('[extensions] runtime loading completed with errors:', extensionSummary);
    }
    const host = createWorkspaceHost({
        root,
        registry: componentRegistry,
        bus: dashboardDataBus,
        subscriptions: dashboardSubscriptionClient,
    });
    const summary = await reloadWorkspace(host, approvedWorkspaceId);

    bindDashboardActions();
    startConnectionMonitoring();
    startClock();
    initAppearance();
    initNavigation();
    startWebSocket(
        summary.sources,
        dashboardDataBus,
        summary.channels,
        approvedWorkspaceId,
        () => reloadWorkspace(host, approvedWorkspaceId),
        dashboardSubscriptionClient,
    );
    websocketStarted = true;
    window.addEventListener('pagehide', () => {
        uninstallWorkspaceViewportReporter();
        host.destroy();
    }, { once: true });
    return host;
}

bootstrapDashboard().catch((error) => console.error('[dashboard] bootstrap failed:', error));
