import { fetchLyricOffset, fetchMedia, postLyricOffset } from './api.js';
import { state } from './state.js';
import { escHtml } from './utils.js';

function trackKey(data) {
    if (!data) return '';
    if (data.track_key) return String(data.track_key);
    const key = [
        data.song_id == null ? '' : String(data.song_id),
        data.title || '',
        data.artist || '',
        data.album || '',
    ].join('\u001f');
    return key.replace(/\u001f/g, '') ? key : '';
}

function lyricsKey(lines) {
    if (!Array.isArray(lines) || !lines.length) return '';
    return lines.map((item) => {
        if (Array.isArray(item)) return `${item[0]}\u0001${item[1] || ''}`;
        if (item && typeof item === 'object') {
            const chars = Array.isArray(item.chars)
                ? item.chars.map((char) => `${char.start || 0}:${char.dur || 0}:${char.text || ''}`).join('')
                : '';
            return `${item.start || 0}\u0001${chars}`;
        }
        return String(item || '');
    }).join('\u0002');
}

function requestMediaHydration() {
    const now = Date.now();
    if (now - state.media.hydrateRequestedAt < 500) return;
    state.media.hydrateRequestedAt = now;
    refreshMedia();
}

function lyricTextForIndex(index) {
    if (index < 0 || index >= state.media.lyrics.length) return '';
    return String(state.media.lyrics[index]?.[1] || '');
}

function lyricDurationForIndex(index, data) {
    if (data && typeof data.lyric_duration === 'number' && data.lyric_duration > 0) {
        return Math.max(0.18, Number(data.lyric_duration) || 0.18);
    }
    if (index >= 0 && index < state.media.lyrics.length) {
        const start = Number(state.media.lyrics[index]?.[0] || 0);
        const end = index + 1 < state.media.lyrics.length
            ? Number(state.media.lyrics[index + 1]?.[0] || 0)
            : Number(state.media.duration || 0);
        if (end > start) return Math.max(0.18, end - start);
    }
    return 3;
}

function stopMarqueeLoop() {
    if (state.media.marqueeRaf) cancelAnimationFrame(state.media.marqueeRaf);
    state.media.marqueeRaf = 0;
}

function marqueeLoop() {
    state.media.marqueeRaf = 0;
    if (!state.media.marqueeActive || !state.media.playing) return;
    if (updateMarquee()) state.media.marqueeRaf = requestAnimationFrame(marqueeLoop);
}

function startMarqueeLoop() {
    if (state.media.marqueeRaf || !state.media.marqueeActive || !state.media.playing) return;
    state.media.marqueeRaf = requestAnimationFrame(marqueeLoop);
}

function resetMarquee() {
    Object.assign(state.media, {
        lineIndex: -1,
        lineText: '',
        lineDuration: 0,
        lineElapsedAtSync: 0,
        lineSyncAt: 0,
        lineActive: false,
        marqueeActive: false,
    });
    stopMarqueeLoop();
}

function setMediaPlaying(playing) {
    const next = !!playing;
    if (state.media.playing === next) return;
    if (state.media.lineActive) {
        if (state.media.playing && !next) state.media.lineElapsedAtSync = currentLineElapsed();
        state.media.lineSyncAt = performance.now();
    }
    state.media.playing = next;
    if (!state.media.marqueeActive) return;
    if (state.media.playing) startMarqueeLoop();
    else stopMarqueeLoop();
}

function bindLyricTiming(data = {}, options = {}) {
    const index = typeof data.lyric_index === 'number' ? data.lyric_index : state.media.lyricIndex;
    const text = data.lyric != null && data.lyric !== '' ? String(data.lyric) : lyricTextForIndex(index);
    const sameLine = !options.force && state.media.lineActive
        && index === state.media.lineIndex && text === state.media.lineText;
    if (sameLine) {
        if (typeof data.lyric_duration === 'number' && data.lyric_duration > 0) {
            state.media.lineDuration = Math.max(0.18, Number(data.lyric_duration) || state.media.lineDuration);
        }
        return false;
    }
    Object.assign(state.media, {
        lineIndex: index,
        lineText: text,
        lineDuration: lyricDurationForIndex(index, data),
        lineElapsedAtSync: 0,
        lineSyncAt: performance.now(),
        lineActive: index >= 0 && !!text,
        marqueeActive: false,
    });
    return true;
}

function currentLineElapsed() {
    if (!state.media.lineActive) return 0;
    let elapsed = state.media.lineElapsedAtSync;
    if (state.media.playing) elapsed += Math.max(0, (performance.now() - state.media.lineSyncAt) / 1000);
    return Math.max(0, elapsed);
}

