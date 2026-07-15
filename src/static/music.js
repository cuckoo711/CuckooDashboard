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

/* Use the same configured font file as the dashboard. */
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
    secureFetch('/api/player/' + action, { method: 'POST' }).catch(function (e) {
        console.error('[player]', e);
    });
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
// 0 keeps the automatic device profile; settings can pin either value.
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
var _lastPositionSource = 'none';
var _lastLyricIdx = -1;
var _coverVersion = 0;
var _coverUrl = '';

var _specBins = [];
var _specPeaks = [];
var _renderSpecBins = [];
var _renderSpecPeaks = [];
var _lastSpectrumSourceTs = 0;
var _specRms = 0;
var _specBass = 0;
var _specMid = 0;
var _specHigh = 0;
var _specOnset = 0;
var _specEnergy = 0;
var _specRawRms = 0;
var _specSilent = true;
var _specAvailable = false;
var _pendingBeats = [];
var _lastBeatAt = 0;
var _smoothDisplayBins = [];
var _smoothDisplayPeaks = [];
var _ringAngle = 0;
var _pulse = 0;
var _particles = [];
var _constellationNodes = [];
var _constellationWidth = 0;
var _constellationHeight = 0;
var _shockwaves = [];
var _visualClock = 0;
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
    // H618-class kiosk boards are identified by their four cores / low memory.
    var lite = reduceMotion || cores <= 4 || memory <= 4 || (dpr > 1.4 && pixels > 1200000);
    var manualFps = clampRenderOption(SPECTRUM_RENDER_FPS, 12, 60);
    var manualBars = clampRenderOption(SPECTRUM_RENDER_BARS, 12, 96);
    var spectrumFps = manualFps || (lite ? 24 : 60);
    var spectrumBars = manualBars || (lite ? 24 : 48);
    var pixelRatio = lite ? 1 : Math.min(1.5, dpr);
    return {
        lite: lite,
        pixelRatio: pixelRatio,
        // The spectrum receives the only meaningful animation budget on ARM.
        spectrumPixelRatio: lite ? 0.75 : pixelRatio,
        spectrumFps: spectrumFps,
        spectrumBars: spectrumBars,
        spectrumFrameMs: 1000 / spectrumFps,
        stageTickFrameMs: 1000 / spectrumFps,
        // Decorative canvases are completely skipped in the lite path.
        frameMs: lite ? Infinity : 50,
        nodeMin: lite ? 0 : 28,
        nodeMax: lite ? 0 : 58,
        linkBudget: lite ? 0 : 180,
        particleCap: lite ? 0 : 70,
        shockwaveCap: lite ? 0 : 5,
        orbitSegments: lite ? 0 : 96
    };
}

function invalidateSpectrumPaintCache() {
    _spectrumPaintCache = null;
}

function refreshVisualProfile() {
    _visualProfile = buildVisualProfile();
    document.body.classList.toggle('performance-lite', _visualProfile.lite);
    refreshRenderSpectrumBins();
    invalidateSpectrumPaintCache();
}

/* 3D orbit controls */
var ORBIT_YAW_MAX = 45;     // left/right clamp (deg)
var ORBIT_PITCH_MAX = 30;   // up/down clamp (deg)
var ORBIT_PITCH_DEFAULT = 14;
var ORBIT_STEP = 2;         // arrow key step
var _orbitYaw = 0;
var _orbitPitch = ORBIT_PITCH_DEFAULT;
var _orbitTargetYaw = 0;
var _orbitTargetPitch = ORBIT_PITCH_DEFAULT;
var _cameraPunch = 0;
var _orbitDragging = false;
var _orbitLastX = 0;
var _orbitLastY = 0;


var _calibrating = false;
var _calibMsg = '';
var _calibSamples = 0;

/* ── offsets API ── */
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
    SPECTRUM_RENDER_FPS = nextFps;
    SPECTRUM_RENDER_BARS = nextBars;
    _lastStageTickAt = 0;
    _lastSpectrumRenderAt = 0;
    resizeCanvases();
    updateSpectrumSubscription(true);
}

function renderCalibUi() {
    var badge = document.getElementById('calibBadge');
    var hint = document.getElementById('calibHint');
    var msg = document.getElementById('calibMsg');
    var btn = document.getElementById('calibBtn');
    if (badge) {
        badge.hidden = !_calibrating && !_calibMsg;
        badge.textContent = _calibrating ? ('校准 ' + _calibSamples + '/4') : '校准完成';
        badge.className = 'badge' + (_calibrating ? ' live' : ' soft');
    }
    if (hint) hint.hidden = !(_calibrating || _calibMsg);
    if (msg) msg.textContent = _calibMsg || '跟随鼓点连点 4~6 次（空格/点击 ⊙）';
    if (btn) {
        btn.classList.toggle('armed', !!_calibrating);
    }
    document.body.classList.toggle('calibrating', !!_calibrating);
}

function adjLyric(delta) {
    secureFetch('/api/media/offset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ delta: delta })
    }).then(function (r) { return r.json(); }).then(function (d) {
        LYRIC_OFFSET = Number(d.offset || 0);
        renderOffsets();
    }).catch(function () {});
}

function pushMusicOffsets(payload) {
    secureFetch('/api/music/offset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    }).then(function (r) { return r.json(); }).then(function (d) {
        if (typeof d.spectrum_offset_ms === 'number') SPECTRUM_OFFSET_MS = d.spectrum_offset_ms;
        if (typeof d.beat_lead_ms === 'number') BEAT_LEAD_MS = d.beat_lead_ms;
        if (typeof d.auto_calibrate === 'boolean') AUTO_CALIBRATE = d.auto_calibrate;
        renderOffsets();
    }).catch(function () {});
}

function adjSpectrum(delta) { pushMusicOffsets({ delta_spectrum_offset_ms: delta }); }
function adjBeat(delta) { pushMusicOffsets({ delta_beat_lead_ms: delta }); }

function loadOffsets() {
    secureFetch('/api/media/offset').then(function (r) { return r.json(); }).then(function (d) {
        if (typeof d.offset === 'number') LYRIC_OFFSET = d.offset;
        renderOffsets();
    }).catch(function () {});

    secureFetch('/api/music/offset').then(function (r) { return r.json(); }).then(function (d) {
        if (typeof d.spectrum_offset_ms === 'number') SPECTRUM_OFFSET_MS = d.spectrum_offset_ms;
        if (typeof d.beat_lead_ms === 'number') BEAT_LEAD_MS = d.beat_lead_ms;
        if (typeof d.auto_calibrate === 'boolean') AUTO_CALIBRATE = d.auto_calibrate;
        renderOffsets();
    }).catch(function () {});
}

