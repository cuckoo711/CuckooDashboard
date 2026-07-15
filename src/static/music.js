/* Cuckoo Music Stage — full-screen lyric + spectrum + cover palette */

var DASHBOARD_TOKEN = '';
try { DASHBOARD_TOKEN = localStorage.getItem('dashboardToken') || ''; } catch (e) {}

function secureFetch(url, options) {
    options = options || {};
    options.headers = options.headers || {};
    options.headers['X-Requested-With'] = 'CuckooDashboard';
    if (DASHBOARD_TOKEN) options.headers['X-Dashboard-Token'] = DASHBOARD_TOKEN;
    return fetch(url, options);
}

var _stageFontStyleEl = null;
var _stageFontUrlCache = null;
function guessFontFormat(url) {
    var ext = (String(url || '').split('.').pop() || '').toLowerCase();
    if (ext === 'woff2') return 'woff2';
    if (ext === 'woff') return 'woff';
    if (ext === 'otf') return 'opentype';
    return 'truetype';
}
function applyStageFont(data) {
    data = data || {};
    var url = data.url || '';
    if (_stageFontUrlCache === url) return;
    _stageFontUrlCache = url;
    if (_stageFontStyleEl && _stageFontStyleEl.parentNode) _stageFontStyleEl.parentNode.removeChild(_stageFontStyleEl);
    _stageFontStyleEl = null;
    if (url) {
        var family = 'CuckooStageFont';
        var style = document.createElement('style');
        style.textContent = '@font-face{font-family:"' + family + '";src:url("' + url + '") format("' + guessFontFormat(url) + '");font-display:swap;}';
        document.head.appendChild(style);
        _stageFontStyleEl = style;
        document.body.style.fontFamily = '"' + family + '","Segoe UI",-apple-system,BlinkMacSystemFont,sans-serif';
    } else {
        document.body.style.fontFamily = '"Segoe UI Variable","Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
    }
}
function fmtTime(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    var m = Math.floor(sec / 60);
    var s = sec % 60;
    return m + ':' + String(s).padStart(2, '0');
}
function fmtMs(ms) {
    var n = Math.round(ms || 0);
    return (n >= 0 ? '+' : '') + n + 'ms';
}
function playerCtl(action) {
    secureFetch('/api/player/' + action, { method: 'POST' }).catch(function (e) { console.error('[player]', e); });
}
function reloadMedia() {
    secureFetch('/api/media/reload', { method: 'POST' })
        .then(function (r) { return r.json(); })
        .then(function (d) { applyMedia(d); })
        .catch(function (e) { console.error('[media reload]', e); });
}

/* ── state ── */
var LYRIC_OFFSET = 0;
var SPECTRUM_OFFSET_MS = 40;
var BEAT_LEAD_MS = 20;
var AUTO_CALIBRATE = true;
var SPECTRUM_RENDER_FPS = 0;
var SPECTRUM_RENDER_BARS = 0;
var _mediaTitle = '';
var _mediaArtist = '';
var _mediaTrackKey = '';
var _mediaLyrics = [];
var _mediaPlaying = false;
var _mediaPosition = 0;
var _mediaDuration = 0;
var _mediaStartTime = 0;
var _mediaServerTs = 0;
var _mediaLyricIndex = -1;
var _mediaNextLyricIndex = -1;
var _mediaLyricScroll = 0;
var _mediaLyricLineProgress = 0;
var _lastPositionSource = 'none';
var _lastLyricIdx = -1;
var _lyricCompact = false;
var _lyricActiveSlot = 0;
var _lyricSlotState = [{ idx: null, role: 'active', raw: '' }, { idx: null, role: 'next', raw: '' }];
var _lyricSlotPending = false;
var _lyricFadeToken = 0;
var _coverVersion = 0;
var _coverUrl = '';
var _coverIdentity = '';
var _lastPaletteToken = '';
var _lastAmbientIdentity = '';
var _orbitSaveTimer = 0;
var ORBIT_STORAGE_KEY = 'cuckooMusicOrbit';
var _specBins = [];
var _specPeaks = [];
var _renderSpecBins = [];
var _renderSpecPeaks = [];
var _lastSpectrumSourceTs = 0;
var _specRms = 0, _specBass = 0, _specMid = 0, _specHigh = 0, _specOnset = 0, _specEnergy = 0, _specRawRms = 0;
var _specSilent = true;
var _specAvailable = false;
var _pendingBeats = [];
var _lastBeatAt = 0;
var _smoothDisplayBins = [];
var _smoothDisplayPeaks = [];
var _ringAngle = 0;
var _pulse = 0;
var _coverTone = { r: 146, g: 162, b: 224 };
var _visualProfile = null;
var _lastVisualRenderAt = 0;
var _lastSpectrumRenderAt = 0;
var _lastStageTickAt = 0;
var _lastLyricSyncAt = 0;
var _frameRequestId = 0;
var _specLogicalWidth = 0;
var _specLogicalHeight = 0;
var _spectrumPaintCache = null;
var _heavyStageReady = false;
var _cameraPunch = 0;

function clampRenderOption(value, min, max) {
    var number = Math.round(Number(value || 0));
    if (!Number.isFinite(number) || number <= 0) return 0;
    return Math.max(min, Math.min(max, number));
}
function buildVisualProfile() {
    var dpr = Math.max(1, Number(window.devicePixelRatio || 1));
    var cores = Math.max(1, Number(navigator.hardwareConcurrency || 8));
    var memory = Math.max(1, Number(navigator.deviceMemory || 8));
    var reduceMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    var pixels = Math.max(1, window.innerWidth * window.innerHeight);
    var lite = reduceMotion || cores <= 4 || memory <= 4 || (dpr > 1.4 && pixels > 1200000);
    var manualFps = clampRenderOption(SPECTRUM_RENDER_FPS, 12, 60);
    var manualBars = clampRenderOption(SPECTRUM_RENDER_BARS, 12, 96);
    var spectrumFps = manualFps || (lite ? 24 : 60);
    var spectrumBars = manualBars || (lite ? 24 : 48);
    var pixelRatio = lite ? 1 : Math.min(1.5, dpr);
    return {
        lite: lite, pixelRatio: pixelRatio, spectrumPixelRatio: lite ? 0.75 : pixelRatio,
        spectrumFps: spectrumFps, spectrumBars: spectrumBars,
        spectrumFrameMs: 1000 / spectrumFps, stageTickFrameMs: 1000 / spectrumFps,
        frameMs: lite ? Infinity : 50
    };
}
function invalidateSpectrumPaintCache() { _spectrumPaintCache = null; }
function refreshVisualProfile() {
    _visualProfile = buildVisualProfile();
    document.body.classList.toggle('performance-lite', _visualProfile.lite);
    refreshRenderSpectrumBins();
    invalidateSpectrumPaintCache();
}

var ORBIT_YAW_MAX = 45, ORBIT_PITCH_MAX = 30, ORBIT_PITCH_DEFAULT = 14, ORBIT_STEP = 2;
var _orbitYaw = 0, _orbitPitch = ORBIT_PITCH_DEFAULT, _orbitTargetYaw = 0, _orbitTargetPitch = ORBIT_PITCH_DEFAULT;
var _orbitDragging = false, _orbitLastX = 0, _orbitLastY = 0;
var _calibrating = false, _calibMsg = '', _calibSamples = 0;

