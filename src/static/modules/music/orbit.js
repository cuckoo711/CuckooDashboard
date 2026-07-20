import { ORBIT, state } from './state.js';

function clampOrbit(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
}

function loadLocalOrbit() {
    try {
        const raw = localStorage.getItem(ORBIT.storageKey);
        if (!raw) return null;
        const data = JSON.parse(raw);
        if (!data || typeof data !== 'object') return null;
        return {
            yaw: typeof data.yaw === 'number' ? data.yaw : null,
            pitch: typeof data.pitch === 'number' ? data.pitch : null,
        };
    } catch (_error) {
        return null;
    }
}

function saveLocalOrbit(yaw, pitch) {
    try {
        localStorage.setItem(ORBIT.storageKey, JSON.stringify({
            yaw: Math.round(Number(yaw) * 100) / 100,
            pitch: Math.round(Number(pitch) * 100) / 100,
        }));
    } catch (_error) {}
}

function scheduleOrbitSave() {
    if (state.orbitSaveTimer) clearTimeout(state.orbitSaveTimer);
    state.orbitSaveTimer = setTimeout(() => {
        state.orbitSaveTimer = 0;
        saveLocalOrbit(state.orbitTargetYaw, state.orbitTargetPitch);
    }, 250);
}

function updateOrbitBadge() {
    const badge = document.getElementById('orbitBadge');
    const hint = document.getElementById('orbitHint');
    const yaw = Math.round(state.orbitYaw);
    const pitch = Math.round(state.orbitPitch);
    if (badge) badge.textContent = `3D ${yaw}° / ${pitch}°`;
    if (hint) hint.textContent = `左右 ${yaw}° · 俯仰 ${pitch}°`;
}

export function applySceneOrbit(immediate = false) {
    state.orbitYaw = clampOrbit(state.orbitYaw, -ORBIT.yawMax, ORBIT.yawMax);
    state.orbitPitch = clampOrbit(state.orbitPitch, -ORBIT.pitchMax, ORBIT.pitchMax);
    state.orbitTargetYaw = clampOrbit(state.orbitTargetYaw, -ORBIT.yawMax, ORBIT.yawMax);
    state.orbitTargetPitch = clampOrbit(state.orbitTargetPitch, -ORBIT.pitchMax, ORBIT.pitchMax);
    if (immediate) {
        state.orbitYaw = state.orbitTargetYaw;
        state.orbitPitch = state.orbitTargetPitch;
    }
    const scene = document.getElementById('scene3d');
    if (scene) scene.style.transform = `rotateX(${state.orbitPitch.toFixed(2)}deg) rotateY(${state.orbitYaw.toFixed(2)}deg)`;
    updateOrbitBadge();
}

export function setOrbitTarget(yaw, pitch, options = {}) {
    state.orbitTargetYaw = clampOrbit(yaw, -ORBIT.yawMax, ORBIT.yawMax);
    state.orbitTargetPitch = clampOrbit(pitch, -ORBIT.pitchMax, ORBIT.pitchMax);
    if (options.persist) scheduleOrbitSave();
}

export function nudgeOrbit(yawDelta, pitchDelta) {
    setOrbitTarget(state.orbitTargetYaw + yawDelta, state.orbitTargetPitch + pitchDelta, { persist: true });
}

export function resetOrbit() {
    setOrbitTarget(0, ORBIT.pitchDefault, { persist: true });
}

export function tickOrbit() {
    const ease = state.orbitDragging ? 1 : 0.18;
    const moving = state.orbitDragging
        || Math.abs(state.orbitTargetYaw - state.orbitYaw) >= 0.02
        || Math.abs(state.orbitTargetPitch - state.orbitPitch) >= 0.02;
    state.orbitYaw += (state.orbitTargetYaw - state.orbitYaw) * ease;
    state.orbitPitch += (state.orbitTargetPitch - state.orbitPitch) * ease;
    if (Math.abs(state.orbitTargetYaw - state.orbitYaw) < 0.02) state.orbitYaw = state.orbitTargetYaw;
    if (Math.abs(state.orbitTargetPitch - state.orbitPitch) < 0.02) state.orbitPitch = state.orbitTargetPitch;
    applySceneOrbit(false);
}

export function loadOrbit() {
    const localOrbit = loadLocalOrbit();
    if (!localOrbit) return;
    setOrbitTarget(
        localOrbit.yaw == null ? 0 : localOrbit.yaw,
        localOrbit.pitch == null ? ORBIT.pitchDefault : localOrbit.pitch,
    );
    applySceneOrbit(true);
}

export function setupOrbitControls() {
    const viewport = document.getElementById('sceneViewport');
    if (!viewport) return;
    viewport.addEventListener('pointerdown', (event) => {
        if (event.target && event.target.closest
            && event.target.closest('.corner-menu, .topbar, a, button, input, select, textarea')) return;
        state.orbitDragging = true;
        state.orbitLastX = event.clientX;
        state.orbitLastY = event.clientY;
        viewport.classList.add('dragging');
        try { viewport.setPointerCapture(event.pointerId); } catch (_error) {}
    });
    const pointerMove = (event) => {
        if (!state.orbitDragging) return;
        const deltaX = event.clientX - state.orbitLastX;
        const deltaY = event.clientY - state.orbitLastY;
        state.orbitLastX = event.clientX;
        state.orbitLastY = event.clientY;
        setOrbitTarget(state.orbitTargetYaw + deltaX * 0.18, state.orbitTargetPitch - deltaY * 0.16);
        applySceneOrbit(true);
    };
    const pointerUp = (event) => {
        if (!state.orbitDragging) return;
        state.orbitDragging = false;
        viewport.classList.remove('dragging');
        try { viewport.releasePointerCapture(event.pointerId); } catch (_error) {}
        scheduleOrbitSave();
    };
    window.addEventListener('pointermove', pointerMove);
    window.addEventListener('pointerup', pointerUp);
    window.addEventListener('pointercancel', pointerUp);
    viewport.addEventListener('dblclick', resetOrbit);
    const resetButton = document.getElementById('orbitResetBtn');
    if (resetButton) resetButton.addEventListener('click', (event) => {
        event.preventDefault();
        resetOrbit();
    });
    applySceneOrbit(true);
}