/* ── calibration ── */
function toggleCalibrate() {
    if (_calibrating) {
        calibAction('cancel');
        return;
    }
    calibAction('start');
}

function calibAction(action, extra) {
    var body = Object.assign({ action: action }, extra || {});
    return secureFetch('/api/music/calibrate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    }).then(function (r) { return r.json(); }).then(function (d) {
        applyCalibStatus(d);
        if (d.offsets) {
            if (typeof d.offsets.spectrum_offset_ms === 'number') SPECTRUM_OFFSET_MS = d.offsets.spectrum_offset_ms;
            if (typeof d.offsets.beat_lead_ms === 'number') BEAT_LEAD_MS = d.offsets.beat_lead_ms;
            renderOffsets();
        } else if (typeof d.suggested_beat_lead_ms === 'number' && d.applied) {
            BEAT_LEAD_MS = d.suggested_beat_lead_ms;
            renderOffsets();
        }
        return d;
    }).catch(function (e) {
        console.error('[calib]', e);
    });
}

function applyCalibStatus(d) {
    if (!d) return;
    _calibrating = !!d.active;
    _calibSamples = Number(d.samples || 0);
    _calibMsg = d.message || '';
    if (d.offsets) {
        if (typeof d.offsets.spectrum_offset_ms === 'number') SPECTRUM_OFFSET_MS = d.offsets.spectrum_offset_ms;
        if (typeof d.offsets.beat_lead_ms === 'number') BEAT_LEAD_MS = d.offsets.beat_lead_ms;
    }
    renderOffsets();
}

function tapCalibrate() {
    if (!_calibrating) return;
    calibAction('tap', { client_ts: Date.now() / 1000 });
    // local feedback
    triggerBeat();
}

/* ── cover palette ── */
var _lastPaletteToken = '';

function applyCoverBackground(bg, url) {
    if (!bg || !url) return;
    // This is intentionally the only write path for the full-screen background.
    // It runs on a new cover, never inside the per-frame visual loop.
    if (bg.dataset.coverUrl !== url) {
        bg.dataset.coverUrl = url;
        bg.style.backgroundImage = 'url("' + url + '")';
    }
    bg.classList.add('on');
    document.body.classList.add('has-cover');
}

function setCover(url, playing, force) {
    var disc = document.getElementById('coverDisc');
    var img = document.getElementById('coverImg');
    var bg = document.getElementById('coverBg');
    if (!url) {
        if (disc) disc.hidden = true;
        if (bg) {
            bg.classList.remove('on');
            bg.style.backgroundImage = '';
            delete bg.dataset.coverUrl;
        }
        document.body.classList.remove('has-cover');
        if (img) {
            img.removeAttribute('src');
            img.onload = null;
        }
        _lastPaletteToken = '';
        return;
    }

    var sameSrc = img && img.getAttribute('src') === url;
    var sourceChanged = !sameSrc || !!force;
    // The full-screen cover is independent of palette sampling. Updating it at
    // the moment a new identity arrives prevents a cached/late <img> load from
    // leaving the background on the previous song.
    if (sourceChanged) applyCoverBackground(bg, url);
    if (img && sourceChanged) {
        // Only reload the artwork when its identity changes — avoids full-page flash.
        var requestedUrl = url;
        img.onload = function () {
            if (img.getAttribute('src') !== requestedUrl) return;
            if (_lastPaletteToken !== requestedUrl) {
                _lastPaletteToken = requestedUrl;
                extractCoverPalette(img);
            }
        };
        img.onerror = function () {
            // The new background request has already been issued; retain the
            // current foreground artwork rather than clearing the whole stage.
        };
        img.src = requestedUrl;
    }
    if (disc) {
        disc.hidden = false;
        disc.classList.toggle('playing', !!playing);
    }
    // If the same artwork is already loaded, only restore a cleared background.
    if (!sourceChanged && bg && !bg.classList.contains('on')) applyCoverBackground(bg, url);
}

function extractCoverPalette(img) {
    try {
        var c = document.createElement('canvas');
        var size = 32;
        c.width = size;
        c.height = size;
        var ctx = c.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(img, 0, 0, size, size);
        var data = ctx.getImageData(0, 0, size, size).data;
        var buckets = {};
        for (var i = 0; i < data.length; i += 4) {
            var r = data[i], g = data[i + 1], b = data[i + 2], a = data[i + 3];
            if (a < 200) continue;
            // skip near-white / near-black
            var max = Math.max(r, g, b), min = Math.min(r, g, b);
            if (max < 30 || min > 235) continue;
            var rr = Math.round(r / 24) * 24;
            var gg = Math.round(g / 24) * 24;
            var bb = Math.round(b / 24) * 24;
            var key = rr + ',' + gg + ',' + bb;
            buckets[key] = (buckets[key] || 0) + 1 + (max - min) * 0.02;
        }
        var ranked = Object.keys(buckets).map(function (k) {
            return { k: k, n: buckets[k] };
        }).sort(function (a, b) { return b.n - a.n; });
        if (!ranked.length) return;
        function toRgba(k, a) {
            var p = k.split(',');
            return 'rgba(' + p[0] + ',' + p[1] + ',' + p[2] + ',' + a + ')';
        }
        var c1 = ranked[0].k;
        var c2 = (ranked[1] && ranked[1].k) || c1;
        var c3 = (ranked[2] && ranked[2].k) || c2;
        var p = c1.split(',');
        // Keep the artwork responsive, but deliberately cool and desaturate it so
        // one album never turns the entire stage into a loud color theme.
        _coverTone = {
            r: Math.round((+p[0]) * 0.42 + 184 * 0.58),
            g: Math.round((+p[1]) * 0.42 + 198 * 0.58),
            b: Math.round((+p[2]) * 0.42 + 235 * 0.58)
        };
        document.documentElement.style.setProperty('--cover-rgb', _coverTone.r + ', ' + _coverTone.g + ', ' + _coverTone.b);
        document.documentElement.style.setProperty('--cover-1', toRgba(c1, 0.13));
        document.documentElement.style.setProperty('--cover-2', toRgba(c2, 0.09));
        document.documentElement.style.setProperty('--cover-3', toRgba(c3, 0.1));
    } catch (e) {
        // cross-origin or empty
    }
}

