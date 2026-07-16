import { applyMusicOffsetData } from './controls.js';
import { state } from './state.js';

let fxCanvas = null;
let fxContext = null;
let spectrumCanvas = null;
let spectrumContext = null;
let ringCanvas = null;

function clampRenderOption(value, minimum, maximum) {
    const number = Math.round(Number(value || 0));
    if (!Number.isFinite(number) || number <= 0) return 0;
    return Math.max(minimum, Math.min(maximum, number));
}

function buildVisualProfile() {
    const dpr = Math.max(1, Number(window.devicePixelRatio || 1));
    const cores = Math.max(1, Number(navigator.hardwareConcurrency || 8));
    const memory = Math.max(1, Number(navigator.deviceMemory || 8));
    const reduceMotion = Boolean(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    const pixels = Math.max(1, window.innerWidth * window.innerHeight);
    const lite = reduceMotion || cores <= 4 || memory <= 4 || (dpr > 1.4 && pixels > 1200000);
    const manualFps = clampRenderOption(state.spectrumRenderFps, 12, 60);
    const manualBars = clampRenderOption(state.spectrumRenderBars, 12, 96);
    const spectrumFps = manualFps || (lite ? 24 : 60);
    const pixelRatio = lite ? 1 : Math.min(1.5, dpr);
    return {
        lite,
        pixelRatio,
        spectrumPixelRatio: lite ? 0.75 : pixelRatio,
        spectrumFps,
        spectrumBars: manualBars || 0,
        spectrumFrameMs: 1000 / spectrumFps,
        stageTickFrameMs: 1000 / spectrumFps,
        frameMs: lite ? Infinity : 50,
    };
}

export function invalidateSpectrumPaintCache() {
    state.spectrumPaintCache = null;
}

export function refreshVisualProfile() {
    state.visualProfile = buildVisualProfile();
    document.body.classList.toggle('performance-lite', state.visualProfile.lite);
    refreshRenderSpectrumBins();
    invalidateSpectrumPaintCache();
}

function setSpecBadge(text, className = '') {
    const key = text + '\u0000' + className;
    if (key === state.lastSpecBadgeKey) return;
    state.lastSpecBadgeKey = key;
    const element = document.getElementById('specBadge');
    if (!element) return;
    element.textContent = text;
    element.className = 'badge soft' + (className ? ' ' + className : '');
}

export function applySpectrum(data) {
    if (!data) return;
    if (data.offsets) applyMusicOffsetData(data.offsets);
    const sourceTimestamp = Number(data.ts || 0);
    if (sourceTimestamp && sourceTimestamp <= state.lastSpectrumSourceTs) return;
    if (sourceTimestamp) state.lastSpectrumSourceTs = sourceTimestamp;
    state.specAvailable = Boolean(data.available);
    state.specBins = data.bins || [];
    state.specPeaks = data.peaks || data.bins || [];
    refreshRenderSpectrumBins();
    state.specRms = Number(data.rms || 0);
    state.specBass = Number(data.bass || 0);
    state.specMid = Number(data.mid || 0);
    state.specHigh = Number(data.high || 0);
    state.specOnset = Number(data.onset || 0);
    state.specEnergy = Number(data.energy || state.specRms || 0);
    state.specRawRms = Number(data.raw_rms || 0);
    state.specSilent = Boolean(data.silent)
        || (state.specAvailable && state.specRawRms > 0 && state.specRawRms < 0.004 && state.specEnergy < 0.03);
    if (!state.specAvailable) {
        if (data.enabled === false) setSpecBadge('频谱关闭', 'warn');
        else if (data.error) setSpecBadge('频谱不可用', 'bad');
        else setSpecBadge('频谱待机');
    } else if (state.specSilent) setSpecBadge('静音/无输出', 'warn');
    else setSpecBadge('Loopback 频谱');
    if (data.beat && !state.specSilent) {
        state.pendingBeats.push(performance.now() + Math.max(0, state.spectrumOffsetMs + state.beatLeadMs));
    }
}

function triggerBeat() {
    state.lastBeatAt = performance.now();
    state.pulse = 1;
    state.cameraPunch = Math.min(1, state.cameraPunch + 0.35);
    document.body.classList.add('beat');
    setTimeout(() => document.body.classList.remove('beat'), 150);
}

export function processPendingBeats() {
    const now = performance.now();
    while (state.pendingBeats.length && state.pendingBeats[0] <= now) {
        state.pendingBeats.shift();
        triggerBeat();
    }
    const localBeat = (state.specBass > 0.42 && state.specOnset > 0.28)
        || (state.specEnergy > 0.55 && state.specOnset > 0.35);
    if (state.specAvailable && !state.specSilent && localBeat
        && now - state.lastBeatAt > 160
        && state.spectrumOffsetMs + state.beatLeadMs >= 0) {
        triggerBeat();
    }
}

function compactSpectrumBins(source, targetCount) {
    if (!source || !source.length) return [];
    const count = Math.max(1, Math.round(targetCount || source.length));
    if (count >= source.length) return source.slice();
    const result = new Array(count);
    for (let index = 0; index < count; index += 1) {
        const start = Math.floor(index * source.length / count);
        const end = Math.max(start + 1, Math.floor((index + 1) * source.length / count));
        let total = 0;
        for (let sourceIndex = start; sourceIndex < end; sourceIndex += 1) total += Number(source[sourceIndex] || 0);
        result[index] = total / (end - start);
    }
    return result;
}

export function refreshRenderSpectrumBins() {
    const available = state.specBins && state.specBins.length ? state.specBins.length : 0;
    let wanted = state.visualProfile && state.visualProfile.spectrumBars > 0
        ? state.visualProfile.spectrumBars
        : available || 48;
    if (!(state.visualProfile && state.visualProfile.spectrumBars > 0) && available > 0) wanted = available;
    if (available > 0) wanted = Math.min(wanted, available);
    state.renderSpecBins = compactSpectrumBins(state.specBins, wanted || available || 48);
    state.renderSpecPeaks = compactSpectrumBins(state.specPeaks, wanted || available || 48);
}

function ensureCanvases() {
    if (!fxCanvas) {
        fxCanvas = document.getElementById('fxCanvas');
        fxContext = fxCanvas ? fxCanvas.getContext('2d') : null;
        spectrumCanvas = document.getElementById('specCanvas');
        spectrumContext = spectrumCanvas ? spectrumCanvas.getContext('2d') : null;
        ringCanvas = document.getElementById('ringCanvas');
    }
}

export function resizeCanvases() {
    ensureCanvases();
    refreshVisualProfile();
    if (!spectrumCanvas || !spectrumContext) return;
    const renderRatio = state.visualProfile.pixelRatio;
    if (fxCanvas && fxContext) {
        fxCanvas.style.width = window.innerWidth + 'px';
        fxCanvas.style.height = window.innerHeight + 'px';
        if (state.visualProfile.lite) {
            fxCanvas.width = 1;
            fxCanvas.height = 1;
        } else {
            fxCanvas.width = Math.round(window.innerWidth * renderRatio);
            fxCanvas.height = Math.round(window.innerHeight * renderRatio);
            fxContext.setTransform(renderRatio, 0, 0, renderRatio, 0, 0);
        }
    }
    const parent = spectrumCanvas.parentElement;
    const rect = parent ? parent.getBoundingClientRect() : { width: window.innerWidth, height: 180 };
    state.specLogicalWidth = Math.max(320, Math.round(rect.width || 320));
    state.specLogicalHeight = Math.max(72, Math.round(rect.height || 110));
    const spectrumRatio = state.visualProfile.spectrumPixelRatio;
    spectrumCanvas.width = Math.max(1, Math.round(state.specLogicalWidth * spectrumRatio));
    spectrumCanvas.height = Math.max(1, Math.round(state.specLogicalHeight * spectrumRatio));
    spectrumCanvas.style.width = state.specLogicalWidth + 'px';
    spectrumCanvas.style.height = state.specLogicalHeight + 'px';
    spectrumContext.setTransform(spectrumRatio, 0, 0, spectrumRatio, 0, 0);
    invalidateSpectrumPaintCache();
    if (ringCanvas) {
        ringCanvas.width = 1;
        ringCanvas.height = 1;
    }
}

export function resetSpectrumRendering() {
    state.lastStageTickAt = 0;
    state.lastSpectrumRenderAt = 0;
    resizeCanvases();
}

function easeBins(source, destination, attack, release) {
    if (!source || !source.length) return destination || [];
    if (!destination || destination.length !== source.length) return source.slice();
    for (let index = 0; index < source.length; index += 1) {
        const target = source[index] || 0;
        const current = destination[index] || 0;
        destination[index] = target > current
            ? current * (1 - attack) + target * attack
            : current * (1 - release) + target * release;
    }
    return destination;
}

function ensureSpectrumPaintCache(width, height) {
    const key = [
        width,
        height,
        state.visualProfile.lite ? 1 : 0,
        state.coverTone.r,
        state.coverTone.g,
        state.coverTone.b,
        state.spectrumTone.r,
        state.spectrumTone.g,
        state.spectrumTone.b,
        state.blockTone.r,
        state.blockTone.g,
        state.blockTone.b,
    ].join(':');
    if (state.spectrumPaintCache && state.spectrumPaintCache.key === key) return state.spectrumPaintCache;
    const ink = {
        r: Math.round(state.spectrumTone.r),
        g: Math.round(state.spectrumTone.g),
        b: Math.round(state.spectrumTone.b),
    };
    const color = (alpha, lift = 0) => `rgba(${Math.round(ink.r + (236 - ink.r) * lift * 0.38)},${Math.round(ink.g + (242 - ink.g) * lift * 0.38)},${Math.round(ink.b + (255 - ink.b) * lift * 0.38)},${alpha})`;
    const inverse = {
        r: Math.round(state.blockTone.r),
        g: Math.round(state.blockTone.g),
        b: Math.round(state.blockTone.b),
    };
    const inverseColor = (alpha, lift = 0) => `rgba(${Math.round(inverse.r + (255 - inverse.r) * lift * 0.35)},${Math.round(inverse.g + (255 - inverse.g) * lift * 0.35)},${Math.round(inverse.b + (255 - inverse.b) * lift * 0.35)},${alpha})`;
    const cache = {
        key,
        barSolid: color(0.84, 0.38),
        peak: inverseColor(0.96, 0.18),
        capGlow: inverseColor(0.55, 0.45),
        line: color(0.96, 0.96),
        baseSolid: color(0.68, 0.6),
    };
    if (!state.visualProfile.lite) {
        const horizon = height * 0.9;
        cache.barFill = spectrumContext.createLinearGradient(0, height * 0.06, 0, horizon);
        cache.barFill.addColorStop(0, color(0.94, 0.64));
        cache.barFill.addColorStop(0.55, color(0.64, 0.28));
        cache.barFill.addColorStop(1, color(0.16, 0.03));
        cache.fieldFill = spectrumContext.createLinearGradient(0, height * 0.05, 0, horizon);
        cache.fieldFill.addColorStop(0, color(0, 0.38));
        cache.fieldFill.addColorStop(0.7, color(0.2, 0.14));
        cache.fieldFill.addColorStop(1, color(0.03, 0));
        cache.baseFill = spectrumContext.createLinearGradient(0, horizon, width, horizon);
        cache.baseFill.addColorStop(0, 'rgba(255,255,255,0)');
        cache.baseFill.addColorStop(0.5, color(0.7, 0.64));
        cache.baseFill.addColorStop(1, 'rgba(255,255,255,0)');
    }
    state.spectrumPaintCache = cache;
    return cache;
}

export function drawSpectrum() {
    ensureCanvases();
    if (!spectrumContext || !state.visualProfile) return;
    const width = state.specLogicalWidth || 640;
    const height = state.specLogicalHeight || 190;
    spectrumContext.clearRect(0, 0, width, height);
    const rawBins = state.renderSpecBins.length
        ? state.renderSpecBins
        : new Array(state.visualProfile.lite ? 24 : 48).fill(0);
    const rawPeaks = state.renderSpecPeaks.length ? state.renderSpecPeaks : rawBins;
    const attack = state.visualProfile.lite ? 0.94 : 0.96;
    const release = state.visualProfile.lite ? 0.24 : 0.16;
    state.smoothDisplayBins = easeBins(rawBins, state.smoothDisplayBins, attack, release);
    state.smoothDisplayPeaks = easeBins(rawPeaks, state.smoothDisplayPeaks, 0.97, state.visualProfile.lite ? 0.05 : 0.035);
    const bins = state.smoothDisplayBins.length ? state.smoothDisplayBins : rawBins;
    const peaks = state.smoothDisplayPeaks.length ? state.smoothDisplayPeaks : bins;
    const count = Math.max(1, bins.length);
    const horizon = height * 0.92;
    const topPadding = Math.max(10, height * 0.08);
    const usable = Math.max(30, horizon - topPadding);
    const maxBarHeight = usable * 0.97;
    const maxCrestHeight = usable;
    const step = width / count;
    const barWidth = Math.max(1.5, Math.min(step * 0.78, step - 1));
    const paints = ensureSpectrumPaintCache(width, height);
    const levels = new Array(count);
    const crests = new Array(count);

    function bandGain(index) {
        if (count <= 1) return 1;
        const ratio = index / (count - 1);
        if (ratio < 0.12) return 0.42 + ratio * 1.5;
        if (ratio < 0.35) return 0.60 + (ratio - 0.12) * 1.4;
        if (ratio < 0.65) return 0.92 + (ratio - 0.35) * 0.35;
        return 1.03 + (ratio - 0.65) * 0.35;
    }

    function shapeLevel(value, index, softCap) {
        value = Math.max(0, Math.min(1, Number(value) || 0));
        value = Math.max(0, Math.min(1.35, value * bandGain(index)));
        let shaped = Math.pow(value, 1.05);
        shaped = Math.max(0, (shaped - 0.02) / 0.98);
        shaped = Math.pow(shaped, 0.72) * 1.28;
        if (shaped > 0.88) {
            const over = shaped - 0.88;
            shaped = 0.88 + over / (1 + over * 1.15);
        }
        const ratio = count <= 1 ? 0 : index / (count - 1);
        let localCap = softCap;
        if (ratio < 0.18) localCap = Math.min(localCap, 0.78 + ratio * 0.7);
        else if (ratio < 0.36) localCap = Math.min(localCap, 0.86 + (ratio - 0.18) * 0.45);
        return Math.max(0, Math.min(localCap, shaped));
    }

    for (let index = 0; index < count; index += 1) {
        levels[index] = shapeLevel(bins[index], index, 0.97);
        crests[index] = Math.max(levels[index], shapeLevel(peaks[index], index, 1));
    }

    if (!state.visualProfile.lite) {
        spectrumContext.beginPath();
        for (let index = 0; index < count; index += 1) {
            const x = index * step + step * 0.5;
            const y = horizon - crests[index] * maxCrestHeight * 0.94;
            if (index === 0) spectrumContext.moveTo(x, y);
            else spectrumContext.lineTo(x, y);
        }
        spectrumContext.lineTo(width, horizon);
        spectrumContext.lineTo(0, horizon);
        spectrumContext.closePath();
        spectrumContext.fillStyle = paints.fieldFill;
        spectrumContext.fill();
    }

    for (let index = 0; index < count; index += 1) {
        const level = levels[index];
        if (level < 0.01) continue;
        const barHeight = Math.max(2, level * maxBarHeight);
        const x = index * step + (step - barWidth) * 0.5;
        const y = horizon - barHeight;
        spectrumContext.fillStyle = state.visualProfile.lite ? paints.barSolid : paints.barFill;
        spectrumContext.fillRect(x, y, barWidth, barHeight);
        const peak = crests[index];
        if (peak < 0.025) continue;
        let capY = Math.min(horizon - peak * maxCrestHeight, y - 1);
        const capHeight = Math.max(2, height * (state.visualProfile.lite ? 0.014 : 0.012));
        const minimumCapTop = topPadding * 0.2;
        if (capY - capHeight * 1.3 < minimumCapTop) capY = minimumCapTop + capHeight * 1.3;
        spectrumContext.fillStyle = paints.peak;
        const peakWidth = Math.max(barWidth, 2);
        const peakX = x - (peakWidth - barWidth) * 0.5;
        spectrumContext.fillRect(peakX, capY - capHeight * 0.15, peakWidth, Math.max(capHeight, 2.5));
        if (!state.visualProfile.lite && peak > 0.42) {
            spectrumContext.fillStyle = paints.capGlow;
            spectrumContext.fillRect(peakX + peakWidth * 0.16, capY - capHeight * 1.2, peakWidth * 0.68, Math.max(1, capHeight * 0.55));
        }
    }

    if (count >= 2) {
        const lift = Math.max(3, Math.min(barWidth * 0.42, topPadding * 0.42));
        const minimumCurveY = topPadding * 0.15;
        const crestY = (index) => Math.max(minimumCurveY, horizon - crests[index] * maxCrestHeight - lift);
        const strokeCrest = () => {
            spectrumContext.beginPath();
            for (let index = 0; index < count; index += 1) {
                const x = index * step + step * 0.5;
                const y = crestY(index);
                if (index === 0) spectrumContext.moveTo(x, y);
                else {
                    const previousX = (index - 1) * step + step * 0.5;
                    const previousY = crestY(index - 1);
                    const controlX = (previousX + x) * 0.5;
                    const controlY = Math.min(previousY, y) - Math.abs(previousY - y) * 0.16;
                    spectrumContext.quadraticCurveTo(controlX, Math.max(minimumCurveY, controlY), x, y);
                }
            }
        };
        if (!state.visualProfile.lite) {
            strokeCrest();
            spectrumContext.strokeStyle = paints.capGlow;
            spectrumContext.lineWidth = 3.8 + state.specRms * 2.4;
            spectrumContext.lineJoin = 'round';
            spectrumContext.lineCap = 'round';
            spectrumContext.globalAlpha = 0.32;
            spectrumContext.stroke();
            spectrumContext.globalAlpha = 1;
        }
        strokeCrest();
        spectrumContext.strokeStyle = paints.line;
        spectrumContext.lineWidth = (state.visualProfile.lite ? 2.1 : 2.45) + state.specRms * 1.55;
        spectrumContext.lineJoin = 'round';
        spectrumContext.lineCap = 'round';
        spectrumContext.stroke();
    }

    spectrumContext.fillStyle = state.visualProfile.lite ? paints.baseSolid : paints.baseFill;
    spectrumContext.fillRect(width * 0.04, horizon, width * 0.92, Math.max(1, height * 0.01));
}
