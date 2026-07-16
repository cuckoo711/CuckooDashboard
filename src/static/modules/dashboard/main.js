import { initAppearance } from './appearance.js';
import { fetchWorkspaceManifest } from './api.js';
import { startClock } from './clock.js';
import { startConnectionMonitoring } from './connection.js';
import { bindDashboardActions } from './events.js';
import { initLyrics } from './lyrics.js';
import { initNavigation } from './navigation.js';
import { initVibe } from './vibe.js';
import { startWebSocket } from './ws.js';
import { dashboardDataBus } from './workspace/data-bus.js';
import { DEFAULT_WORKSPACE_MANIFEST } from './workspace/default-manifest.js';
import { createWorkspaceHost } from './workspace/host.js';
import { componentRegistry } from './workspace/registry.js';

async function loadWorkspaceManifest() {
    try {
        return await fetchWorkspaceManifest();
    } catch (error) {
        console.warn('[workspace] manifest fetch failed, using local fallback:', error);
        return DEFAULT_WORKSPACE_MANIFEST;
    }
}

export async function bootstrapDashboard() {
    const root = document.getElementById('workspaceHost');
    if (!root) throw new Error('Dashboard workspace host is missing');
    const host = createWorkspaceHost({ root, registry: componentRegistry, bus: dashboardDataBus });
    let manifest = await loadWorkspaceManifest();
    let summary;
    try {
        summary = host.mount(manifest);
    } catch (error) {
        console.error('[workspace] invalid remote manifest, using local fallback:', error);
        manifest = DEFAULT_WORKSPACE_MANIFEST;
        summary = host.mount(manifest);
    }

    bindDashboardActions();
    startConnectionMonitoring();
    startClock();
    if (summary.channels.includes('media.lyric')) initLyrics();
    initVibe();
    initAppearance();
    initNavigation();
    startWebSocket(summary.sources, dashboardDataBus, summary.channels);
    window.addEventListener('pagehide', () => host.destroy(), { once: true });
    return host;
}

bootstrapDashboard().catch((error) => console.error('[dashboard] bootstrap failed:', error));
