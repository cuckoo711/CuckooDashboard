import { renderOffsets } from './controls.js';
import { state } from './state.js';

function fmtTime(seconds) {
    seconds = Math.max(0, Math.floor(seconds || 0));
    return Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0');
}

export function syncMediaClock(data) {
    const newPosition = Number(data.position || 0);
    const serverTimestamp = Number(data.server_ts || 0);
    const nowSeconds = Date.now() / 1000;
    if (serverTimestamp > 0) {
        const skew = Math.max(-1.5, Math.min(1.5, nowSeconds - serverTimestamp));
        state.mediaServerTs = serverTimestamp;
        state.mediaStartTime = nowSeconds - newPosition - skew;
    } else {
        state.mediaServerTs = 0;
        state.mediaStartTime = nowSeconds - newPosition;
    }
    state.mediaPosition = newPosition;
    if (data.duration != null) state.mediaDuration = Number(data.duration) || state.mediaDuration;
    state.lastPositionSource = data.position_source || state.lastPositionSource || 'none';
}

export function bindLyricLineTiming(data = {}, options = {}) {
    const nextIndex = typeof data.lyric_index === 'number' ? data.lyric_index : -1;
    const nextText = String(data.lyric || '');
    const nextNextText = String(data.next_lyric || '');
    const nextNextIndex = typeof data.next_lyric_index === 'number' ? data.next_lyric_index : -1;
    const sameLine = !options.force
        && state.lyricLineActive
        && nextIndex === state.mediaLyricIndex
        && nextText === state.lyricLineText;
    if (sameLine) {
        if (nextNextText) state.lyricNextText = nextNextText;
        if (nextNextIndex >= -1) state.mediaNextLyricIndex = nextNextIndex;
        if (typeof data.lyric_duration === 'number' && data.lyric_duration > 0) {
            state.lyricLineDuration = Math.max(0.18, Number(data.lyric_duration) || state.lyricLineDuration);
        }
        return false;
    }

    state.lyricLineText = nextText;
    state.lyricNextText = nextNextText;
    state.mediaLyricIndex = nextIndex;
    state.mediaNextLyricIndex = nextNextIndex;
    state.lyricLineDuration = Math.max(0.18, Number(data.lyric_duration || 0) || 0.18);
    state.lyricLineElapsedAtSync = 0;
    state.mediaLyricScroll = 0;
    state.mediaLyricLineProgress = 0;
    state.lyricLineSyncAt = performance.now();
    state.lyricLineActive = state.mediaLyricIndex >= 0 && Boolean(state.lyricLineText);
    return true;
}

function currentLineElapsed() {
    if (!state.lyricLineActive) return 0;
    let elapsed = state.lyricLineElapsedAtSync;
    if (state.mediaPlaying) elapsed += Math.max(0, (performance.now() - state.lyricLineSyncAt) / 1000);
    return Math.max(0, elapsed);
}

function localLineProgress(elapsed) {
    const span = Math.max(0.18, state.lyricLineDuration || 0.18);
    return Math.max(0, Math.min(1, (elapsed == null ? currentLineElapsed() : elapsed) / span));
}

function localScrollProgress(elapsed) {
    const span = Math.max(0.18, state.lyricLineDuration || 0.18);
    const value = elapsed == null ? currentLineElapsed() : elapsed;
    const scrollDuration = Math.min(3, span);
    const hold = scrollDuration / 3;
    if (value <= hold) return 0;
    const moveDuration = Math.max(0.12, scrollDuration - hold);
    return Math.max(0, Math.min(1, (value - hold) / moveDuration));
}

export function currentPosSec() {
    if (state.mediaPlaying) return Math.max(0, Date.now() / 1000 - state.mediaStartTime);
    return state.mediaPosition;
}

export function currentEffPosSec() {
    return currentPosSec() + Number(state.lyricOffset || 0);
}

// updateLyricLine 以约 25fps 运行；缓存静态元素引用，避免每帧重复 getElementById。
const domCache = new Map();

function getEl(id) {
    let element = domCache.get(id);
    if (!element || !element.isConnected) {
        element = document.getElementById(id);
        domCache.set(id, element);
    }
    return element;
}

function lyricSlots() {
    return [getEl('lyricCurrent'), getEl('lyricNext')];
}

export function resetLyricSlots() {
    state.lastLyricIdx = -1;
    state.lyricActiveSlot = 0;
    state.lyricSlotPending = false;
    state.lyricFadeToken += 1;
    state.lyricSlotState = [{ idx: null, role: 'active', raw: '' }, { idx: null, role: 'next', raw: '' }];
    lyricSlots().forEach((slot, index) => {
        if (!slot) return;
        slot.classList.remove('is-active', 'is-next', 'is-fading', 'marquee', 'marquee-done', 'pulse', 'long');
        slot.classList.add(index === 0 ? 'lyric-slot-a' : 'lyric-slot-b');
        delete slot.dataset.rawLyric;
        delete slot.dataset.scrollDistance;
    });
}

