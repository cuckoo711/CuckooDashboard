import { updateLyricLine } from './lyrics.js';
import { tickOrbit } from './orbit.js';
import { drawSpectrum } from './spectrum.js';
import { state } from './state.js';

export function startFrameLoop() {
    if (document.hidden || state.frameRequestId) return;
    state.lastStageTickAt = 0;
    state.lastSpectrumRenderAt = 0;
    state.lastVisualRenderAt = 0;
    state.frameRequestId = requestAnimationFrame(frameLoop);
}

export function stopFrameLoop() {
    if (state.frameRequestId) cancelAnimationFrame(state.frameRequestId);
    state.frameRequestId = 0;
}

function frameLoop(now) {
    state.frameRequestId = 0;
    if (document.hidden || !state.visualProfile) return;
    state.frameRequestId = requestAnimationFrame(frameLoop);
    now = now || performance.now();
    if (now - state.lastStageTickAt < state.visualProfile.stageTickFrameMs) return;
    state.lastStageTickAt = now;
    tickOrbit();
    if (now - state.lastLyricSyncAt >= 40) {
        updateLyricLine(false);
        state.lastLyricSyncAt = now;
    }
    if (now - state.lastSpectrumRenderAt >= state.visualProfile.spectrumFrameMs) {
        drawSpectrum();
        state.lastSpectrumRenderAt = now;
    }
}