/* ── media + lyrics ── */
function applyMedia(data) {
    var titleEl = document.getElementById('trackTitle');
    var artistEl = document.getElementById('trackArtist');
    var prevEl = document.getElementById('lyricPrev');
    var curEl = document.getElementById('lyricCurrent');
    var nextEl = document.getElementById('lyricNext');

    if (!data || data.status === 'idle' || data.status === 'error' || !data.title) {
        titleEl.textContent = '--';
        artistEl.textContent = '等待媒体会话…';
        if (prevEl) prevEl.textContent = '';
        if (curEl) curEl.textContent = '未在播放';
        if (nextEl) nextEl.textContent = '';
        _mediaPlaying = false;
        _mediaTitle = '';
        _mediaTrackKey = '';
        _mediaLyrics = [];
        _lastLyricIdx = -1;
        _coverVersion = 0;
        _coverUrl = '';
        setCover('', false);
        document.getElementById('progressFill').style.width = '0%';
        document.getElementById('posText').textContent = '0:00';
        document.getElementById('durText').textContent = '0:00';
        return;
    }

    titleEl.textContent = data.title;
    artistEl.textContent = data.artist || '';
    _mediaPlaying = (data.status === 'playing');
    _mediaArtist = data.artist || '';

    var trackKey = [
        data.song_id == null ? '' : String(data.song_id),
        data.title || '',
        data.artist || '',
        data.album || ''
    ].join('\u001f');
    var isNewSong = (trackKey !== _mediaTrackKey);

    // Cover: only touch DOM when track or cover identity changes.
    if (data.has_cover && data.cover_url) {
        var coverChanged = (
            isNewSong ||
            data.cover_url !== _coverUrl ||
            (data.cover_version && data.cover_version !== _coverVersion)
        );
        if (coverChanged) {
            _coverVersion = data.cover_version || 0;
            _coverUrl = data.cover_url;
            setCover(data.cover_url, data.status === 'playing', coverChanged);
        } else {
            // Only update spin state; never rewrite image src.
            var discKeep = document.getElementById('coverDisc');
            if (discKeep && !discKeep.hidden) {
                discKeep.classList.toggle('playing', data.status === 'playing');
            }
        }
    } else if (isNewSong || _coverUrl) {
        _coverVersion = 0;
        _coverUrl = '';
        setCover('', data.status === 'playing');
    }

    if (isNewSong) {
        _mediaTitle = data.title;
        _mediaTrackKey = trackKey;
        _mediaLyrics = data.lyrics || [];
        _mediaDuration = data.duration || 0;
        _mediaPosition = data.position || 0;
        _mediaStartTime = Date.now() / 1000 - _mediaPosition;
        _lastLyricIdx = -1;
        _lastPositionSource = data.position_source || 'none';
    } else {
        var newPos = Number(data.position || 0);
        if (data.position_source === 'api') {
            var looped = (_lastPositionSource === 'api' && newPos < _mediaPosition - 5);
            _mediaPosition = newPos;
            if (data.duration) _mediaDuration = data.duration;
            if (looped) _lastLyricIdx = -1;
            _mediaStartTime = Date.now() / 1000 - _mediaPosition;
            _lastPositionSource = 'api';
        } else if (data.position_source === 'uia' && data.progress_ratio != null) {
            var estPos = _mediaPlaying ? (Date.now() / 1000 - _mediaStartTime) : _mediaPosition;
            if (Math.abs(newPos - estPos) > 0.3 || newPos < _mediaPosition - 5) {
                _mediaPosition = newPos;
                _mediaStartTime = Date.now() / 1000 - _mediaPosition;
                if (newPos < _mediaPosition - 5) _lastLyricIdx = -1;
            }
            if (data.duration) _mediaDuration = data.duration;
            _lastPositionSource = 'uia';
        } else if (data.duration) {
            _mediaDuration = data.duration;
        }
    }

    // spin state already handled in cover branch / isNewSong path
}

function currentPosSec() {
    if (_mediaPlaying) return Math.max(0, Date.now() / 1000 - _mediaStartTime);
    return _mediaPosition;
}

function fitLyricText(el, text) {
    if (!el) return;
    var length = Array.from(String(text || '').replace(/\s/g, '')).length;
    var scale = 1;
    if (length > 34) scale = 0.58;
    else if (length > 27) scale = 0.66;
    else if (length > 21) scale = 0.76;
    else if (length > 16) scale = 0.86;
    else if (length > 11) scale = 0.94;
    el.style.setProperty('--lyric-scale', scale.toFixed(2));
    el.dataset.lyricLength = String(length);
}

function updateLyricLine() {
    var prevEl = document.getElementById('lyricPrev');
    var curEl = document.getElementById('lyricCurrent');
    var nextEl = document.getElementById('lyricNext');
    var fill = document.getElementById('progressFill');
    var posText = document.getElementById('posText');
    var durText = document.getElementById('durText');

    var pos = currentPosSec();
    var dur = _mediaDuration || 0;
    if (dur > 0 && pos > dur + 1) {
        pos = pos % Math.max(dur, 1);
    }
    // Progress controls remain in the DOM for compatibility but are intentionally
    // hidden on the stage, so avoid per-frame text/style writes here.

    if (!_mediaLyrics.length) {
        if (_mediaTitle) {
            if (prevEl) prevEl.textContent = '';
            if (curEl) {
                curEl.textContent = _mediaTitle;
                fitLyricText(curEl, _mediaTitle);
            }
            if (nextEl) nextEl.textContent = _mediaArtist || '暂无歌词';
        }
        return;
    }

    var posSec = pos + LYRIC_OFFSET;
    var idx = -1;
    for (var i = 0; i < _mediaLyrics.length; i++) {
        if (_mediaLyrics[i][0] <= posSec) idx = i;
        else break;
    }
    if (idx < 0) {
        if (prevEl) prevEl.textContent = '';
        if (curEl) {
            curEl.textContent = _mediaLyrics[0][1] || '';
            fitLyricText(curEl, curEl.textContent);
        }
        if (nextEl) nextEl.textContent = _mediaLyrics[1] ? (_mediaLyrics[1][1] || '') : '';
        return;
    }
    if (idx !== _lastLyricIdx) {
        _lastLyricIdx = idx;
        if (prevEl) prevEl.textContent = idx > 0 ? (_mediaLyrics[idx - 1][1] || '') : '';
        if (curEl) {
            curEl.textContent = _mediaLyrics[idx][1] || '';
            fitLyricText(curEl, curEl.textContent);
            curEl.classList.remove('pulse');
            void curEl.offsetWidth;
            curEl.classList.add('pulse');
        }
        if (nextEl) nextEl.textContent = _mediaLyrics[idx + 1] ? (_mediaLyrics[idx + 1][1] || '') : '';
    }
}