function countEffectiveChars(text) {
    return Array.from(String(text || '').replace(/\s/g, '')).length;
}

function fitLyricText(element, text, role) {
    if (!element) return 0;
    const length = countEffectiveChars(text);
    let scale = 1;
    if (role === 'active') {
        if (length > 52) scale = 0.72;
        else if (length > 38) scale = 0.78;
        else if (length > 26) scale = 0.86;
        else if (length > 16) scale = 0.94;
    } else {
        if (length > 32) scale = 0.78;
        else if (length > 22) scale = 0.88;
        else if (length > 14) scale = 0.94;
    }
    element.style.setProperty('--lyric-scale', scale.toFixed(2));
    element.dataset.lyricLength = String(length);
    element.classList.toggle('long', length > 10);
    return length;
}

function setLyricDensity(text) {
    const length = countEffectiveChars(text);
    const compact = length > 0 && length <= 15;
    if (compact === state.lyricCompact) return compact;
    state.lyricCompact = compact;
    const stage = document.querySelector('.cinema-lyric');
    if (stage) stage.classList.toggle('compact', compact);
    return compact;
}

const LYRIC_EXTRUDE_LAYERS = 12;

function ensureLyricInner(slot) {
    let inner = slot.querySelector('.lyric-scroll-inner');
    if (!inner) {
        inner = document.createElement('span');
        inner.className = 'lyric-scroll-inner';
        const extrude = document.createElement('span');
        extrude.className = 'lyric-extrude';
        extrude.setAttribute('aria-hidden', 'true');
        for (let layer = 0; layer < LYRIC_EXTRUDE_LAYERS; layer += 1) {
            const face = document.createElement('span');
            face.className = layer === 0 ? 'lyric-face lyric-face-front' : 'lyric-face lyric-face-side';
            face.style.setProperty('--layer', String(layer));
            extrude.appendChild(face);
        }
        inner.appendChild(extrude);
        slot.textContent = '';
        slot.appendChild(inner);
        return inner;
    }

    let extrude = inner.querySelector('.lyric-extrude');
    if (!extrude) {
        // Migrate any legacy single-text inner into the 3D extrude stack.
        const previous = inner.textContent || '';
        inner.textContent = '';
        extrude = document.createElement('span');
        extrude.className = 'lyric-extrude';
        extrude.setAttribute('aria-hidden', 'true');
        for (let layer = 0; layer < LYRIC_EXTRUDE_LAYERS; layer += 1) {
            const face = document.createElement('span');
            face.className = layer === 0 ? 'lyric-face lyric-face-front' : 'lyric-face lyric-face-side';
            face.style.setProperty('--layer', String(layer));
            face.textContent = previous;
            extrude.appendChild(face);
        }
        inner.appendChild(extrude);
        return inner;
    }

    // Keep marquee/static slots at the exact layer count (old cached DOM may lag).
    const faces = Array.from(extrude.querySelectorAll('.lyric-face'));
    if (faces.length !== LYRIC_EXTRUDE_LAYERS) {
        const sample = (faces[0] && faces[0].textContent) || '';
        extrude.textContent = '';
        for (let layer = 0; layer < LYRIC_EXTRUDE_LAYERS; layer += 1) {
            const face = document.createElement('span');
            face.className = layer === 0 ? 'lyric-face lyric-face-front' : 'lyric-face lyric-face-side';
            face.style.setProperty('--layer', String(layer));
            face.textContent = sample;
            extrude.appendChild(face);
        }
    }
    return inner;
}

function setLyricFaceText(inner, displayText) {
    const faces = inner.querySelectorAll('.lyric-face');
    if (!faces.length) {
        inner.textContent = displayText;
        return;
    }
    faces.forEach((face) => {
        if (face.textContent !== displayText) face.textContent = displayText;
    });
}

function measureLyricScroll(slot, inner) {
    const front = inner.querySelector('.lyric-face-front') || inner;
    const distance = Math.max(0, Math.ceil(front.scrollWidth - slot.clientWidth));
    slot.dataset.scrollDistance = String(distance);
    return distance;
}

