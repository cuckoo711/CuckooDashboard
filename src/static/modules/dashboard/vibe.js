import { fetchDashboardData, fetchVibe, postVibe } from './api.js';
import { handleDashboardData } from './render-dashboard.js';
import { state } from './state.js';

export function applyVibeUi() {
    const toggle = document.getElementById('vibeToggle');
    const label = document.getElementById('vibeToggleLabel');
    if (!toggle || !label) return;
    toggle.classList.toggle('active', state.vibe.active);
    label.textContent = state.vibe.active ? 'Coding' : 'Chilling';
}

function storeVibeState() {
    try { localStorage.setItem('vibeActive', state.vibe.active ? '1' : '0'); } catch (_error) {}
}

function sendVibeState() {
    const socket = state.websocket.socket;
    if (socket && socket.readyState === WebSocket.OPEN) {
        try {
            socket.send(JSON.stringify({ type: 'vibe', active: state.vibe.active }));
            return;
        } catch (_error) {}
    }
    postVibe(state.vibe.active).catch((error) => console.error('[vibe] REST sync failed:', error));
}

export async function toggleVibe() {
    state.vibe.active = !state.vibe.active;
    storeVibeState();
    applyVibeUi();
    sendVibeState();
    try { handleDashboardData(await fetchDashboardData()); }
    catch (error) { console.error('Refresh error:', error); }
}

export function applyServerVibeState(data = {}) {
    state.vibe.active = !!data.active;
    state.vibe.syncedFromServer = true;
    storeVibeState();
    applyVibeUi();
}

async function fetchVibeFromServer() {
    if (state.vibe.syncedFromServer) return;
    try {
        const data = await fetchVibe();
        if (state.vibe.syncedFromServer) return;
        applyServerVibeState(data);
    } catch (error) {
        console.error('[vibe] REST fetch failed:', error);
    }
}

export function initVibe() {
    try { state.vibe.active = localStorage.getItem('vibeActive') === '1'; }
    catch (_error) { state.vibe.active = false; }
    applyVibeUi();
    fetchVibeFromServer();
}