/* ── spectrum + visuals ── */
function setSpecBadge(text, cls) {
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
        renderOffsets();
    }
    if (data.calibration) {
        _calibrating = !!data.calibration.active;
        _calibSamples = Number(data.calibration.samples || 0);
        if (data.calibration.message) _calibMsg = data.calibration.message;
        renderCalibUi();
    }

    _specAvailable = !!data.available;
    _specBins = data.bins || [];
    _specPeaks = data.peaks || data.bins || [];
    _specRms = Number(data.rms || 0);
    _specBass = Number(data.bass || 0);
    _specMid = Number(data.mid || 0);
    _specHigh = Number(data.high || 0);
    _specOnset = Number(data.onset || 0);
    _specEnergy = Number(data.energy || _specRms || 0);
    _specRawRms = Number(data.raw_rms || 0);
    _specSilent = !!data.silent || (_specAvailable && _specRawRms > 0 && _specRawRms < 0.004 && _specEnergy < 0.03);

    if (!_specAvailable) {
        var err = data.error || '';
        if (data.enabled === false) setSpecBadge('频谱关闭', 'warn');
        else if (err) setSpecBadge('频谱不可用', 'bad');
        else setSpecBadge('频谱待机', '');
    } else if (_specSilent) {
        var dev = (data.device || '');
        if (/fallback|WDM2VST|Mix \(/i.test(dev) && (_specRawRms > 0 && _specRawRms < 1e-4)) setSpecBadge('采到静音源', 'bad');
        else setSpecBadge('静音/无输出', 'warn');
    } else {
        setSpecBadge('Loopback 频谱', '');
    }

    if (data.beat && !_specSilent) {
        var totalLead = (SPECTRUM_OFFSET_MS || 0) + (BEAT_LEAD_MS || 0);
        var delay = Math.max(0, -totalLead);
        _pendingBeats.push(Date.now() + delay);
    }
}

function triggerBeat() {
    var now = Date.now();
    if (now - _lastBeatAt < 90) return;
    _lastBeatAt = now;
    _pulse = 1;
    _cameraPunch = 1;
    document.body.classList.add('beat');
    // A beat becomes a short cinematic cut, not a fireworks burst.
    spawnBurst(8 + Math.floor(_specBass * 10) + Math.floor(_specEnergy * 5));
    _shockwaves.push({
        x: window.innerWidth * 0.42,
        y: window.innerHeight * 0.54,
        radius: Math.min(window.innerWidth, window.innerHeight) * 0.08,
        life: 1
    });
    if (_shockwaves.length > _visualProfile.shockwaveCap) _shockwaves.shift();
    setTimeout(function () { document.body.classList.remove('beat'); }, 150);
}

function processPendingBeats() {
    if (_specSilent) { _pendingBeats.length = 0; return; }
    var now = Date.now();
    while (_pendingBeats.length && _pendingBeats[0] <= now) {
        _pendingBeats.shift();
        triggerBeat();
    }
    if (_specAvailable && !_specSilent && ((_specBass > 0.42 && _specOnset > 0.28) || (_specEnergy > 0.55 && _specOnset > 0.35))) {
        if (now - _lastBeatAt > 160) {
            var totalLead = (SPECTRUM_OFFSET_MS || 0) + (BEAT_LEAD_MS || 0);
            if (totalLead >= 0) triggerBeat();
        }
    }
}

var fx = document.getElementById('fxCanvas');
var fxCtx = fx.getContext('2d');
var spec = document.getElementById('specCanvas');
var specCtx = spec.getContext('2d');
var ring = document.getElementById('ringCanvas');
var ringCtx = ring ? ring.getContext('2d') : null;

function resizeCanvases() {
    refreshVisualProfile();
    var renderRatio = _visualProfile.pixelRatio;
    fx.width = Math.round(window.innerWidth * renderRatio);
    fx.height = Math.round(window.innerHeight * renderRatio);
    fx.style.width = window.innerWidth + 'px';
    fx.style.height = window.innerHeight + 'px';
    fxCtx.setTransform(renderRatio, 0, 0, renderRatio, 0, 0);
    _constellationWidth = 0;
    _constellationHeight = 0;
    _constellationNodes = [];

    var rect = spec.parentElement.getBoundingClientRect();
    var sh = Math.max(72, rect.height || 110);
    spec.width = Math.round(Math.max(320, rect.width) * renderRatio);
    spec.height = Math.round(sh * renderRatio);
    spec.style.width = rect.width + 'px';
    spec.style.height = sh + 'px';
    specCtx.setTransform(renderRatio, 0, 0, renderRatio, 0, 0);

    if (ring && ringCtx) {
        var rr = ring.parentElement.getBoundingClientRect();
        var side = Math.max(160, Math.min(rr.width, rr.height)) * 1.44;
        ring.width = Math.round(side * renderRatio);
        ring.height = Math.round(side * renderRatio);
        ring.style.width = side + 'px';
        ring.style.height = side + 'px';
        ringCtx.setTransform(renderRatio, 0, 0, renderRatio, 0, 0);
    }
}
window.addEventListener('resize', resizeCanvases);

function easeBins(src, dest, attack, release) {
    if (!src || !src.length) return dest || [];
    if (!dest || dest.length !== src.length) return src.slice();
    for (var i = 0; i < src.length; i++) {
        var t = src[i] || 0;
        var c = dest[i] || 0;
        dest[i] = t > c ? (c * (1 - attack) + t * attack) : (c * (1 - release) + t * release);
    }
    return dest;
}

function visualColor(alpha, whiteMix) {
    whiteMix = whiteMix == null ? 0 : whiteMix;
    var r = Math.round(_coverTone.r + (244 - _coverTone.r) * whiteMix);
    var g = Math.round(_coverTone.g + (247 - _coverTone.g) * whiteMix);
    var b = Math.round(_coverTone.b + (255 - _coverTone.b) * whiteMix);
    return 'rgba(' + r + ',' + g + ',' + b + ',' + Math.max(0, alpha) + ')';
}

function ensureConstellation(w, h) {
    if (_constellationNodes.length && _constellationWidth === w && _constellationHeight === h) return;
    _constellationNodes = [];
    _constellationWidth = w;
    _constellationHeight = h;
    var count = Math.round(Math.max(_visualProfile.nodeMin, Math.min(_visualProfile.nodeMax, (w * h) / (_visualProfile.lite ? 48000 : 23500))));
    for (var i = 0; i < count; i++) {
        _constellationNodes.push({
            x: 0.03 + Math.random() * 0.94,
            y: 0.08 + Math.random() * 0.73,
            depth: 0.25 + Math.random() * 0.95,
            phase: Math.random() * Math.PI * 2,
            drift: 0.12 + Math.random() * 0.5,
            size: 0.45 + Math.random() * 1.15
        });
    }
}