function setLyricSlot(slotIndex, lyricIndex, role, force, options = {}) {
    const slot = lyricSlots()[slotIndex];
    if (!slot) return;
    let rawText = '';
    if (options.text != null && options.text !== '') rawText = String(options.text);
    else if (lyricIndex != null) rawText = String((state.mediaLyrics[lyricIndex] && state.mediaLyrics[lyricIndex][1]) || '');
    const inner = ensureLyricInner(slot);
    const slotState = state.lyricSlotState[slotIndex] || { idx: null, role: '', raw: '' };
    const changed = force || slotState.idx !== lyricIndex || slotState.raw !== rawText || slotState.role !== role || slot.dataset.rawLyric !== rawText;
    const isActive = role === 'active';
    const longLine = isActive && rawText && countEffectiveChars(rawText) > 10;
    const displayText = longLine ? ' ' + rawText + '   ' : rawText;
    slot.classList.toggle('is-active', isActive);
    slot.classList.toggle('is-next', !isActive);
    slot.classList.toggle('marquee', Boolean(longLine));
    slot.classList.add(slotIndex === 0 ? 'lyric-slot-a' : 'lyric-slot-b');

    if (changed) {
        // A slot being authoritatively rewritten can no longer be mid-fade.
        // If fadeSlotTo's timeout got token-invalidated before it could clean
        // up, its 'is-fading' class would otherwise stick around forever and
        // leave this slot's text permanently opacity:0.
        slot.classList.remove('is-fading');
        state.lyricSlotState[slotIndex] = { idx: lyricIndex, role, raw: rawText };
        slot.dataset.rawLyric = rawText;
        inner.style.transition = 'none';
        inner.style.transform = 'translate3d(0,0,0)';
        setLyricFaceText(inner, displayText);
        fitLyricText(slot, rawText, role);
        measureLyricScroll(slot, inner);
        slot.classList.remove('marquee-done');
        if (longLine) {
            const measureToken = String(lyricIndex) + '\u0001' + rawText;
            slot.dataset.measureToken = measureToken;
            requestAnimationFrame(() => {
                if (slot.dataset.measureToken !== measureToken) return;
                setLyricFaceText(inner, displayText);
                measureLyricScroll(slot, inner);
                if (slot.classList.contains('marquee') && slot.classList.contains('is-active')) inner.style.transition = '';
            });
        } else {
            requestAnimationFrame(() => {
                if (!slot.classList.contains('marquee')) inner.style.transition = '';
            });
        }
        return;
    }

    if (longLine && Number(slot.dataset.scrollDistance || 0) <= 0) {
        setLyricFaceText(inner, displayText);
        measureLyricScroll(slot, inner);
    }
    if (!longLine) {
        if (inner.style.transform !== 'translate3d(0,0,0)') {
            inner.style.transition = 'none';
            inner.style.transform = 'translate3d(0,0,0)';
        }
        slot.classList.remove('marquee-done');
        return;
    }
    const front = inner.querySelector('.lyric-face-front');
    if (!front || front.textContent !== displayText) {
        const keepTransform = inner.style.transform;
        setLyricFaceText(inner, displayText);
        measureLyricScroll(slot, inner);
        if (keepTransform) inner.style.transform = keepTransform;
    }
    const progress = typeof options.scroll === 'number'
        ? Math.max(0, Math.min(1, options.scroll))
        : localScrollProgress();
    const distance = Number(slot.dataset.scrollDistance || 0);
    if (inner.style.transition === 'none') {
        void inner.offsetWidth;
        inner.style.transition = '';
    }
    inner.style.transform = `translate3d(${(-distance * progress).toFixed(1)}px,0,0)`;
    slot.classList.toggle('marquee-done', progress >= 0.995);
}

function fadeSlotTo(slotIndex, lyricIndex, text) {
    if (state.lyricSlotPending) return;
    const slot = lyricSlots()[slotIndex];
    if (!slot) return;
    const slotState = state.lyricSlotState[slotIndex] || {};
    if (slotState.idx === lyricIndex && slotState.role === 'next') return;
    state.lyricSlotPending = true;
    const token = ++state.lyricFadeToken;
    slot.classList.add('is-fading');
    setTimeout(() => {
        if (token !== state.lyricFadeToken) return;
        setLyricSlot(slotIndex, lyricIndex, 'next', true, { text: text || '' });
        slot.classList.remove('is-fading', 'next-settle');
        void slot.offsetWidth;
        slot.classList.add('next-settle');
        state.lyricSlotPending = false;
    }, 160);
}