function lyricScrollProgress(elapsed = currentLineElapsed()) {
    // 与音乐舞台一致：停 1/3 行时长 → 4/9 匀速滚完 → 末尾 2/9 停在句尾。
    const span = Math.max(0.18, state.media.lineDuration || 0.18);
    const hold = span / 3;
    if (elapsed <= hold) return 0;
    return Math.max(0, Math.min(1, (elapsed - hold) / Math.max(0.12, span * 4 / 9)));
}

function syncMediaClock(data) {
    const newPosition = Number(data.position || 0);
    const serverTimestamp = Number(data.server_ts || 0);
    const now = Date.now() / 1000;
    state.media.startTime = serverTimestamp > 0
        ? now - newPosition - Math.max(-1.5, Math.min(1.5, now - serverTimestamp))
        : now - newPosition;
    state.media.position = newPosition;
    if (data.duration) state.media.duration = Number(data.duration) || state.media.duration;
    state.media.lastPositionSource = data.position_source || state.media.lastPositionSource || 'none';
}

export function applyLyricFrame(data = {}) {
    if (data.status === 'idle' || data.status === 'error' || (!data.title && !data.lyric)) {
        drawLyric(data);
        return;
    }
    if (typeof data.lyric_offset === 'number') setOffsetUi(Number(data.lyric_offset));
    if (typeof data.playing === 'boolean') setMediaPlaying(data.playing);
    else if (data.status) setMediaPlaying(data.status === 'playing');
    const incomingTrackKey = trackKey(data);
    // 首帧就是 lyric 帧（歌词推送可能先于 playback 快照到达）时也要走完整的
    // 新歌引导：若只记 trackKey 不请求水合，随后的 slim playback 快照会因
    // trackKey 相同而不再渲染/水合，整首歌都没有歌词。
    const trackChanged = Boolean(incomingTrackKey) && incomingTrackKey !== state.media.trackKey;
    if (trackChanged) {
        state.media.trackKey = incomingTrackKey;
        state.media.title = data.title || '';
        state.media.lyrics = [];
        state.media.lyricsKey = '';
        resetLyricState();
        renderLyricLines();
        const idle = document.getElementById('lyricIdle');
        if (idle) {
            idle.textContent = '歌词加载中…';
            idle.style.display = 'block';
        }
        requestMediaHydration();
    }
    if (data.title) {
        const title = document.getElementById('lyricTitle');
        const artist = document.getElementById('lyricArtist');
        if (title) title.textContent = data.title;
        if (artist) artist.textContent = data.artist || '';
    }
    const previousPosition = state.media.position;
    syncMediaClock(data);
    if (previousPosition - Number(data.position || 0) > 5) resetLyricState();
    if (typeof data.lyric_index === 'number') state.media.lyricIndex = data.lyric_index;
    bindLyricTiming(data, { force: trackChanged });
    updateLyricLine(true);
}

export function drawLyric(data) {
    const title = document.getElementById('lyricTitle');
    const artist = document.getElementById('lyricArtist');
    const scroll = document.getElementById('lyricScroll');
    const idle = document.getElementById('lyricIdle');
    if (!data || data.status === 'idle' || data.status === 'error' || !data.title) {
        title.textContent = '--';
        artist.textContent = '';
        scroll.innerHTML = '';
        idle.textContent = '未在播放';
        idle.style.display = 'block';
        setMediaPlaying(false);
        Object.assign(state.media, {
            title: '',
            trackKey: '',
            lyricsKey: '',
            lyrics: [],
            lyricIndex: -1,
        });
        resetLyricState();
        return;
    }
    title.textContent = data.title;
    artist.textContent = data.artist;
    setMediaPlaying(data.status === 'playing');
    if (typeof data.lyric_offset === 'number') setOffsetUi(Number(data.lyric_offset));
    const incomingTrackKey = trackKey(data) || String(data.title || '');
    const isNewSong = !state.media.trackKey || (incomingTrackKey && incomingTrackKey !== state.media.trackKey);
    if (!isNewSong) state.media.title = data.title;
    let didRenderLyrics = false;
    const isSlimMedia = !!data.media_slim;
    const incomingLyrics = Array.isArray(data.lyrics) ? data.lyrics : [];
    const incomingLyricsKey = lyricsKey(incomingLyrics);
    if (isNewSong) {
        Object.assign(state.media, {
            title: data.title,
            trackKey: incomingTrackKey,
            duration: data.duration || 0,
        });
        resetLyricState();
        didRenderLyrics = true;
        if (isSlimMedia && !incomingLyricsKey) {
            state.media.lyrics = [];
            state.media.lyricsKey = '';
            idle.style.display = 'block';
            idle.textContent = '歌词加载中…';
            renderLyricLines();
            requestMediaHydration();
        } else {
            state.media.lyrics = incomingLyrics;
            state.media.lyricsKey = incomingLyricsKey;
            idle.style.display = state.media.lyrics.length ? 'none' : 'block';
            idle.textContent = '暂无歌词';
            renderLyricLines();
        }
    } else if (incomingLyricsKey && incomingLyricsKey !== state.media.lyricsKey) {
        state.media.lyrics = incomingLyrics;
        state.media.lyricsKey = incomingLyricsKey;
        didRenderLyrics = true;
        idle.style.display = 'none';
        idle.textContent = '暂无歌词';
        renderLyricLines();
    } else if (!isSlimMedia && !incomingLyricsKey && !state.media.lyricsKey && !state.media.lyrics.length) {
        state.media.lyricsKey = '__empty__';
        idle.style.display = 'block';
        idle.textContent = '暂无歌词';
        renderLyricLines();
    }
    const previousPosition = state.media.position;
    syncMediaClock(data);
    if (previousPosition - Number(data.position || 0) > 5) resetLyricState();
    if (typeof data.lyric_index === 'number') state.media.lyricIndex = data.lyric_index;
    bindLyricTiming(data, { force: isNewSong || didRenderLyrics });
    updateLyricLine(isNewSong || didRenderLyrics);
}