function drawConstellation(w, h, punch) {
    ensureConstellation(w, h);
    var points = [];
    var yaw = (_orbitYaw / ORBIT_YAW_MAX) || 0;
    var pitch = (_orbitPitch - ORBIT_PITCH_DEFAULT) / ORBIT_PITCH_MAX;
    var nodeEnergy = 0.14 + _specEnergy * 0.24 + punch * 0.18;

    for (var i = 0; i < _constellationNodes.length; i++) {
        var node = _constellationNodes[i];
        var x = node.x * w + Math.sin(_visualClock * node.drift + node.phase) * 8 * node.depth + yaw * 20 * node.depth;
        var y = node.y * h + Math.cos(_visualClock * node.drift * 0.7 + node.phase) * 6 * node.depth + pitch * 12 * node.depth;
        points.push({ x: x, y: y, depth: node.depth, size: node.size });
    }

    fxCtx.save();
    fxCtx.globalCompositeOperation = _visualProfile.lite ? 'source-over' : 'screen';
    var threshold = Math.min(_visualProfile.lite ? 125 : 160, 84 + _specEnergy * 42);
    var thresholdSq = threshold * threshold;
    var linkBudget = _visualProfile.linkBudget;
    var links = 0;
    outerLinks:
    for (var a = 0; a < points.length; a++) {
        for (var b = a + 1; b < points.length; b++) {
            var dx = points[a].x - points[b].x;
            var dy = points[a].y - points[b].y;
            var distanceSq = dx * dx + dy * dy;
            if (distanceSq > thresholdSq) continue;
            var distance = Math.sqrt(distanceSq);
            var strength = (1 - distance / threshold) * (_visualProfile.lite ? 0.055 : 0.075) * Math.min(points[a].depth, points[b].depth);
            fxCtx.strokeStyle = visualColor(strength + punch * 0.015, 0.64);
            fxCtx.lineWidth = 0.45;
            fxCtx.beginPath();
            fxCtx.moveTo(points[a].x, points[a].y);
            fxCtx.lineTo(points[b].x, points[b].y);
            fxCtx.stroke();
            links += 1;
            if (links >= linkBudget) break outerLinks;
        }
    }
    for (var p = 0; p < points.length; p++) {
        var point = points[p];
        var alpha = (0.09 + nodeEnergy * 0.42) * point.depth;
        fxCtx.fillStyle = visualColor(alpha, 0.74);
        fxCtx.beginPath();
        fxCtx.arc(point.x, point.y, point.size * (0.6 + _specHigh * 0.45), 0, Math.PI * 2);
        fxCtx.fill();
    }
    fxCtx.restore();
}

function drawShockwaves() {
    for (var i = _shockwaves.length - 1; i >= 0; i--) {
        var wave = _shockwaves[i];
        wave.life -= 0.036;
        if (wave.life <= 0) { _shockwaves.splice(i, 1); continue; }
        var grow = 1 - wave.life;
        var radius = wave.radius + grow * Math.max(window.innerWidth, window.innerHeight) * 0.29;
        fxCtx.save();
        fxCtx.globalCompositeOperation = 'screen';
        fxCtx.strokeStyle = visualColor(wave.life * wave.life * 0.18, 0.86);
        fxCtx.lineWidth = 0.75 + wave.life * 0.8;
        fxCtx.beginPath();
        fxCtx.ellipse(wave.x, wave.y, radius, radius * 0.13, 0, 0, Math.PI * 2);
        fxCtx.stroke();
        fxCtx.restore();
    }
}

function spawnBurst(n) {
    var cx = window.innerWidth * 0.42;
    var cy = window.innerHeight * 0.5;
    var count = Math.max(_visualProfile.lite ? 3 : 5, Math.min(_visualProfile.lite ? 7 : 14, Math.round(n)));
    for (var i = 0; i < count; i++) {
        var angle = (Math.random() - 0.5) * Math.PI * 0.8;
        var speed = 0.8 + Math.random() * 2.6 + _specBass * 2.4;
        _particles.push({
            x: cx + (Math.random() - 0.5) * 118,
            y: cy + (Math.random() - 0.5) * 46,
            vx: Math.sin(angle) * speed,
            vy: -Math.cos(angle) * speed * 0.48,
            life: 0.48 + Math.random() * 0.35,
            size: 0.7 + Math.random() * 1.9,
            streak: Math.random() > 0.48,
            lift: Math.random() * 0.76
        });
    }
}

function drawFx() {
    var w = window.innerWidth;
    var h = window.innerHeight;
    fxCtx.clearRect(0, 0, w, h);
    _pulse *= 0.9;
    var punch = Math.max(0, _cameraPunch || 0);
    document.documentElement.style.setProperty('--beat', String(Math.max(_pulse, punch)));

    var breath = 0.16 + _specRms * 0.31 + _pulse * 0.18 + punch * 0.13;
    var glow = document.getElementById('glow');
    if (glow) {
        glow.style.opacity = String(0.1 + breath * 0.3);
        glow.style.transform = 'scale(' + (1 + _specBass * 0.035 + punch * 0.018).toFixed(3) + ')';
    }
    var galaxy = document.getElementById('galaxy');
    if (galaxy) galaxy.style.opacity = String(0.18 + _specHigh * 0.07 + _specEnergy * 0.04);

    drawConstellation(w, h, punch);
    drawShockwaves();

    // One thin light cut on a strong transient keeps the response cinematic.
    if (punch > 0.28) {
        var y = h * (0.49 + (_specHigh - 0.5) * 0.08);
        var light = fxCtx.createLinearGradient(w * 0.15, y, w * 0.85, y);
        light.addColorStop(0, 'rgba(255,255,255,0)');
        light.addColorStop(0.5, visualColor(0.08 + punch * 0.16, 0.9));
        light.addColorStop(1, 'rgba(255,255,255,0)');
        fxCtx.fillStyle = light;
        fxCtx.fillRect(w * 0.15, y, w * 0.7, 1);
    }

    var ambientChance = 0.035 + _specHigh * 0.1 + _pulse * 0.06;
    if (_visualProfile.lite) ambientChance *= 0.25;
    if (_specAvailable && !_specSilent && Math.random() < ambientChance) {
        _particles.push({
            x: w * (0.08 + Math.random() * 0.84),
            y: h * (0.18 + Math.random() * 0.54),
            vx: (Math.random() - 0.5) * 0.24,
            vy: -0.14 - Math.random() * 0.33,
            life: 0.75 + Math.random() * 0.45,
            size: 0.45 + Math.random() * 1.2,
            streak: false,
            lift: 0.45 + Math.random() * 0.45
        });
    }

    for (var i = _particles.length - 1; i >= 0; i--) {
        var particle = _particles[i];
        particle.x += particle.vx;
        particle.y += particle.vy;
        particle.life -= 0.016;
        if (particle.life <= 0) { _particles.splice(i, 1); continue; }
        var alpha = particle.life * 0.46;
        if (particle.streak) {
            fxCtx.strokeStyle = visualColor(alpha, particle.lift);
            fxCtx.lineWidth = Math.max(0.55, particle.size * 0.48);
            fxCtx.beginPath();
            fxCtx.moveTo(particle.x, particle.y);
            fxCtx.lineTo(particle.x - particle.vx * 7, particle.y - particle.vy * 7);
            fxCtx.stroke();
        } else {
            fxCtx.fillStyle = visualColor(alpha, particle.lift);
            fxCtx.beginPath();
            fxCtx.arc(particle.x, particle.y, particle.size, 0, Math.PI * 2);
            fxCtx.fill();
        }
    }
    if (_particles.length > _visualProfile.particleCap) _particles.splice(0, _particles.length - _visualProfile.particleCap);
}

