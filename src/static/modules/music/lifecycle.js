import { playerControl, secureFetch } from './api.js';
import {
    bindActionControls,
    configureControls,
    loadOffsets,
    setupMenu,
    updateStageClock,
} from './controls.js';
import { loadStageFont } from './font.js';
import { startFrameLoop, stopFrameLoop } from './frame-loop.js';
import { applyMedia } from './media.js';
import { loadOrbit, nudgeOrbit, resetOrbit, setupOrbitControls } from './orbit.js';
import { applySpectrum, resetSpectrumRendering, resizeCanvases } from './spectrum.js';
import { ORBIT, state } from './state.js';
import { connectWs, updateSpectrumSubscription } from './ws.js';

function finishStageBoot() {
    if (state.heavyStageReady || document.hidden) return;
    state.heavyStageReady = true;
    resizeCanvases();
    startFrameLoop();
    if (state.ws && state.ws.readyState === WebSocket.OPEN) updateSpectrumSubscription(true);
    else connectWs();
    document.body.classList.remove('stage-booting');
    document.body.classList.add('stage-ready');
}

function handleStageVisibilityChange() {
    if (document.hidden) {
        stopFrameLoop();
        return;
    }
    if (state.heavyStageReady) startFrameLoop();
    if (state.ws && state.ws.readyState === WebSocket.OPEN) updateSpectrumSubscription(true);
    else connectWs();
}

function handleKeyboard(event) {
    if (event.key === 'Escape' || event.code === 'Escape') {
        const panel = document.getElementById('menuPanel');
        if (panel && !panel.hidden) panel.hidden = true;
        const active = document.activeElement;
        if (active && active !== document.body && active !== document.documentElement && typeof active.blur === 'function') active.blur();
        event.preventDefault();
        return;
    }
    if (event.target && /input|textarea/i.test(event.target.tagName)) return;
    if (!event.shiftKey && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.code)) {
        event.preventDefault();
        if (event.code === 'ArrowLeft') nudgeOrbit(-ORBIT.step, 0);
        if (event.code === 'ArrowRight') nudgeOrbit(ORBIT.step, 0);
        if (event.code === 'ArrowUp') nudgeOrbit(0, -ORBIT.step);
        if (event.code === 'ArrowDown') nudgeOrbit(0, ORBIT.step);
        return;
    }
    if (event.code === 'Space') {
        event.preventDefault();
        playerControl('toggle');
    } else if (event.shiftKey && event.code === 'ArrowRight') playerControl('next');
    else if (event.shiftKey && event.code === 'ArrowLeft') playerControl('prev');
    else if (event.key === 'r' || event.key === 'R') resetOrbit();
}

function startRestFallback() {
    setInterval(() => {
        if (document.hidden) return;
        const websocketLive = state.ws && state.ws.readyState === WebSocket.OPEN;
        if (!websocketLive) {
            secureFetch('/api/media')
                .then((response) => response.json())
                .then(applyMedia)
                .catch(() => {});
        }
        if (!state.heavyStageReady) return;
        if (!websocketLive || (!state.specAvailable && !state.lastSpectrumSourceTs)) {
            if (websocketLive) updateSpectrumSubscription(true);
            secureFetch('/api/music/spectrum')
                .then((response) => response.json())
                .then(applySpectrum)
                .catch(() => {});
        }
    }, 1000);
}

export function bootstrapMusicStage() {
    configureControls({
        onRenderOptionsChanged: () => {
            resetSpectrumRendering();
            updateSpectrumSubscription(true);
        },
    });
    document.body.classList.add('stage-booting');
    bindActionControls();
    setupMenu();
    loadOffsets();
    loadOrbit();
    setupOrbitControls();
    loadStageFont();

    window.addEventListener('resize', resizeCanvases);
    document.addEventListener('visibilitychange', handleStageVisibilityChange);
    window.addEventListener('pagehide', stopFrameLoop);
    window.addEventListener('beforeunload', stopFrameLoop);
    document.addEventListener('keydown', handleKeyboard);

    updateStageClock();
    setInterval(updateStageClock, 1000);
    connectWs();
    requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(finishStageBoot, 40)));
    startRestFallback();
}