function resetLyricState() {
    state.media.lastLyricIdx = -2;
    state.media.pendingIdx = -2;
    state.media.pendingSince = 0;
    resetMarquee();
}

function renderLyricLines() {
    const scroll = document.getElementById('lyricScroll');
    if (!scroll) return;
    scroll.innerHTML = state.media.lyrics.map((item, index) => `<div class="lyric-line" data-idx="${index}"><span class="lyric-line-inner">${escHtml(item[1] || ' ')}</span></div>`).join('');
}

function lineInner(line) {
    return line ? line.querySelector('.lyric-line-inner') : null;
}

function resetLineMarquee(line) {
    if (!line) return;
    const inner = lineInner(line);
    line.classList.remove('marquee', 'marquee-done');
    delete line.dataset.scrollDistance;
    delete line.dataset.rawLyric;
    delete line.dataset.measureToken;
    if (!inner) return;
    const raw = lyricTextForIndex(Number(line.dataset.idx || -1));
    if (raw && inner.textContent !== raw) inner.textContent = raw;
    inner.style.transition = 'none';
    inner.style.transform = 'translate3d(0,0,0)';
    requestAnimationFrame(() => {
        if (!line.classList.contains('marquee')) inner.style.transition = '';
    });
}

function measureLineScroll(line, inner) {
    const distance = Math.max(0, Math.ceil(inner.scrollWidth - line.clientWidth));
    line.dataset.scrollDistance = String(distance);
    return distance;
}

function prepareCurrentMarquee(index) {
    state.media.marqueeActive = false;
    if (index < 0) return false;
    const scroll = document.getElementById('lyricScroll');
    if (!scroll) return false;
    const line = scroll.children[index];
    const inner = lineInner(line);
    if (!line || !inner) return false;
    const rawText = state.media.lineText || lyricTextForIndex(index);
    if (!rawText) {
        resetLineMarquee(line);
        return false;
    }
    const displayText = `${rawText}   `;
    const changed = line.dataset.rawLyric !== rawText || inner.textContent !== displayText;
    line.classList.add('marquee');
    if (changed) {
        line.dataset.rawLyric = rawText;
        inner.style.transition = 'none';
        inner.style.transform = 'translate3d(0,0,0)';
        inner.textContent = displayText;
        line.classList.remove('marquee-done');
    }
    const distance = measureLineScroll(line, inner);
    const token = `${index}\u0001${rawText}`;
    line.dataset.measureToken = token;
    if (changed || distance <= 1) {
        requestAnimationFrame(() => {
            if (line.dataset.measureToken !== token) return;
            if (inner.textContent !== displayText) inner.textContent = displayText;
            const nextDistance = measureLineScroll(line, inner);
            if (nextDistance <= 1) {
                resetLineMarquee(line);
                return;
            }
            state.media.marqueeActive = true;
            if (line.classList.contains('marquee')) inner.style.transition = '';
            updateMarquee();
            startMarqueeLoop();
        });
    }
    if (distance <= 1) return false;
    state.media.marqueeActive = true;
    updateMarquee();
    startMarqueeLoop();
    return true;
}