function drawRing() {
    if (!ring || !ringCtx) return;
    var w = ring.clientWidth || 300;
    var h = ring.clientHeight || 300;
    ringCtx.clearRect(0, 0, w, h);
    var cx = w / 2;
    var cy = h / 2;
    var baseR = Math.min(w, h) * 0.255;
    var bins = _smoothDisplayBins.length ? _smoothDisplayBins : (_specBins.length ? _specBins : new Array(48).fill(0.03));
    var n = Math.max(1, bins.length);
    var punch = Math.max(0, _cameraPunch || 0);
    _ringAngle += 0.0019 + _specEnergy * 0.005 + _pulse * 0.004;

    var halo = ringCtx.createRadialGradient(cx, cy, baseR * 0.15, cx, cy, baseR * 1.92);
    halo.addColorStop(0, visualColor(0.035 + _specRms * 0.045 + punch * 0.05, 0.72));
    halo.addColorStop(0.52, visualColor(0.025 + _specMid * 0.035, 0.28));
    halo.addColorStop(1, visualColor(0, 0));
    ringCtx.fillStyle = halo;
    ringCtx.beginPath();
    ringCtx.arc(cx, cy, baseR * 1.92, 0, Math.PI * 2);
    ringCtx.fill();

    function signalAt(index) {
        var at = ((index % n) + n) % n;
        var value = bins[at] || 0;
        var prev = bins[(at - 1 + n) % n] || 0;
        var next = bins[(at + 1) % n] || 0;
        return Math.max(0, Math.min(1, value * 0.54 + prev * 0.23 + next * 0.23));
    }

    function orbit(radius, squash, phase, amplitude, alpha, dashed) {
        var segments = _visualProfile.orbitSegments;
        ringCtx.save();
        ringCtx.globalCompositeOperation = 'screen';
        ringCtx.strokeStyle = visualColor(alpha, 0.58);
        ringCtx.lineWidth = 0.65 + _specRms * 0.48;
        if (dashed) ringCtx.setLineDash([2, 8 + _specHigh * 9]);
        ringCtx.beginPath();
        for (var i = 0; i <= segments; i++) {
            var angle = phase + (i / segments) * Math.PI * 2;
            var energy = signalAt(Math.floor((i / segments) * n + phase * 5));
            var radiusNow = radius + (energy - 0.16) * amplitude + Math.sin(angle * 3 + _visualClock) * amplitude * 0.08;
            var x = cx + Math.cos(angle) * radiusNow;
            var y = cy + Math.sin(angle) * radiusNow * squash;
            if (i === 0) ringCtx.moveTo(x, y);
            else ringCtx.lineTo(x, y);
        }
        ringCtx.stroke();
        ringCtx.restore();
    }

    // Thin orbital geometry replaces the former radial-bar visualizer.
    orbit(baseR * 0.93, 0.93, _ringAngle * 0.9, baseR * 0.11, 0.1 + _specRms * 0.06, false);
    orbit(baseR * 1.22, 0.58, -_ringAngle * 0.56 + 0.72, baseR * 0.14, 0.07 + _specEnergy * 0.055, true);
    if (!_visualProfile.lite) {
        orbit(baseR * 1.52, 0.78, _ringAngle * 0.34 - 1.2, baseR * 0.1, 0.045 + _specHigh * 0.045, true);
    }

    var markerAngle = _ringAngle * 1.8 + 0.4;
    var markerRadius = baseR * (1.23 + _specEnergy * 0.04);
    var markerX = cx + Math.cos(markerAngle) * markerRadius;
    var markerY = cy + Math.sin(markerAngle) * markerRadius * 0.58;
    ringCtx.fillStyle = visualColor(0.26 + punch * 0.18, 0.86);
    ringCtx.beginPath();
    ringCtx.arc(markerX, markerY, 1.25 + _specHigh * 1.1, 0, Math.PI * 2);
    ringCtx.fill();
}