function renderOffsets() {
    var lo = document.getElementById('lyricOffsetVal');
    var so = document.getElementById('spectrumOffsetVal');
    var bo = document.getElementById('beatLeadVal');
    if (lo) lo.textContent = LYRIC_OFFSET.toFixed(1) + 's';
    if (so) so.textContent = fmtMs(SPECTRUM_OFFSET_MS);
    if (bo) bo.textContent = fmtMs(BEAT_LEAD_MS);
}
function applyMusicRenderOptions(data) {
    if (!data) return;
    var nextFps = typeof data.render_fps === 'number' ? data.render_fps : SPECTRUM_RENDER_FPS;
    var nextBars = typeof data.render_bars === 'number' ? data.render_bars : SPECTRUM_RENDER_BARS;
    if (nextFps === SPECTRUM_RENDER_FPS && nextBars === SPECTRUM_RENDER_BARS) return;
    SPECTRUM_RENDER_FPS = nextFps; SPECTRUM_RENDER_BARS = nextBars;
    _lastStageTickAt = 0; _lastSpectrumRenderAt = 0;
    resizeCanvases(); updateSpectrumSubscription(true);
}
function adjLyric(delta) {
    secureFetch('/api/media/offset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ delta: delta }) })
        .then(function (r) { return r.json(); }).then(function (d) { LYRIC_OFFSET = Number(d.offset || 0); renderOffsets(); }).catch(function () {});
}
function pushMusicOffsets(payload) {
    secureFetch('/api/music/offset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
        .then(function (r) { return r.json(); }).then(function (d) {
            if (typeof d.spectrum_offset_ms === 'number') SPECTRUM_OFFSET_MS = d.spectrum_offset_ms;
            if (typeof d.beat_lead_ms === 'number') BEAT_LEAD_MS = d.beat_lead_ms;
            if (typeof d.auto_calibrate === 'boolean') AUTO_CALIBRATE = d.auto_calibrate;
            applyMusicRenderOptions(d); renderOffsets();
        }).catch(function () {});
}
function adjSpectrum(delta) { pushMusicOffsets({ delta_spectrum_offset_ms: delta }); }
function adjBeat(delta) { pushMusicOffsets({ delta_beat_lead_ms: delta }); }
function loadLocalOrbit() {
    try {
        var raw = localStorage.getItem(ORBIT_STORAGE_KEY); if (!raw) return null;
        var data = JSON.parse(raw); if (!data || typeof data !== 'object') return null;
        return { yaw: (typeof data.yaw === 'number') ? data.yaw : null, pitch: (typeof data.pitch === 'number') ? data.pitch : null };
    } catch (e) { return null; }
}
function saveLocalOrbit(yaw, pitch) {
    try { localStorage.setItem(ORBIT_STORAGE_KEY, JSON.stringify({ yaw: Math.round(Number(yaw) * 100) / 100, pitch: Math.round(Number(pitch) * 100) / 100 })); } catch (e) {}
}
function scheduleOrbitSave() {
    if (_orbitSaveTimer) clearTimeout(_orbitSaveTimer);
    _orbitSaveTimer = setTimeout(function () { _orbitSaveTimer = 0; saveLocalOrbit(_orbitTargetYaw, _orbitTargetPitch); }, 250);
}
function loadOffsets() {
    secureFetch('/api/media/offset').then(function (r) { return r.json(); }).then(function (d) { if (typeof d.offset === 'number') LYRIC_OFFSET = d.offset; renderOffsets(); }).catch(function () {});
    secureFetch('/api/music/offset').then(function (r) { return r.json(); }).then(function (d) {
        if (typeof d.spectrum_offset_ms === 'number') SPECTRUM_OFFSET_MS = d.spectrum_offset_ms;
        if (typeof d.beat_lead_ms === 'number') BEAT_LEAD_MS = d.beat_lead_ms;
        if (typeof d.auto_calibrate === 'boolean') AUTO_CALIBRATE = d.auto_calibrate;
        applyMusicRenderOptions(d); renderOffsets();
    }).catch(function () {});
    var localOrbit = loadLocalOrbit();
    if (localOrbit) {
        setOrbitTarget(localOrbit.yaw == null ? 0 : localOrbit.yaw, localOrbit.pitch == null ? ORBIT_PITCH_DEFAULT : localOrbit.pitch);
        applySceneOrbit(true);
    }
}
function applyCoverPaletteFromData(data, token) {
    if (!data) return;
    var rgb = data.cover_palette_rgb;
    if (!Array.isArray(rgb) || rgb.length < 3) return;
    var r = Math.max(0, Math.min(255, Math.round(Number(rgb[0]) || 0)));
    var g = Math.max(0, Math.min(255, Math.round(Number(rgb[1]) || 0)));
    var b = Math.max(0, Math.min(255, Math.round(Number(rgb[2]) || 0)));
    var paintToken = (token || '') + '|' + r + ',' + g + ',' + b;
    if (paintToken === _lastPaletteToken) return;
    function toRgba(src, alpha) {
        if (!Array.isArray(src) || src.length < 3) return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
        return 'rgba(' + Math.max(0, Math.min(255, Math.round(Number(src[0]) || r))) + ',' +
            Math.max(0, Math.min(255, Math.round(Number(src[1]) || g))) + ',' +
            Math.max(0, Math.min(255, Math.round(Number(src[2]) || b))) + ',' + alpha + ')';
    }
    _coverTone = { r: r, g: g, b: b };
    document.documentElement.style.setProperty('--cover-rgb', r + ', ' + g + ', ' + b);
    document.documentElement.style.setProperty('--cover-1', toRgba(data.cover_palette_1 || rgb, 0.13));
    document.documentElement.style.setProperty('--cover-2', toRgba(data.cover_palette_2 || rgb, 0.09));
    document.documentElement.style.setProperty('--cover-3', toRgba(data.cover_palette_3 || rgb, 0.1));
    _lastPaletteToken = paintToken;
    invalidateSpectrumPaintCache();
}
function applyCoverBackground(bg, url) {
    if (!bg || !url) return;
    if (bg.dataset.coverUrl !== url) { bg.dataset.coverUrl = url; bg.style.backgroundImage = 'url("' + url + '")'; }
    bg.classList.add('on'); document.body.classList.add('has-cover');
}
function ambientCoverUrl(identity, version) {
    var params = [];
    if (identity) params.push('id=' + encodeURIComponent(identity));
    if (version) params.push('v=' + encodeURIComponent(String(version)));
    return '/api/media/cover/ambient' + (params.length ? ('?' + params.join('&')) : '');
}
function setCover(url, playing, force, options) {
    options = options || {};
    var identity = options.identity || url || '';
    var version = options.version || 0;
    var ambientUrl = options.ambientUrl || (url ? ambientCoverUrl(identity, version) : '');
    var disc = document.getElementById('coverDisc');
    var img = document.getElementById('coverImg');
    var bg = document.getElementById('coverBg');
    if (!url) {
        if (disc) disc.hidden = true;
        if (bg) { bg.classList.remove('on'); bg.style.backgroundImage = ''; delete bg.dataset.coverUrl; }
        document.body.classList.remove('has-cover');
        if (img) { img.removeAttribute('src'); img.onload = null; }
        _lastPaletteToken = ''; _lastAmbientIdentity = '';
        return;
    }
    var sameSrc = img && img.getAttribute('src') === url;
    var sourceChanged = !sameSrc || !!force;
    var ambientChanged = !!force || identity !== _lastAmbientIdentity || (bg && bg.dataset.coverUrl !== ambientUrl);
    if (ambientChanged && ambientUrl) { _lastAmbientIdentity = identity; applyCoverBackground(bg, ambientUrl); }
    if (img && sourceChanged) { img.onload = null; img.onerror = function () {}; img.src = url; }
    if (disc) { disc.hidden = false; disc.classList.toggle('playing', !!playing); }
    if (!ambientChanged && bg && !bg.classList.contains('on') && ambientUrl) applyCoverBackground(bg, ambientUrl);
}
function syncMediaClock(data) {
    var newPos = Number(data.position || 0);
    var serverTs = Number(data.server_ts || 0);
    var nowSec = Date.now() / 1000;
    if (serverTs > 0) {
        var skew = Math.max(-1.5, Math.min(1.5, nowSec - serverTs));
        _mediaServerTs = serverTs; _mediaStartTime = nowSec - newPos - skew;
    } else { _mediaServerTs = 0; _mediaStartTime = nowSec - newPos; }
    _mediaPosition = newPos;
    if (data.duration != null) _mediaDuration = Number(data.duration) || _mediaDuration;
    _lastPositionSource = data.position_source || _lastPositionSource || 'none';
}
function applyLyricFrame(data) {
    if (!data) return;
    if (typeof data.lyric_offset === 'number') {
        var nextOffset = Number(data.lyric_offset);
        if (nextOffset !== LYRIC_OFFSET) { LYRIC_OFFSET = nextOffset; renderOffsets(); }
    }
    if (data.duration != null) _mediaDuration = Number(data.duration) || _mediaDuration;
    if (typeof data.playing === 'boolean') _mediaPlaying = data.playing;
    else if (data.status) _mediaPlaying = (data.status === 'playing');
    if (data.track_key && data.track_key !== _mediaTrackKey && data.title) {
        _mediaTitle = data.title || _mediaTitle; _mediaArtist = data.artist || _mediaArtist;
        _mediaTrackKey = data.track_key; _lastLyricIdx = -1; resetLyricSlots();
        var titleEl = document.getElementById('trackTitle'); var artistEl = document.getElementById('trackArtist');
        if (titleEl) titleEl.textContent = _mediaTitle || '--';
        if (artistEl) artistEl.textContent = _mediaArtist || '';
    }
    var prevPos = _mediaPosition; syncMediaClock(data);
    if (prevPos - Number(data.position || 0) > 5) _lastLyricIdx = -1;
    _mediaLyricIndex = (typeof data.lyric_index === 'number') ? data.lyric_index : -1;
    _mediaNextLyricIndex = (typeof data.next_lyric_index === 'number') ? data.next_lyric_index : -1;
    _mediaLyricScroll = Math.max(0, Math.min(1, Number(data.lyric_scroll || 0)));
    _mediaLyricLineProgress = Math.max(0, Math.min(1, Number(data.lyric_line_progress || 0)));
    updateLyricLine(false, { lyric: data.lyric || '', nextLyric: data.next_lyric || '', scroll: _mediaLyricScroll, lineProgress: _mediaLyricLineProgress });
}
function applyMedia(data) {
    var titleEl = document.getElementById('trackTitle');
    var artistEl = document.getElementById('trackArtist');
    var curEl = document.getElementById('lyricCurrent');
    var nextEl = document.getElementById('lyricNext');
    if (!data || data.status === 'idle' || data.status === 'error' || !data.title) {
        titleEl.textContent = '--'; artistEl.textContent = '等待媒体会话…';
        if (curEl) curEl.textContent = '未在播放'; if (nextEl) nextEl.textContent = '';
        _mediaPlaying = false; _mediaTitle = ''; _mediaTrackKey = ''; _mediaLyrics = [];
        _mediaLyricIndex = -1; _mediaNextLyricIndex = -1; _mediaLyricScroll = 0; _mediaLyricLineProgress = 0;
        _lastLyricIdx = -1; resetLyricSlots(); _coverVersion = 0; _coverUrl = ''; _coverIdentity = ''; setCover('', false);
        var pf = document.getElementById('progressFill'); var pt = document.getElementById('posText'); var dt = document.getElementById('durText');
        if (pf) pf.style.width = '0%'; if (pt) pt.textContent = '0:00'; if (dt) dt.textContent = '0:00';
        return;
    }
    titleEl.textContent = data.title; artistEl.textContent = data.artist || '';
    _mediaPlaying = (data.status === 'playing'); _mediaArtist = data.artist || '';
    if (typeof data.lyric_offset === 'number') { LYRIC_OFFSET = Number(data.lyric_offset); renderOffsets(); }
    var trackKey = [data.song_id == null ? '' : String(data.song_id), data.title || '', data.artist || '', data.album || ''].join('\u001f');
    var isNewSong = (trackKey !== _mediaTrackKey);
    if (data.has_cover && data.cover_url) {
        var coverIdentity = data.cover_identity || data.cover_url;
        var coverChanged = isNewSong || coverIdentity !== _coverIdentity;
        applyCoverPaletteFromData(data, coverIdentity);
        if (coverChanged) {
            _coverVersion = data.cover_version || 0; _coverUrl = data.cover_url; _coverIdentity = coverIdentity;
            setCover(data.cover_url, data.status === 'playing', true, { identity: coverIdentity, version: data.cover_version || 0 });
        } else {
            var discKeep = document.getElementById('coverDisc');
            if (discKeep && !discKeep.hidden) discKeep.classList.toggle('playing', data.status === 'playing');
        }
    } else if (isNewSong || _coverUrl) {
        _coverVersion = 0; _coverUrl = ''; _coverIdentity = ''; setCover('', data.status === 'playing');
    }
    if (isNewSong) {
        _mediaTitle = data.title; _mediaTrackKey = trackKey; _mediaLyrics = data.lyrics || [];
        _mediaDuration = data.duration || 0; _lastLyricIdx = -1; resetLyricSlots();
    } else if (data.lyrics && data.lyrics.length) {
        _mediaLyrics = data.lyrics; if (data.duration) _mediaDuration = data.duration;
    }
    var prevPos = _mediaPosition; syncMediaClock(data);
    if (prevPos - Number(data.position || 0) > 5) _lastLyricIdx = -1;
    if (typeof data.lyric_index === 'number') _mediaLyricIndex = data.lyric_index;
    if (typeof data.next_lyric_index === 'number') _mediaNextLyricIndex = data.next_lyric_index;
    if (typeof data.lyric_scroll === 'number') _mediaLyricScroll = data.lyric_scroll;
    if (typeof data.lyric_line_progress === 'number') _mediaLyricLineProgress = data.lyric_line_progress;
    updateLyricLine(!!isNewSong || _lastLyricIdx < 0, {
        lyric: data.lyric || '', nextLyric: data.next_lyric || '',
        scroll: _mediaLyricScroll, lineProgress: _mediaLyricLineProgress
    });
}
function currentPosSec() { if (_mediaPlaying) return Math.max(0, Date.now() / 1000 - _mediaStartTime); return _mediaPosition; }
function currentEffPosSec() { return currentPosSec() + Number(LYRIC_OFFSET || 0); }
function lyricSlots() { return [document.getElementById('lyricCurrent'), document.getElementById('lyricNext')]; }
function resetLyricSlots() {
    _lastLyricIdx = -1; _lyricActiveSlot = 0; _lyricSlotPending = false; _lyricFadeToken += 1;
    _lyricSlotState = [{ idx: null, role: 'active', raw: '' }, { idx: null, role: 'next', raw: '' }];
    lyricSlots().forEach(function (slot, i) {
        if (!slot) return;
        slot.classList.remove('is-active', 'is-next', 'is-fading', 'marquee', 'marquee-done', 'pulse', 'long');
        slot.classList.add(i === 0 ? 'lyric-slot-a' : 'lyric-slot-b');
        delete slot.dataset.rawLyric; delete slot.dataset.scrollDistance;
    });
}
function countEffectiveChars(text) { return Array.from(String(text || '').replace(/\s/g, '')).length; }
function fitLyricText(el, text, role) {
    if (!el) return 0;
    var length = countEffectiveChars(text), scale = 1;
    if (role === 'active') {
        if (length > 52) scale = 0.72; else if (length > 38) scale = 0.78; else if (length > 26) scale = 0.86; else if (length > 16) scale = 0.94;
    } else {
        if (length > 32) scale = 0.78; else if (length > 22) scale = 0.88; else if (length > 14) scale = 0.94;
    }
    el.style.setProperty('--lyric-scale', scale.toFixed(2));
    el.dataset.lyricLength = String(length);
    el.classList.toggle('long', length > 10);
    return length;
}
function setLyricDensity(text) {
    var length = countEffectiveChars(text), compact = length > 0 && length <= 15;
    if (compact === _lyricCompact) return compact;
    _lyricCompact = compact;
    var stage = document.querySelector('.cinema-lyric');
    if (stage) stage.classList.toggle('compact', compact);
    return compact;
}
function ensureLyricInner(slot) {
    var inner = slot.querySelector('.lyric-scroll-inner');
    if (!inner) { inner = document.createElement('span'); inner.className = 'lyric-scroll-inner'; slot.textContent = ''; slot.appendChild(inner); }
    return inner;
}
function measureLyricScroll(slot, inner) {
    slot.dataset.scrollDistance = String(Math.ceil(Math.max(0, inner.scrollWidth - slot.clientWidth)));
}
function setLyricSlot(slotIndex, lyricIndex, role, posSec, force, options) {
    options = options || {};
    var slot = lyricSlots()[slotIndex]; if (!slot) return;
    var rawText = '';
    if (options.text != null && options.text !== '') rawText = String(options.text);
    else if (lyricIndex != null) rawText = String((_mediaLyrics[lyricIndex] && _mediaLyrics[lyricIndex][1]) || '');
    var inner = ensureLyricInner(slot);
    var state = _lyricSlotState[slotIndex] || { idx: null, role: '', raw: '' };
    var changed = force || state.idx !== lyricIndex || state.raw !== rawText || state.role !== role || slot.dataset.rawLyric !== rawText;
    var isActive = role === 'active';
    var longLine = isActive && rawText && countEffectiveChars(rawText) > 10;
    slot.classList.toggle('is-active', isActive);
    slot.classList.toggle('is-next', !isActive);
    slot.classList.toggle('marquee', longLine);
    slot.classList.add(slotIndex === 0 ? 'lyric-slot-a' : 'lyric-slot-b');
    if (changed) {
        _lyricSlotState[slotIndex] = { idx: lyricIndex, role: role, raw: rawText };
        slot.dataset.rawLyric = rawText; inner.textContent = rawText; inner.style.transform = 'translate3d(0,0,0)';
        fitLyricText(slot, rawText, role); measureLyricScroll(slot, inner); slot.classList.remove('marquee-done');
    }
    if (!longLine) { inner.style.transform = 'translate3d(0,0,0)'; slot.classList.remove('marquee-done'); return; }
    var progress = (typeof options.scroll === 'number') ? Math.max(0, Math.min(1, options.scroll)) : 0;
    var distance = Number(slot.dataset.scrollDistance || 0);
    inner.style.transform = 'translate3d(' + (-distance * progress).toFixed(1) + 'px,0,0)';
    slot.classList.toggle('marquee-done', progress >= 0.995);
}
function fadeSlotTo(slotIndex, lyricIndex, posSec) {
    if (_lyricSlotPending) return;
    var slot = lyricSlots()[slotIndex]; if (!slot) return;
    if ((_lyricSlotState[slotIndex] || {}).idx === lyricIndex && (_lyricSlotState[slotIndex] || {}).role === 'next') return;
    _lyricSlotPending = true; var token = ++_lyricFadeToken; slot.classList.add('is-fading');
    setTimeout(function () {
        if (token !== _lyricFadeToken) return;
        setLyricSlot(slotIndex, lyricIndex, 'next', posSec, true);
        slot.classList.remove('is-fading'); _lyricSlotPending = false;
    }, 260);
}
function updateLyricLine(force, frame) {
    frame = frame || {};
    var prevEl = document.getElementById('lyricPrev'); if (prevEl) prevEl.hidden = true;
    var pos = currentPosSec(), posEff = currentEffPosSec(), dur = _mediaDuration || 0;
    if (dur > 0 && pos > dur + 1) pos = pos % Math.max(dur, 1);
    var fill = document.getElementById('progressFill');
    var posText = document.getElementById('posText');
    var durText = document.getElementById('durText');
    if (fill) fill.style.width = (dur > 0 ? Math.min(100, (pos / dur) * 100) : 0).toFixed(2) + '%';
    if (posText) posText.textContent = fmtTime(pos);
    if (durText) durText.textContent = fmtTime(dur);
    var hasTable = !!(_mediaLyrics && _mediaLyrics.length);
    var idx = (typeof _mediaLyricIndex === 'number') ? _mediaLyricIndex : -1;
    var nextIdx = (typeof _mediaNextLyricIndex === 'number') ? _mediaNextLyricIndex : -1;
    var backendScroll = (typeof frame.scroll === 'number') ? frame.scroll : _mediaLyricScroll;
    var lineProgress = (typeof frame.lineProgress === 'number') ? frame.lineProgress : _mediaLyricLineProgress;
    var currentText = frame.lyric || (hasTable && idx >= 0 ? ((_mediaLyrics[idx] && _mediaLyrics[idx][1]) || '') : '');
    var nextText = frame.nextLyric || (hasTable && nextIdx >= 0 ? ((_mediaLyrics[nextIdx] && _mediaLyrics[nextIdx][1]) || '') : '');
    if (!hasTable && !currentText && !_mediaTitle) {
        if (_lastLyricIdx !== -2) { resetLyricSlots(); _lastLyricIdx = -2; }
        setLyricDensity(''); return;
    }
    if ((!hasTable && !currentText) || idx < 0) {
        if (_lastLyricIdx !== -3 || force) {
            _lastLyricIdx = -3; _lyricActiveSlot = 0;
            setLyricDensity(_mediaTitle || currentText || '');
            setLyricSlot(0, null, 'active', posEff, true, { text: _mediaTitle || '' });
            setLyricSlot(1, nextIdx >= 0 ? nextIdx : null, 'next', posEff, true, { text: nextText || _mediaArtist || '暂无歌词' });
        }
        return;
    }
    if (idx !== _lastLyricIdx) {
        _lyricFadeToken += 1; _lyricSlotPending = false; _lastLyricIdx = idx;
        _lyricActiveSlot = Math.abs(idx) % 2; setLyricDensity(currentText);
        setLyricSlot(_lyricActiveSlot, idx, 'active', posEff, true, { text: currentText, scroll: backendScroll });
        var otherSlot = 1 - _lyricActiveSlot;
        if (nextIdx == null || nextIdx < 0) nextIdx = idx > 0 ? idx - 1 : null;
        setLyricSlot(otherSlot, nextIdx, 'next', posEff, true, { text: nextText });
        var activeEl = lyricSlots()[_lyricActiveSlot];
        if (activeEl) { activeEl.classList.remove('pulse'); void activeEl.offsetWidth; activeEl.classList.add('pulse'); }
    } else {
        setLyricSlot(_lyricActiveSlot, idx, 'active', posEff, !!force, { text: currentText, scroll: backendScroll });
        if (lineProgress >= 1 / 3 && nextIdx != null && nextIdx >= 0) {
            var other = 1 - _lyricActiveSlot;
            if ((_lyricSlotState[other] || {}).idx !== nextIdx) fadeSlotTo(other, nextIdx, posEff);
        }
    }
}
var _lastSpecBadgeKey = '';
function setSpecBadge(text, cls) {
    var key = text + '\u0000' + (cls || '');
    if (key === _lastSpecBadgeKey) return;
    _lastSpecBadgeKey = key;
    var el = document.getElementById('specBadge');
    if (!el) return;
    el.textContent = text;
    el.className = 'badge soft' + (cls ? ' ' + cls : '');
}
function applySpectrum(data) {
    if (!data) return;
    if (data.offsets) {
        if (typeof data.offsets.spectrum_offset_ms === 'number') SPECTRUM_OFFSET_MS = data.offsets.spectrum_offset_ms;
        if (typeof data.offsets.beat_lead_ms === 'number') BEAT_LEAD_MS = data.offsets.beat_lead_ms;
        if (typeof data.offsets.auto_calibrate === 'boolean') AUTO_CALIBRATE = data.offsets.auto_calibrate;
        applyMusicRenderOptions(data.offsets); renderOffsets();
    }
    var sourceTs = Number(data.ts || 0);
    if (sourceTs && sourceTs <= _lastSpectrumSourceTs) return;
    if (sourceTs) _lastSpectrumSourceTs = sourceTs;
    _specAvailable = !!data.available;
    _specBins = data.bins || [];
    _specPeaks = data.peaks || data.bins || [];
    refreshRenderSpectrumBins();
    _specRms = Number(data.rms || 0); _specBass = Number(data.bass || 0); _specMid = Number(data.mid || 0);
    _specHigh = Number(data.high || 0); _specOnset = Number(data.onset || 0);
    _specEnergy = Number(data.energy || _specRms || 0); _specRawRms = Number(data.raw_rms || 0);
    _specSilent = !!data.silent || (_specAvailable && _specRawRms > 0 && _specRawRms < 0.004 && _specEnergy < 0.03);
    if (!_specAvailable) {
        if (data.enabled === false) setSpecBadge('频谱关闭', 'warn');
        else if (data.error) setSpecBadge('频谱不可用', 'bad');
        else setSpecBadge('频谱待机', '');
    } else if (_specSilent) setSpecBadge('静音/无输出', 'warn');
    else setSpecBadge('Loopback 频谱', '');
    if (data.beat && !_specSilent) _pendingBeats.push(performance.now() + Math.max(0, (SPECTRUM_OFFSET_MS || 0) + (BEAT_LEAD_MS || 0)));
}
function triggerBeat() {
    _lastBeatAt = performance.now(); _pulse = 1; _cameraPunch = Math.min(1, _cameraPunch + 0.35);
    document.body.classList.add('beat'); setTimeout(function () { document.body.classList.remove('beat'); }, 150);
}
function processPendingBeats() {
    var now = performance.now();
    while (_pendingBeats.length && _pendingBeats[0] <= now) { _pendingBeats.shift(); triggerBeat(); }
    if (_specAvailable && !_specSilent && ((_specBass > 0.42 && _specOnset > 0.28) || (_specEnergy > 0.55 && _specOnset > 0.35))) {
        if (now - _lastBeatAt > 160 && ((SPECTRUM_OFFSET_MS || 0) + (BEAT_LEAD_MS || 0) >= 0)) triggerBeat();
    }
}
var fx = document.getElementById('fxCanvas');
var fxCtx = fx ? fx.getContext('2d') : null;
var spec = document.getElementById('specCanvas');
var specCtx = spec ? spec.getContext('2d') : null;
var ring = document.getElementById('ringCanvas');
function compactSpectrumBins(source, targetCount) {
    if (!source || !source.length) return [];
    var count = Math.max(1, Math.min(source.length, Math.round(targetCount || source.length)));
    if (count >= source.length) return source.slice();
    var result = new Array(count);
    for (var i = 0; i < count; i++) {
        var start = Math.floor(i * source.length / count);
        var end = Math.max(start + 1, Math.floor((i + 1) * source.length / count));
        var total = 0; for (var j = start; j < end; j++) total += Number(source[j] || 0);
        result[i] = total / (end - start);
    }
    return result;
}
function refreshRenderSpectrumBins() {
    var bars = _visualProfile ? _visualProfile.spectrumBars : 24;
    _renderSpecBins = compactSpectrumBins(_specBins, bars);
    _renderSpecPeaks = compactSpectrumBins(_specPeaks, bars);
}
function resizeCanvases() {
    refreshVisualProfile();
    if (!spec || !specCtx) return;
    var renderRatio = _visualProfile.pixelRatio;
    if (fx && fxCtx) {
        fx.style.width = window.innerWidth + 'px'; fx.style.height = window.innerHeight + 'px';
        if (_visualProfile.lite) { fx.width = 1; fx.height = 1; }
        else {
            fx.width = Math.round(window.innerWidth * renderRatio);
            fx.height = Math.round(window.innerHeight * renderRatio);
            fxCtx.setTransform(renderRatio, 0, 0, renderRatio, 0, 0);
        }
    }
    var parent = spec.parentElement;
    var rect = parent ? parent.getBoundingClientRect() : { width: window.innerWidth, height: 180 };
    _specLogicalWidth = Math.max(320, Math.round(rect.width || 320));
    _specLogicalHeight = Math.max(72, Math.round(rect.height || 110));
    var spectrumRatio = _visualProfile.spectrumPixelRatio;
    spec.width = Math.max(1, Math.round(_specLogicalWidth * spectrumRatio));
    spec.height = Math.max(1, Math.round(_specLogicalHeight * spectrumRatio));
    spec.style.width = _specLogicalWidth + 'px';
    spec.style.height = _specLogicalHeight + 'px';
    specCtx.setTransform(spectrumRatio, 0, 0, spectrumRatio, 0, 0);
    invalidateSpectrumPaintCache();
    if (ring) { ring.width = 1; ring.height = 1; }
}
window.addEventListener('resize', resizeCanvases);
function easeBins(src, dest, attack, release) {
    if (!src || !src.length) return dest || [];
    if (!dest || dest.length !== src.length) return src.slice();
    for (var i = 0; i < src.length; i++) {
        var t = src[i] || 0, c = dest[i] || 0;
        dest[i] = t > c ? (c * (1 - attack) + t * attack) : (c * (1 - release) + t * release);
    }
    return dest;
}
function ensureSpectrumPaintCache(w, h) {
    var key = [w, h, _visualProfile.lite ? 1 : 0, _coverTone.r, _coverTone.g, _coverTone.b].join(':');
    if (_spectrumPaintCache && _spectrumPaintCache.key === key) return _spectrumPaintCache;
    var ink = { r: Math.round(26 + _coverTone.r * 0.43), g: Math.round(28 + _coverTone.g * 0.43), b: Math.round(34 + _coverTone.b * 0.43) };
    function color(alpha, lift) {
        return 'rgba(' + Math.round(ink.r + (240 - ink.r) * (lift || 0)) + ',' +
            Math.round(ink.g + (246 - ink.g) * (lift || 0)) + ',' +
            Math.round(ink.b + (255 - ink.b) * (lift || 0)) + ',' + alpha + ')';
    }
    var cache = { key: key, barSolid: color(0.84, 0.38), peak: color(0.95, 0.88), capGlow: color(0.48, 0.96), line: color(0.96, 0.96), baseSolid: color(0.68, 0.6) };
    if (!_visualProfile.lite) {
        var horizon = h * 0.9;
        cache.barFill = specCtx.createLinearGradient(0, h * 0.06, 0, horizon);
        cache.barFill.addColorStop(0, color(0.94, 0.64));
        cache.barFill.addColorStop(0.55, color(0.64, 0.28));
        cache.barFill.addColorStop(1, color(0.16, 0.03));
        cache.fieldFill = specCtx.createLinearGradient(0, h * 0.05, 0, horizon);
        cache.fieldFill.addColorStop(0, color(0, 0.38));
        cache.fieldFill.addColorStop(0.7, color(0.2, 0.14));
        cache.fieldFill.addColorStop(1, color(0.03, 0));
        cache.baseFill = specCtx.createLinearGradient(0, horizon, w, horizon);
        cache.baseFill.addColorStop(0, 'rgba(255,255,255,0)');
        cache.baseFill.addColorStop(0.5, color(0.7, 0.64));
        cache.baseFill.addColorStop(1, 'rgba(255,255,255,0)');
    }
    _spectrumPaintCache = cache; return cache;
}
function drawSpectrum() {
    if (!specCtx || !_visualProfile) return;
    var w = _specLogicalWidth || 640, h = _specLogicalHeight || 190;
    specCtx.clearRect(0, 0, w, h);
    var rawBins = _renderSpecBins.length ? _renderSpecBins : new Array(_visualProfile.lite ? 24 : 48).fill(0);
    var rawPeaks = _renderSpecPeaks.length ? _renderSpecPeaks : rawBins;
    var attack = _visualProfile.lite ? 0.9 : 0.92;
    var release = _visualProfile.lite ? 0.28 : 0.2;
    _smoothDisplayBins = easeBins(rawBins, _smoothDisplayBins, attack, release);
    _smoothDisplayPeaks = easeBins(rawPeaks, _smoothDisplayPeaks, 0.95, _visualProfile.lite ? 0.06 : 0.04);
    var bins = _smoothDisplayBins.length ? _smoothDisplayBins : rawBins;
    var peaks = _smoothDisplayPeaks.length ? _smoothDisplayPeaks : bins;
    var n = Math.max(1, bins.length);
    var horizon = h * 0.9, maxHeight = h * 0.88, step = w / n;
    var barWidth = Math.max(2.5, Math.min(_visualProfile.lite ? 22 : 16, step * (_visualProfile.lite ? 0.62 : 0.52)));
    var paints = ensureSpectrumPaintCache(w, h);
    var levels = new Array(n), crests = new Array(n);
    function shapeLevel(v) {
        v = Math.max(0, Math.min(1, Number(v) || 0));
        var x = Math.pow(v, 1.3);
        x = Math.max(0, (x - 0.07) / 0.93);
        x = Math.pow(x, 0.74);
        return Math.max(0, Math.min(1, x * 1.4));
    }
    for (var i = 0; i < n; i++) { levels[i] = shapeLevel(bins[i]); crests[i] = Math.max(levels[i], shapeLevel(peaks[i])); }
    if (!_visualProfile.lite) {
        specCtx.beginPath();
        for (var g = 0; g < n; g++) {
            var gx = g * step + step * 0.5, gy = horizon - crests[g] * maxHeight * 0.92;
            if (g === 0) specCtx.moveTo(gx, gy); else specCtx.lineTo(gx, gy);
        }
        specCtx.lineTo(w, horizon); specCtx.lineTo(0, horizon); specCtx.closePath();
        specCtx.fillStyle = paints.fieldFill; specCtx.fill();
    }
    for (var b = 0; b < n; b++) {
        var level = levels[b]; if (level < 0.012) continue;
        var barHeight = Math.max(2, level * maxHeight);
        var x = b * step + (step - barWidth) * 0.5, y = horizon - barHeight;
        specCtx.fillStyle = _visualProfile.lite ? paints.barSolid : paints.barFill;
        specCtx.fillRect(x, y, barWidth, barHeight);
        var peak = crests[b]; if (peak < 0.03) continue;
        var capY = Math.min(horizon - peak * maxHeight, y);
        var capH = Math.max(2, h * (_visualProfile.lite ? 0.014 : 0.012));
        specCtx.fillStyle = paints.peak;
        specCtx.fillRect(x, capY - capH * 0.2, barWidth, capH);
        if (!_visualProfile.lite && peak > 0.58) {
            specCtx.fillStyle = paints.capGlow;
            specCtx.fillRect(x + barWidth * 0.2, capY - capH * 1.25, barWidth * 0.6, Math.max(1, capH * 0.5));
        }
    }
    if (n >= 2) {
        var lift = Math.max(3, barWidth * 0.45);
        function crestY(index) { return horizon - crests[index] * maxHeight - lift; }
        if (!_visualProfile.lite) {
            specCtx.beginPath();
            for (var p = 0; p < n; p++) {
                var px = p * step + step * 0.5, py = crestY(p);
                if (p === 0) specCtx.moveTo(px, py);
                else {
                    var prevX = (p - 1) * step + step * 0.5, prevY = crestY(p - 1);
                    var cpx = (prevX + px) * 0.5, cpy = Math.min(prevY, py) - Math.abs(prevY - py) * 0.16;
                    specCtx.quadraticCurveTo(cpx, cpy, px, py);
                }
            }
            specCtx.strokeStyle = paints.capGlow; specCtx.lineWidth = 3.6 + _specRms * 2;
            specCtx.lineJoin = 'round'; specCtx.lineCap = 'round';
            specCtx.globalAlpha = 0.26; specCtx.stroke(); specCtx.globalAlpha = 1;
        }
        specCtx.beginPath();
        for (var q = 0; q < n; q++) {
            var qx = q * step + step * 0.5, qy = crestY(q);
            if (q === 0) specCtx.moveTo(qx, qy);
            else {
                var pqx = (q - 1) * step + step * 0.5, pqy = crestY(q - 1);
                var cqxx = (pqx + qx) * 0.5, cqyy = Math.min(pqy, qy) - Math.abs(pqy - qy) * 0.16;
                specCtx.quadraticCurveTo(cqxx, cqyy, qx, qy);
            }
        }
        specCtx.strokeStyle = paints.line;
        specCtx.lineWidth = (_visualProfile.lite ? 1.9 : 2.2) + _specRms * 1.4;
        specCtx.lineJoin = 'round'; specCtx.lineCap = 'round'; specCtx.stroke();
    }
    specCtx.fillStyle = _visualProfile.lite ? paints.baseSolid : paints.baseFill;
    specCtx.fillRect(w * 0.04, horizon, w * 0.92, Math.max(1, h * 0.01));
}
function clampOrbit(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
function updateOrbitBadge() {
    var badge = document.getElementById('orbitBadge'); var hint = document.getElementById('orbitHint');
    var y = Math.round(_orbitYaw), p = Math.round(_orbitPitch);
    if (badge) badge.textContent = '3D ' + y + '° / ' + p + '°';
    if (hint) hint.textContent = '左右 ' + y + '° · 俯仰 ' + p + '°';
}
function applySceneOrbit(immediate) {
    _orbitYaw = clampOrbit(_orbitYaw, -ORBIT_YAW_MAX, ORBIT_YAW_MAX);
    _orbitPitch = clampOrbit(_orbitPitch, -ORBIT_PITCH_MAX, ORBIT_PITCH_MAX);
    _orbitTargetYaw = clampOrbit(_orbitTargetYaw, -ORBIT_YAW_MAX, ORBIT_YAW_MAX);
    _orbitTargetPitch = clampOrbit(_orbitTargetPitch, -ORBIT_PITCH_MAX, ORBIT_PITCH_MAX);
    if (immediate) { _orbitYaw = _orbitTargetYaw; _orbitPitch = _orbitTargetPitch; }
    var scene = document.getElementById('scene3d');
    if (scene) scene.style.transform = 'rotateX(' + _orbitPitch.toFixed(2) + 'deg) rotateY(' + _orbitYaw.toFixed(2) + 'deg)';
    updateOrbitBadge();
}
function setOrbitTarget(yaw, pitch, opts) {
    opts = opts || {};
    _orbitTargetYaw = clampOrbit(yaw, -ORBIT_YAW_MAX, ORBIT_YAW_MAX);
    _orbitTargetPitch = clampOrbit(pitch, -ORBIT_PITCH_MAX, ORBIT_PITCH_MAX);
    if (opts.persist) scheduleOrbitSave();
}
function nudgeOrbit(dyaw, dpitch) { setOrbitTarget(_orbitTargetYaw + dyaw, _orbitTargetPitch + dpitch, { persist: true }); }
function resetOrbit() { setOrbitTarget(0, ORBIT_PITCH_DEFAULT, { persist: true }); }
function tickOrbit() {
    var ease = _orbitDragging ? 1 : 0.18;
    var moving = _orbitDragging || Math.abs(_orbitTargetYaw - _orbitYaw) >= 0.02 || Math.abs(_orbitTargetPitch - _orbitPitch) >= 0.02;
    _orbitYaw += (_orbitTargetYaw - _orbitYaw) * ease; _orbitPitch += (_orbitTargetPitch - _orbitPitch) * ease;
    if (Math.abs(_orbitTargetYaw - _orbitYaw) < 0.02) _orbitYaw = _orbitTargetYaw;
    if (Math.abs(_orbitTargetPitch - _orbitPitch) < 0.02) _orbitPitch = _orbitTargetPitch;
    _cameraPunch *= 0.88; if (_cameraPunch < 0.01) _cameraPunch = 0;
    if (!_visualProfile || !_visualProfile.lite || moving || _cameraPunch > 0) applySceneOrbit(false);
}
function setupOrbitControls() {
    var viewport = document.getElementById('sceneViewport'); if (!viewport) return;
    function onPointerDown(e) {
        if (e.target && e.target.closest && e.target.closest('.corner-menu, .topbar, a, button, input, select, textarea')) return;
        _orbitDragging = true; _orbitLastX = e.clientX; _orbitLastY = e.clientY; viewport.classList.add('dragging');
        try { viewport.setPointerCapture(e.pointerId); } catch (err) {}
    }
    function onPointerMove(e) {
        if (!_orbitDragging) return;
        var dx = e.clientX - _orbitLastX, dy = e.clientY - _orbitLastY;
        _orbitLastX = e.clientX; _orbitLastY = e.clientY;
        setOrbitTarget(_orbitTargetYaw + dx * 0.18, _orbitTargetPitch - dy * 0.16); applySceneOrbit(true);
    }
    function onPointerUp(e) {
        if (!_orbitDragging) return; _orbitDragging = false; viewport.classList.remove('dragging');
        try { viewport.releasePointerCapture(e.pointerId); } catch (err) {}
        scheduleOrbitSave();
    }
    viewport.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);
    window.addEventListener('pointercancel', onPointerUp);
    viewport.addEventListener('dblclick', function () { resetOrbit(); });
    var resetBtn = document.getElementById('orbitResetBtn');
    if (resetBtn) resetBtn.addEventListener('click', function (e) { e.preventDefault(); resetOrbit(); });
    applySceneOrbit(true);
}
function startFrameLoop() {
    if (document.hidden || _frameRequestId) return;
    _lastStageTickAt = 0; _lastSpectrumRenderAt = 0; _lastVisualRenderAt = 0;
    _frameRequestId = requestAnimationFrame(frameLoop);
}
function stopFrameLoop() { if (_frameRequestId) cancelAnimationFrame(_frameRequestId); _frameRequestId = 0; }
function frameLoop(now) {
    _frameRequestId = 0; if (document.hidden || !_visualProfile) return;
    _frameRequestId = requestAnimationFrame(frameLoop);
    now = now || performance.now();
    if (now - _lastStageTickAt < _visualProfile.stageTickFrameMs) return;
    _lastStageTickAt = now; tickOrbit(); processPendingBeats();
    if (now - _lastLyricSyncAt >= 80) {
        var fill = document.getElementById('progressFill'); var posText = document.getElementById('posText'); var durText = document.getElementById('durText');
        var pos = currentPosSec(), dur = _mediaDuration || 0;
        if (fill) fill.style.width = (dur > 0 ? Math.min(100, (pos / dur) * 100) : 0).toFixed(2) + '%';
        if (posText) posText.textContent = fmtTime(pos); if (durText) durText.textContent = fmtTime(dur);
        _lastLyricSyncAt = now;
    }
    if (now - _lastSpectrumRenderAt >= _visualProfile.spectrumFrameMs) { drawSpectrum(); _lastSpectrumRenderAt = now; }
}
var _ws = null, _wsRetry = 0, _wsReconnectTimer = 0, _spectrumSubscribed = false;
function setConnBadge(text, cls) {
    var el = document.getElementById('connBadge'); if (!el) return;
    el.textContent = text; el.className = 'badge' + (cls ? ' ' + cls : '');
}
function updateSpectrumSubscription(force) {
    if (!_ws || _ws.readyState !== 1) return;
    if (document.hidden) {
        if (_spectrumSubscribed) { _ws.send(JSON.stringify({ type: 'subscribe', channel: 'spectrum', active: false })); _spectrumSubscribed = false; }
        return;
    }
    if (!_visualProfile) { try { refreshVisualProfile(); } catch (e) {} }
    var fps = (_visualProfile && _visualProfile.spectrumFps) || 24;
    if (!_spectrumSubscribed || force) {
        _ws.send(JSON.stringify({ type: 'subscribe', channel: 'spectrum', active: true, fps: fps }));
        _spectrumSubscribed = true;
    }
}
function connectWs() {
    if (_ws && (_ws.readyState === 0 || _ws.readyState === 1)) return;
    var proto = location.protocol === 'https:' ? 'wss' : 'ws';
    _ws = new WebSocket(proto + '://' + location.host + '/ws');
    _ws.onopen = function () {
        _wsRetry = 0; if (_wsReconnectTimer) clearTimeout(_wsReconnectTimer); _wsReconnectTimer = 0;
        setConnBadge('已连接');
        _ws.send(JSON.stringify({ type: 'report', page: 'music' }));
        try { _ws.send(JSON.stringify({ type: 'subscribe', channel: 'lyric', active: true })); } catch (e) {}
        updateSpectrumSubscription(true);
        _ws.send(JSON.stringify({ type: 'init' }));
    };
    _ws.onclose = function () {
        _spectrumSubscribed = false; if (document.hidden) return;
        setConnBadge('重连中', 'warn');
        var delay = Math.min(8000, 800 + _wsRetry * 700); _wsRetry += 1;
        if (_wsReconnectTimer) clearTimeout(_wsReconnectTimer);
        _wsReconnectTimer = setTimeout(function () { _wsReconnectTimer = 0; connectWs(); }, delay);
    };
    _ws.onerror = function () { setConnBadge('连接异常', 'bad'); };
    _ws.onmessage = function (ev) {
        var msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
        if (!msg || !msg.type) return;
        if (msg.type === 'reload') location.reload();
        else if (msg.type === 'navigate') location.href = msg.url || (msg.page === 'music' ? '/music' : '/');
        else if (msg.type === 'media') applyMedia(msg.data || {});
        else if (msg.type === 'lyric') applyLyricFrame(msg.data || {});
        else if (msg.type === 'spectrum') applySpectrum(msg.data || {});
        else if (msg.type === 'font') applyStageFont(msg.data || {});
        else if (msg.type === 'music_offset') {
            if (typeof msg.data.spectrum_offset_ms === 'number') SPECTRUM_OFFSET_MS = msg.data.spectrum_offset_ms;
            if (typeof msg.data.beat_lead_ms === 'number') BEAT_LEAD_MS = msg.data.beat_lead_ms;
            if (typeof msg.data.auto_calibrate === 'boolean') AUTO_CALIBRATE = msg.data.auto_calibrate;
            applyMusicRenderOptions(msg.data || {}); renderOffsets();
        }
    };
}
function handleStageVisibilityChange() {
    if (document.hidden) { stopFrameLoop(); return; }
    if (_heavyStageReady) startFrameLoop();
    if (_ws && _ws.readyState === 1) updateSpectrumSubscription(true); else connectWs();
}
document.addEventListener('visibilitychange', handleStageVisibilityChange);
window.addEventListener('pagehide', stopFrameLoop);
window.addEventListener('beforeunload', stopFrameLoop);
document.addEventListener('keydown', function (e) {
    if (e.target && /input|textarea/i.test(e.target.tagName)) return;
    if (!e.shiftKey && (e.code === 'ArrowLeft' || e.code === 'ArrowRight' || e.code === 'ArrowUp' || e.code === 'ArrowDown')) {
        e.preventDefault();
        if (e.code === 'ArrowLeft') nudgeOrbit(-ORBIT_STEP, 0);
        if (e.code === 'ArrowRight') nudgeOrbit(ORBIT_STEP, 0);
        if (e.code === 'ArrowUp') nudgeOrbit(0, -ORBIT_STEP);
        if (e.code === 'ArrowDown') nudgeOrbit(0, ORBIT_STEP);
        return;
    }
    if (e.code === 'Space') { e.preventDefault(); playerCtl('toggle'); }
    else if (e.shiftKey && e.code === 'ArrowRight') playerCtl('next');
    else if (e.shiftKey && e.code === 'ArrowLeft') playerCtl('prev');
    else if (e.key === 'r' || e.key === 'R') resetOrbit();
});
(function setupMenu() {
    var dots = document.getElementById('menuDots'); var panel = document.getElementById('menuPanel');
    if (dots && panel) {
        dots.addEventListener('click', function (e) { e.stopPropagation(); panel.hidden = !panel.hidden; });
        document.addEventListener('click', function (e) { if (!panel.hidden && !panel.contains(e.target) && e.target !== dots) panel.hidden = true; });
    }
})();
document.body.classList.add('stage-booting');
secureFetch('/api/font').then(function (r) { return r.json(); }).then(applyStageFont).catch(function () {});
loadOffsets(); setupOrbitControls();
function finishStageBoot() {
    if (_heavyStageReady || document.hidden) return;
    _heavyStageReady = true; resizeCanvases(); startFrameLoop();
    if (_ws && _ws.readyState === 1) updateSpectrumSubscription(true); else connectWs();
    document.body.classList.remove('stage-booting'); document.body.classList.add('stage-ready');
}
connectWs();
requestAnimationFrame(function () { requestAnimationFrame(function () { setTimeout(finishStageBoot, 40); }); });
setInterval(function () {
    if (document.hidden) return;
    var wsLive = _ws && _ws.readyState === 1;
    if (!wsLive) secureFetch('/api/media').then(function (r) { return r.json(); }).then(applyMedia).catch(function () {});
    if (!_heavyStageReady) return;
    if (!wsLive || (!_specAvailable && !_lastSpectrumSourceTs)) {
        if (wsLive) updateSpectrumSubscription(true);
        secureFetch('/api/music/spectrum').then(function (r) { return r.json(); }).then(applySpectrum).catch(function () {});
    }
}, 1000);