// This is the single retained implementation: it matches the second, effective
// updateLyricLine from the legacy script, including force-gated sentence pulse.
export function updateLyricLine(force, frame = {}) {
    const previous = getEl('lyricPrev');
    if (previous && !previous.hidden) previous.hidden = true;
    let position = currentPosSec();
    const duration = state.mediaDuration || 0;
    if (duration > 0 && position > duration + 1) position %= Math.max(duration, 1);
    const fill = getEl('progressFill');
    const positionText = getEl('posText');
    const durationText = getEl('durText');
    if (fill) {
        const width = (duration > 0 ? Math.min(100, position / duration * 100) : 0).toFixed(2) + '%';
        if (fill.style.width !== width) fill.style.width = width;
    }
    if (positionText) {
        const text = fmtTime(position);
        if (positionText.textContent !== text) positionText.textContent = text;
    }
    if (durationText) {
        const text = fmtTime(duration);
        if (durationText.textContent !== text) durationText.textContent = text;
    }

    const index = typeof state.mediaLyricIndex === 'number' ? state.mediaLyricIndex : -1;
    let nextIndex = typeof state.mediaNextLyricIndex === 'number' ? state.mediaNextLyricIndex : -1;
    const elapsed = currentLineElapsed();
    const scroll = typeof frame.scroll === 'number' ? frame.scroll : localScrollProgress(elapsed);
    const lineProgress = typeof frame.lineProgress === 'number' ? frame.lineProgress : localLineProgress(elapsed);
    const currentText = frame.lyric != null && frame.lyric !== '' ? frame.lyric : state.lyricLineText || '';
    const nextText = frame.nextLyric != null && frame.nextLyric !== '' ? frame.nextLyric : state.lyricNextText || '';
    state.mediaLyricScroll = scroll;
    state.mediaLyricLineProgress = lineProgress;

    if (!state.lyricLineActive && !currentText && !state.mediaTitle) {
        if (state.lastLyricIdx !== -2) {
            resetLyricSlots();
            state.lastLyricIdx = -2;
        }
        setLyricDensity('');
        return;
    }
    if (!state.lyricLineActive || index < 0 || !currentText) {
        if (state.lastLyricIdx !== -3 || force) {
            state.lastLyricIdx = -3;
            state.lyricActiveSlot = 0;
            setLyricDensity(state.mediaTitle || currentText || '');
            setLyricSlot(0, null, 'active', true, { text: state.mediaTitle || '' });
            setLyricSlot(1, nextIndex >= 0 ? nextIndex : null, 'next', true, { text: nextText || state.mediaArtist || '暂无歌词' });
        }
        return;
    }
    if (index !== state.lastLyricIdx) {
        state.lyricFadeToken += 1;
        state.lyricSlotPending = false;
        state.lastLyricIdx = index;
        state.lyricActiveSlot = Math.abs(index) % 2;
        setLyricDensity(currentText);
        setLyricSlot(state.lyricActiveSlot, index, 'active', true, { text: currentText, scroll });
        const otherSlot = 1 - state.lyricActiveSlot;
        if (nextIndex == null || nextIndex < 0) nextIndex = null;
        setLyricSlot(otherSlot, nextIndex, 'next', true, { text: nextText });
        const activeElement = lyricSlots()[state.lyricActiveSlot];
        if (activeElement && force) {
            activeElement.classList.remove('pulse');
            void activeElement.offsetWidth;
            activeElement.classList.add('pulse');
        }
    } else {
        setLyricSlot(state.lyricActiveSlot, index, 'active', false, { text: currentText, scroll });
        if (lineProgress >= 1 / 3 && nextText && nextIndex != null && nextIndex >= 0) {
            const otherSlot = 1 - state.lyricActiveSlot;
            if ((state.lyricSlotState[otherSlot] || {}).idx !== nextIndex) fadeSlotTo(otherSlot, nextIndex, nextText);
        }
    }
}

export function applyLyricFrame(data) {
    if (!data) return;
    if (typeof data.lyric_offset === 'number') {
        const nextOffset = Number(data.lyric_offset);
        if (nextOffset !== state.lyricOffset) {
            state.lyricOffset = nextOffset;
            renderOffsets();
        }
    }
    if (data.duration != null) state.mediaDuration = Number(data.duration) || state.mediaDuration;
    if (typeof data.playing === 'boolean') state.mediaPlaying = data.playing;
    else if (data.status) state.mediaPlaying = data.status === 'playing';
    if (data.track_key && data.track_key !== state.mediaTrackKey && data.title) {
        state.mediaTitle = data.title || state.mediaTitle;
        state.mediaArtist = data.artist || state.mediaArtist;
        state.mediaTrackKey = data.track_key;
        state.lastLyricIdx = -1;
        resetLyricSlots();
        const title = getEl('trackTitle');
        const artist = getEl('trackArtist');
        if (title) title.textContent = state.mediaTitle || '--';
        if (artist) artist.textContent = state.mediaArtist || '';
    }
    syncMediaClock(data);
    if (bindLyricLineTiming(data, { force: false })) {
        updateLyricLine(true, {
            lyric: state.lyricLineText,
            nextLyric: state.lyricNextText,
            scroll: 0,
            lineProgress: 0,
        });
    }
}