function drawSpectrum() {
    var w = spec.clientWidth || 640;
    var h = spec.clientHeight || 190;
    specCtx.clearRect(0, 0, w, h);

    // Fast enough to follow transients, with just enough release to avoid a
    // flickering LED wall. The old values deliberately made the spectrum lag.
    _smoothDisplayBins = easeBins(_specBins, _smoothDisplayBins, 0.76, 0.31);
    _smoothDisplayPeaks = easeBins(_specPeaks, _smoothDisplayPeaks, 0.9, 0.13);
    var bins = _smoothDisplayBins.length ? _smoothDisplayBins : new Array(48).fill(0);
    var peaks = _smoothDisplayPeaks.length ? _smoothDisplayPeaks : bins;
    var n = Math.max(1, bins.length);
    var horizon = h * 0.92;
    var maxHeight = h * 0.82;
    var step = w / n;
    var ink = {
        r: Math.round(26 + _coverTone.r * 0.43),
        g: Math.round(28 + _coverTone.g * 0.43),
        b: Math.round(34 + _coverTone.b * 0.43)
    };

    function spectrumColor(alpha, lift) {
        lift = lift == null ? 0 : lift;
        var r = Math.round(ink.r + (240 - ink.r) * lift);
        var g = Math.round(ink.g + (246 - ink.g) * lift);
        var b = Math.round(ink.b + (255 - ink.b) * lift);
        return 'rgba(' + r + ',' + g + ',' + b + ',' + Math.max(0, alpha) + ')';
    }

    function sample(index) {
        var v = bins[index] || 0;
        // Preserve more band contrast than the former very soft panorama.
        if (index > 0 && index < n - 1) v = v * 0.68 + (bins[index - 1] || 0) * 0.16 + (bins[index + 1] || 0) * 0.16;
        return Math.max(0, Math.min(1, Math.pow(v, 0.7)));
    }

    var fill = specCtx.createLinearGradient(0, h * 0.08, 0, horizon);
    fill.addColorStop(0, spectrumColor(0.78 + _specEnergy * 0.12, 0.57));
    fill.addColorStop(0.58, spectrumColor(0.44 + _specRms * 0.2, 0.25));
    fill.addColorStop(1, spectrumColor(0.13, 0.02));
    var field = specCtx.createLinearGradient(0, h * 0.06, 0, horizon);
    field.addColorStop(0, spectrumColor(0, 0.36));
    field.addColorStop(0.68, spectrumColor(0.16 + _specEnergy * 0.12, 0.17));
    field.addColorStop(1, spectrumColor(0.03, 0));

    specCtx.save();
    specCtx.globalCompositeOperation = 'source-over';

    // Fill the energy silhouette first so the spectrum reads from a distance.
    specCtx.beginPath();
    for (var i = 0; i < n; i++) {
        var sx = i * step + step * 0.5;
        var sy = horizon - sample(i) * maxHeight;
        if (i === 0) specCtx.moveTo(sx, sy);
        else specCtx.lineTo(sx, sy);
    }
    specCtx.lineTo(w, horizon);
    specCtx.lineTo(0, horizon);
    specCtx.closePath();
    specCtx.fillStyle = field;
    specCtx.fill();

    var barWidth = Math.max(2, Math.min(16, step * 0.68));
    for (var b = 0; b < n; b++) {
        var level = sample(b);
        if (level < 0.008) continue;
        var barHeight = Math.max(1, level * maxHeight);
        var x = b * step + (step - barWidth) * 0.5;
        var y = horizon - barHeight;
        specCtx.fillStyle = fill;
        specCtx.fillRect(x, y, barWidth, barHeight);

        var peak = Math.max(level, Math.min(1, Math.pow(peaks[b] || 0, 0.7)));
        var peakY = horizon - peak * maxHeight;
        specCtx.fillStyle = spectrumColor(0.62 + peak * 0.2, 0.72);
        specCtx.fillRect(x, peakY, barWidth, Math.max(1, h * 0.008));
    }

    // A bright contour retains the continuous waveform reading above the bars.
    specCtx.beginPath();
    for (var p = 0; p < n; p++) {
        var px = p * step + step * 0.5;
        var py = horizon - sample(p) * maxHeight;
        if (p === 0) specCtx.moveTo(px, py);
        else specCtx.lineTo(px, py);
    }
    specCtx.strokeStyle = spectrumColor(0.78 + _specEnergy * 0.14 + _pulse * 0.1, 0.84);
    specCtx.lineWidth = 1.15 + _specRms * 1.05;
    specCtx.shadowColor = spectrumColor(0.32, 0.42);
    specCtx.shadowBlur = _visualProfile.lite ? 0 : 7;
    specCtx.stroke();
    specCtx.shadowBlur = 0;

    var base = specCtx.createLinearGradient(0, horizon, w, horizon);
    base.addColorStop(0, 'rgba(255,255,255,0)');
    base.addColorStop(0.5, spectrumColor(0.5 + _specEnergy * 0.22, 0.62));
    base.addColorStop(1, 'rgba(255,255,255,0)');
    specCtx.fillStyle = base;
    specCtx.fillRect(w * 0.04, horizon, w * 0.92, Math.max(1, h * 0.009));
    specCtx.restore();
}



function clampOrbit(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
}

function updateOrbitBadge() {
    var badge = document.getElementById('orbitBadge');
    var hint = document.getElementById('orbitHint');
    var y = Math.round(_orbitYaw);
    var p = Math.round(_orbitPitch);
    if (badge) badge.textContent = '3D ' + y + '° / ' + p + '°';
    if (hint) hint.textContent = '左右 ' + y + '° · 俯仰 ' + p + '°';
}

function applySceneOrbit(immediate) {
    _orbitYaw = clampOrbit(_orbitYaw, -ORBIT_YAW_MAX, ORBIT_YAW_MAX);
    _orbitPitch = clampOrbit(_orbitPitch, -ORBIT_PITCH_MAX, ORBIT_PITCH_MAX);
    _orbitTargetYaw = clampOrbit(_orbitTargetYaw, -ORBIT_YAW_MAX, ORBIT_YAW_MAX);
    _orbitTargetPitch = clampOrbit(_orbitTargetPitch, -ORBIT_PITCH_MAX, ORBIT_PITCH_MAX);
    if (immediate) {
        _orbitYaw = _orbitTargetYaw;
        _orbitPitch = _orbitTargetPitch;
    }
    var scene = document.getElementById('scene3d');
    if (scene) {
        // Only rotate. Do NOT scale the scene — that makes the spinning cover jump forward/back.
        scene.style.transform =
            'rotateX(' + _orbitPitch.toFixed(2) + 'deg) rotateY(' + _orbitYaw.toFixed(2) + 'deg)';
    }
    // camera punch drives lights only
    var punch = Math.max(0, _cameraPunch || 0);
    var beam = document.getElementById('beam');
    if (beam) {
        beam.style.opacity = String(0.05 + (_specRms || 0) * 0.07 + punch * 0.08);
        beam.style.transform = 'scale(' + (1 + (_specBass || 0) * 0.014 + punch * 0.018).toFixed(3) + ')';
    }
    var flare = document.getElementById('coreFlare');
    if (flare) {
        flare.style.opacity = String(0.18 + (_specRms || 0) * 0.2 + punch * 0.26);
        // no translateZ changes: cover depth always remains fixed.
        flare.style.transform = 'scale(' + (1 + punch * 0.028).toFixed(3) + ')';
    }
    var floor = document.getElementById('stageFloor');
    if (floor) {
        floor.style.opacity = String(0.36 + (_specEnergy || 0) * 0.22 + punch * 0.16);
    }
    updateOrbitBadge();
}

function setOrbitTarget(yaw, pitch) {
    _orbitTargetYaw = clampOrbit(yaw, -ORBIT_YAW_MAX, ORBIT_YAW_MAX);
    _orbitTargetPitch = clampOrbit(pitch, -ORBIT_PITCH_MAX, ORBIT_PITCH_MAX);
}

function nudgeOrbit(dyaw, dpitch) {
    setOrbitTarget(_orbitTargetYaw + dyaw, _orbitTargetPitch + dpitch);
}

function resetOrbit() {
    setOrbitTarget(0, ORBIT_PITCH_DEFAULT);
}

