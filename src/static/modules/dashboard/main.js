import { initAppearance } from './appearance.js';
import { fetchWorkspaceManifest } from './api.js';
import { startClock } from './clock.js';
import { startConnectionMonitoring } from './connection.js';
import { bindDashboardActions } from './events.js';
import { initLyrics } from './lyrics.js';
import { initNavigation } from './navigation.js';
import { applyVibeUi, initVibe } from './vibe.js';
import { startWebSocket, updateWebSocketWorkspace } from './ws.js';
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
    const workspaceId = workspaceIdFromPath();
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
    const summary = await reloadWorkspace(host, workspaceId);

    bindDashboardActions();
    startConnectionMonitoring();
    startClock();
    initAppearance();
    initNavigation();
    startWebSocket(
        summary.sources,
        dashboardDataBus,
        summary.channels,
        workspaceId,
        () => reloadWorkspace(host, workspaceId),
        dashboardSubscriptionClient,
    );
    websocketStarted = true;
    window.addEventListener('pagehide', () => host.destroy(), { once: true });
    return host;
}

bootstrapDashboard().catch((error) => console.error('[dashboard] bootstrap failed:', error));
