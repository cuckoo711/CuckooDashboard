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

function lyricSlots() {
    return [document.getElementById('lyricCurrent'), document.getElementById('lyricNext')];
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

function ensureLyricInner(slot) {
    let inner = slot.querySelector('.lyric-scroll-inner');
    if (!inner) {
        inner = document.createElement('span');
        inner.className = 'lyric-scroll-inner';
        slot.textContent = '';
        slot.appendChild(inner);
    }
    return inner;
}

function measureLyricScroll(slot, inner) {
    const distance = Math.max(0, Math.ceil(inner.scrollWidth - slot.clientWidth));
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
    const displayText = longLine ? '   ' + rawText + '   ' : rawText;
    slot.classList.toggle('is-active', isActive);
    slot.classList.toggle('is-next', !isActive);
    slot.classList.toggle('marquee', Boolean(longLine));
    slot.classList.add(slotIndex === 0 ? 'lyric-slot-a' : 'lyric-slot-b');

    if (changed) {
        state.lyricSlotState[slotIndex] = { idx: lyricIndex, role, raw: rawText };
        slot.dataset.rawLyric = rawText;
        inner.style.transition = 'none';
        inner.style.transform = 'translate3d(0,0,0)';
        inner.textContent = displayText;
        fitLyricText(slot, rawText, role);
        measureLyricScroll(slot, inner);
        slot.classList.remove('marquee-done');
        if (longLine) {
            const measureToken = String(lyricIndex) + '\u0001' + rawText;
            slot.dataset.measureToken = measureToken;
            requestAnimationFrame(() => {
                if (slot.dataset.measureToken !== measureToken) return;
                if (inner.textContent !== displayText) inner.textContent = displayText;
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
        if (inner.textContent !== displayText) inner.textContent = displayText;
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
    if (inner.textContent !== displayText) {
        const keepTransform = inner.style.transform;
        inner.textContent = displayText;
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
    const previous = document.getElementById('lyricPrev');
    if (previous) previous.hidden = true;
    let position = currentPosSec();
    const duration = state.mediaDuration || 0;
    if (duration > 0 && position > duration + 1) position %= Math.max(duration, 1);
    const fill = document.getElementById('progressFill');
    const positionText = document.getElementById('posText');
    const durationText = document.getElementById('durText');
    if (fill) fill.style.width = (duration > 0 ? Math.min(100, position / duration * 100) : 0).toFixed(2) + '%';
    if (positionText) positionText.textContent = fmtTime(position);
    if (durationText) durationText.textContent = fmtTime(duration);

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
        const title = document.getElementById('trackTitle');
        const artist = document.getElementById('trackArtist');
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