function tickOrbit() {
    var ease = _orbitDragging ? 1 : 0.18;
    _orbitYaw += (_orbitTargetYaw - _orbitYaw) * ease;
    _orbitPitch += (_orbitTargetPitch - _orbitPitch) * ease;
    if (Math.abs(_orbitTargetYaw - _orbitYaw) < 0.02) _orbitYaw = _orbitTargetYaw;
    if (Math.abs(_orbitTargetPitch - _orbitPitch) < 0.02) _orbitPitch = _orbitTargetPitch;
    _cameraPunch *= 0.88;
    if (_cameraPunch < 0.01) _cameraPunch = 0;
    applySceneOrbit(false);
}

function setupOrbitControls() {
    var viewport = document.getElementById('sceneViewport');
    if (!viewport) return;

    function onPointerDown(e) {
        if (e.target && e.target.closest && e.target.closest('.corner-menu, .topbar, a, button, input, select, textarea')) return;
        _orbitDragging = true;
        _orbitLastX = e.clientX;
        _orbitLastY = e.clientY;
        viewport.classList.add('dragging');
        try { viewport.setPointerCapture(e.pointerId); } catch (err) {}
    }
    function onPointerMove(e) {
        if (!_orbitDragging) return;
        var dx = e.clientX - _orbitLastX;
        var dy = e.clientY - _orbitLastY;
        _orbitLastX = e.clientX;
        _orbitLastY = e.clientY;
        setOrbitTarget(_orbitTargetYaw + dx * 0.18, _orbitTargetPitch - dy * 0.16);
        applySceneOrbit(true);
    }
    function onPointerUp(e) {
        if (!_orbitDragging) return;
        _orbitDragging = false;
        viewport.classList.remove('dragging');
        try { viewport.releasePointerCapture(e.pointerId); } catch (err) {}
    }

    viewport.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);
    window.addEventListener('pointercancel', onPointerUp);
    viewport.addEventListener('dblclick', function () { resetOrbit(); });

    var resetBtn = document.getElementById('orbitResetBtn');
    if (resetBtn) resetBtn.addEventListener('click', function (e) {
        e.preventDefault();
        resetOrbit();
    });

    applySceneOrbit(true);
}

function frameLoop(now) {
    now = now || performance.now();
    tickOrbit();
    processPendingBeats();
    if (now - _lastLyricSyncAt >= 40) {
        updateLyricLine();
        _lastLyricSyncAt = now;
    }
    // Keep decorative canvases on a smaller budget. The spectrum has its own
    // faster path below because it is the primary real-time visual.
    if (now - _lastVisualRenderAt >= _visualProfile.frameMs) {
        _visualClock += 0.016 + _specEnergy * 0.008;
        drawFx();
        drawRing();
        _lastVisualRenderAt = now;
    }
    if (now - _lastSpectrumRenderAt >= _visualProfile.spectrumFrameMs) {
        drawSpectrum();
        _lastSpectrumRenderAt = now;
    }
    requestAnimationFrame(frameLoop);
}

/* ── websocket ── */
var _ws = null;
var _wsRetry = 0;
var _spectrumSubscribed = false;

function setConnBadge(text, cls) {
    var el = document.getElementById('connBadge');
    if (!el) return;
    el.textContent = text;
    el.className = 'badge' + (cls ? ' ' + cls : '');
}

function connectWs() {
    var proto = location.protocol === 'https:' ? 'wss' : 'ws';
    _ws = new WebSocket(proto + '://' + location.host + '/ws');

    _ws.onopen = function () {
        _wsRetry = 0;
        setConnBadge('已连接');
        _ws.send(JSON.stringify({ type: 'report', page: 'music' }));
        _ws.send(JSON.stringify({ type: 'subscribe', channel: 'spectrum', active: true }));
        _spectrumSubscribed = true;
        _ws.send(JSON.stringify({ type: 'init' }));
    };

    _ws.onclose = function () {
        // The server releases this WebSocket's refcount during its own disconnect
        // cleanup. Calling the REST release endpoint here caused double releases
        // during flaky LAN reconnects and could churn the WASAPI capture device.
        _spectrumSubscribed = false;
        setConnBadge('重连中', 'warn');
        var delay = Math.min(8000, 800 + _wsRetry * 700);
        _wsRetry += 1;
        setTimeout(connectWs, delay);
    };

    _ws.onerror = function () {
        setConnBadge('连接异常', 'bad');
    };

    _ws.onmessage = function (ev) {
        var msg;
        try { msg = JSON.parse(ev.data); } catch (e) { return; }
        if (!msg || !msg.type) return;
        if (msg.type === 'reload') location.reload();
        else if (msg.type === 'navigate') location.href = msg.url || (msg.page === 'music' ? '/music' : '/');
        else if (msg.type === 'media') applyMedia(msg.data || {});
        else if (msg.type === 'spectrum') applySpectrum(msg.data || {});
        else if (msg.type === 'font') applyStageFont(msg.data || {});
        else if (msg.type === 'music_offset') {
            if (typeof msg.data.spectrum_offset_ms === 'number') SPECTRUM_OFFSET_MS = msg.data.spectrum_offset_ms;
            if (typeof msg.data.beat_lead_ms === 'number') BEAT_LEAD_MS = msg.data.beat_lead_ms;
            if (typeof msg.data.auto_calibrate === 'boolean') AUTO_CALIBRATE = msg.data.auto_calibrate;
            renderOffsets();
        }
    };
}

window.addEventListener('beforeunload', function () {
    try {
        if (_spectrumSubscribed && _ws && _ws.readyState === 1) {
            _ws.send(JSON.stringify({ type: 'subscribe', channel: 'spectrum', active: false }));
            _spectrumSubscribed = false;
        }
    } catch (e) {}
});

// keyboard shortcuts (no visible player controls on stage)
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
    else if (e.key === 'Escape') {
        var panel = document.getElementById('menuPanel');
        if (panel) panel.hidden = true;
    }
});

// corner menu


(function () {
    var dots = document.getElementById('menuDots');
    var panel = document.getElementById('menuPanel');
    if (!dots || !panel) return;
    dots.addEventListener('click', function (e) {
        e.stopPropagation();
        panel.hidden = !panel.hidden;
    });
    document.addEventListener('click', function (e) {
        if (!panel.hidden && !panel.contains(e.target) && e.target !== dots) panel.hidden = true;
    });
})();

// bootstrap
secureFetch('/api/font').then(function (r) { return r.json(); }).then(applyStageFont).catch(function () {});
loadOffsets();
resizeCanvases();
setupOrbitControls();
connectWs();
frameLoop();

// REST fallback poll when WS not ready
setInterval(function () {
    if (_ws && _ws.readyState === 1) return;
    secureFetch('/api/media').then(function (r) { return r.json(); }).then(applyMedia).catch(function () {});
    secureFetch('/api/music/spectrum').then(function (r) { return r.json(); }).then(applySpectrum).catch(function () {});
}, 1000);
