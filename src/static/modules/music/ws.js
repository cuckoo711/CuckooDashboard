import { captureAndSendScreenshot } from '../shared/screenshot.js';
import { websocketUrl } from '../shared/websocket.js';
import { applyMusicOffsetData } from './controls.js';
import { applyStageFont } from './font.js';
import { applyLyricFrame } from './lyrics.js';
import { applyMedia } from './media.js';
import { applySpectrum, refreshVisualProfile } from './spectrum.js';
import { state } from './state.js';

function setConnBadge(text, className = '') {
    const element = document.getElementById('connBadge');
    if (!element) return;
    element.textContent = text;
    element.className = 'badge' + (className ? ' ' + className : '');
}

export function updateSpectrumSubscription(force = false) {
    const socket = state.ws;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    if (document.hidden) {
        if (state.spectrumSubscribed) {
            socket.send(JSON.stringify({ type: 'subscribe', channel: 'spectrum', active: false }));
            state.spectrumSubscribed = false;
        }
        return;
    }
    if (!state.visualProfile) {
        try { refreshVisualProfile(); } catch (_error) {}
    }
    const fps = state.visualProfile && state.visualProfile.spectrumFps || 24;
    if (!state.spectrumSubscribed || force) {
        socket.send(JSON.stringify({ type: 'subscribe', channel: 'spectrum', active: true, fps }));
        state.spectrumSubscribed = true;
    }
}

function handleMessage(event) {
    let message;
    try { message = JSON.parse(event.data); } catch (_error) { return; }
    if (!message || !message.type) return;
    if (message.type === 'reload') location.reload();
    else if (message.type === 'navigate') location.href = message.url || (message.page === 'music' ? '/music' : '/');
    else if (message.type === 'media') applyMedia(message.data || {});
    else if (message.type === 'lyric') applyLyricFrame(message.data || {});
    else if (message.type === 'spectrum') applySpectrum(message.data || {});
    else if (message.type === 'font') applyStageFont(message.data || {});
    else if (message.type === 'music_offset') applyMusicOffsetData(message.data || {});
    else if (message.type === 'screenshot') {
        captureAndSendScreenshot(message.request_id, () => state.ws);
    }
}

export function connectWs() {
    if (state.ws && (state.ws.readyState === WebSocket.CONNECTING || state.ws.readyState === WebSocket.OPEN)) return;
    const socket = new WebSocket(websocketUrl('/ws'));
    state.ws = socket;
    socket.onopen = () => {
        state.wsRetry = 0;
        if (state.wsReconnectTimer) clearTimeout(state.wsReconnectTimer);
        state.wsReconnectTimer = 0;
        setConnBadge('已连接');
        socket.send(JSON.stringify({ type: 'report', page: 'music' }));
        try {
            socket.send(JSON.stringify({
                type: 'subscribe',
                sources: ['media.playback'],
                replace: true,
            }));
        } catch (_error) {}
        try { socket.send(JSON.stringify({ type: 'subscribe', channel: 'lyric', active: true })); } catch (_error) {}
        updateSpectrumSubscription(true);
        socket.send(JSON.stringify({ type: 'init' }));
    };
    socket.onclose = () => {
        state.spectrumSubscribed = false;
        if (document.hidden) return;
        setConnBadge('重连中', 'warn');
        const delay = Math.min(8000, 800 + state.wsRetry * 700);
        state.wsRetry += 1;
        if (state.wsReconnectTimer) clearTimeout(state.wsReconnectTimer);
        state.wsReconnectTimer = setTimeout(() => {
            state.wsReconnectTimer = 0;
            connectWs();
        }, delay);
    };
    socket.onerror = () => setConnBadge('连接异常', 'bad');
    socket.onmessage = handleMessage;
}
