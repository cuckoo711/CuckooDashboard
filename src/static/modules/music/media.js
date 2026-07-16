import { applyCoverPaletteFromData, setCover } from './cover.js';
import { renderOffsets } from './controls.js';
import { bindLyricLineTiming, resetLyricSlots, syncMediaClock, updateLyricLine } from './lyrics.js';
import { state } from './state.js';

function resetIdleMedia() {
    const title = document.getElementById('trackTitle');
    const artist = document.getElementById('trackArtist');
    const current = document.getElementById('lyricCurrent');
    const next = document.getElementById('lyricNext');
    if (title) title.textContent = '--';
    if (artist) artist.textContent = '等待媒体会话…';
    if (current) current.textContent = '未在播放';
    if (next) next.textContent = '';

    state.mediaPlaying = false;
    state.mediaTitle = '';
    state.mediaTrackKey = '';
    state.mediaLyrics = [];
    state.mediaLyricIndex = -1;
    state.mediaNextLyricIndex = -1;
    state.mediaLyricScroll = 0;
    state.mediaLyricLineProgress = 0;
    state.lyricLineText = '';
    state.lyricNextText = '';
    state.lyricLineDuration = 0;
    state.lyricLineElapsedAtSync = 0;
    state.lyricLineSyncAt = 0;
    state.lyricLineActive = false;
    state.lastLyricIdx = -1;
    resetLyricSlots();
    state.coverVersion = 0;
    state.coverUrl = '';
    state.coverIdentity = '';
    setCover('', false);

    const fill = document.getElementById('progressFill');
    const position = document.getElementById('posText');
    const duration = document.getElementById('durText');
    if (fill) fill.style.width = '0%';
    if (position) position.textContent = '0:00';
    if (duration) duration.textContent = '0:00';
}

export function applyMedia(data) {
    const title = document.getElementById('trackTitle');
    const artist = document.getElementById('trackArtist');
    if (!data || data.status === 'idle' || data.status === 'error' || !data.title) {
        resetIdleMedia();
        return;
    }

    if (title) title.textContent = data.title;
    if (artist) artist.textContent = data.artist || '';
    state.mediaPlaying = data.status === 'playing';
    state.mediaArtist = data.artist || '';
    if (typeof data.lyric_offset === 'number') {
        state.lyricOffset = Number(data.lyric_offset);
        renderOffsets();
    }

    const trackKey = [
        data.song_id == null ? '' : String(data.song_id),
        data.title || '',
        data.artist || '',
        data.album || '',
    ].join('\u001f');
    const isNewSong = trackKey !== state.mediaTrackKey;

    if (data.has_cover && data.cover_url) {
        const coverIdentity = data.cover_identity || data.cover_url;
        const coverChanged = isNewSong || coverIdentity !== state.coverIdentity;
        applyCoverPaletteFromData(data, coverIdentity);
        if (coverChanged) {
            state.coverVersion = data.cover_version || 0;
            state.coverUrl = data.cover_url;
            state.coverIdentity = coverIdentity;
            setCover(data.cover_url, data.status === 'playing', true, {
                identity: coverIdentity,
                version: data.cover_version || 0,
            });
        } else {
            const disc = document.getElementById('coverDisc');
            if (disc && !disc.hidden) disc.classList.toggle('playing', data.status === 'playing');
        }
    } else if (isNewSong || state.coverUrl) {
        state.coverVersion = 0;
        state.coverUrl = '';
        state.coverIdentity = '';
        setCover('', data.status === 'playing');
    }

    if (isNewSong) {
        state.mediaTitle = data.title;
        state.mediaTrackKey = trackKey;
        state.mediaLyrics = data.lyrics || [];
        state.mediaDuration = data.duration || 0;
        state.lastLyricIdx = -1;
        resetLyricSlots();
        state.lyricLineActive = false;
        state.lyricLineText = '';
        state.lyricNextText = '';
    } else if (data.lyrics && data.lyrics.length) {
        state.mediaLyrics = data.lyrics;
        if (data.duration) state.mediaDuration = data.duration;
    }

    syncMediaClock(data);
    if (isNewSong) {
        if (typeof data.lyric_index === 'number' || data.lyric || typeof data.lyric_duration === 'number') {
            bindLyricLineTiming(data, { force: true });
        }
        updateLyricLine(true, {
            lyric: state.lyricLineText,
            nextLyric: state.lyricNextText,
            scroll: 0,
            lineProgress: 0,
        });
    } else if (!state.lyricLineActive && (typeof data.lyric_index === 'number' || data.lyric)) {
        if (bindLyricLineTiming(data, { force: true })) {
            updateLyricLine(true, {
                lyric: state.lyricLineText,
                nextLyric: state.lyricNextText,
                scroll: 0,
                lineProgress: 0,
            });
        }
    } else if (data.next_lyric && data.next_lyric !== state.lyricNextText) {
        state.lyricNextText = String(data.next_lyric || state.lyricNextText);
        if (typeof data.next_lyric_index === 'number') state.mediaNextLyricIndex = data.next_lyric_index;
    }
}