function updateMarquee() {
    if (!state.media.marqueeActive) return false;
    const scroll = document.getElementById('lyricScroll');
    if (!scroll || state.media.lastLyricIdx < 0) {
        state.media.marqueeActive = false;
        return false;
    }
    const line = scroll.children[state.media.lastLyricIdx];
    const inner = lineInner(line);
    if (!line || !inner || !line.classList.contains('marquee')) {
        state.media.marqueeActive = false;
        return false;
    }
    let distance = Number(line.dataset.scrollDistance || 0);
    if (distance <= 1) distance = measureLineScroll(line, inner);
    if (distance <= 1) {
        state.media.marqueeActive = false;
        return false;
    }
    if (inner.style.transition === 'none') {
        void inner.offsetWidth;
        inner.style.transition = '';
    }
    const progress = lyricScrollProgress();
    inner.style.transform = `translate3d(${(-distance * progress).toFixed(1)}px,0,0)`;
    const done = progress >= 0.995;
    line.classList.toggle('marquee-done', done);
    if (done) state.media.marqueeActive = false;
    return !done;
}

function scrollToLyricIndex(index) {
    const scroll = document.getElementById('lyricScroll');
    const text = document.getElementById('lyricText');
    if (!scroll || !text) return;
    scroll.style.transform = `translateY(${text.clientHeight / 2 - state.media.lineHeight / 2 - index * state.media.lineHeight}px)`;
    Array.from(scroll.children).forEach((line, lineIndex) => {
        const offset = lineIndex - index;
        line.className = `lyric-line${offset === 0 ? ' current' : Math.abs(offset) === 1 ? ' near' : ''}`;
        if (offset !== 0) resetLineMarquee(line);
    });
    prepareCurrentMarquee(index);
}

export function updateLyricLine(force = false) {
    if (!state.media.playing && !force) return;
    if (!state.media.lyrics.length && (typeof state.media.lyricIndex !== 'number' || state.media.lyricIndex < 0)) return;
    const position = (state.media.playing ? Date.now() / 1000 - state.media.startTime : state.media.position) + state.media.lyricOffset;
    let index = -1;
    // lyricIndex 初始为 -1（也是 number），typeof 判断永真会把兜底扫描变成死代码；
    // 只有后端给出了有效行号（>= 0）才视为后端驱动。
    const backendDriven = typeof state.media.lyricIndex === 'number' && state.media.lyricIndex >= 0;
    if (backendDriven) {
        index = state.media.lyricIndex;
    } else {
        for (let line = 0; line < state.media.lyrics.length; line += 1) {
            if (state.media.lyrics[line][0] <= position) index = line;
            else break;
        }
    }
    if (index !== state.media.lastLyricIdx) {
        const now = Date.now();
        if (index !== state.media.pendingIdx) {
            state.media.pendingIdx = index;
            state.media.pendingSince = now;
        }
        const settled = now - state.media.pendingSince >= state.media.debounceMs;
        const bigJump = Math.abs(index - state.media.lastLyricIdx) > 1;
        if (settled || force || bigJump || backendDriven) {
            if (index >= 0 && index !== state.media.lineIndex) {
                bindLyricTiming({ lyric_index: index, lyric: lyricTextForIndex(index) }, { force: true });
            }
            state.media.lastLyricIdx = index;
            scrollToLyricIndex(index);
        }
    } else if (force) {
        scrollToLyricIndex(state.media.lastLyricIdx);
    }
}

export async function refreshMedia() {
    try { drawLyric(await fetchMedia()); }
    catch (error) { console.error('Media error:', error); }
}

export function resetMediaRenderState() {
    // 播放器卡片重挂载（工作区编辑等）后 DOM 是全新的，而模块级 state.media
    // 仍记着上一次渲染的 trackKey/lyricsKey；不清掉的话，重放的 slim 快照
    // 会被当作"没有变化"，卡片永远停在"未在播放"。
    Object.assign(state.media, {
        title: '',
        trackKey: '',
        lyricsKey: '',
        lyrics: [],
        lyricIndex: -1,
        hydrateRequestedAt: 0,
    });
    resetLyricState();
}

function setOffsetUi(offset) {
    state.media.lyricOffset = offset;
    const value = document.getElementById('lyricOffsetVal');
    if (value) value.textContent = state.media.lyricOffset.toFixed(1);
}

export async function adjustLyricOffset(delta) {
    try {
        const data = await postLyricOffset(delta);
        setOffsetUi(data.offset);
    } catch (error) {
        console.error('[lyric offset]', error);
    }
}

export function initLyrics() {
    setOffsetUi(state.media.lyricOffset);
    fetchLyricOffset().then((data) => setOffsetUi(data.offset)).catch(() => {
        try {
            const value = parseFloat(localStorage.getItem('lyricOffset'));
            if (!Number.isNaN(value)) setOffsetUi(value);
        } catch (_error) {}
    });
    window.addEventListener('resize', () => updateLyricLine(true));
}
