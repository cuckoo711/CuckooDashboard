
/* ── XSS-safe helpers ── */
function escHtml(s) {
    var d = document.createElement('div');
    d.appendChild(document.createTextNode(s || ''));
    return d.innerHTML;
}

/* ── Health status dots ── */
var _healthServices = {};
var _vibeStatusProvider = null;

function updateVibeHealthDot() {
    var el = document.getElementById('dot-vibe');
    if (!el) return;
    var status = (_healthServices[_vibeStatusProvider] || {}).status || 'unknown';
    el.className = 'svc-dot ' + status;
}

function updateHealthDots(health) {
    _healthServices = health.services || {};
    var map = {
        'system': 'sysCard',
        'github': 'ghCard',
        'media': 'playerCard',
    };
    for (var svc in map) {
        var el = document.getElementById('dot-' + svc);
        if (!el) continue;
        var s = (_healthServices[svc] || {}).status || 'unknown';
        el.className = 'svc-dot ' + s;
    }
    updateVibeHealthDot();
    // GitHub estimated hint
    var ghHint = document.getElementById('ghHint');
    if (ghHint) {
        var gh = _healthServices.github || {};
        if (gh.details && gh.details.estimated) {
            ghHint.classList.add('visible');
        } else {
            ghHint.classList.remove('visible');
        }
    }
}

async function refreshHealth() {
    try {
        var r = await secureFetch('/api/health');
        var d = await r.json();
        updateHealthDots(d);
    } catch(e) {}
}
refreshHealth();
setInterval(refreshHealth, 15000);

var DASHBOARD_TOKEN = '';
try { DASHBOARD_TOKEN = localStorage.getItem('dashboardToken') || ''; } catch(e) {}
function secureFetch(url, options) {
    options = options || {};
    options.headers = options.headers || {};
    options.headers['X-Requested-With'] = 'CuckooDashboard';
    if (DASHBOARD_TOKEN) options.headers['X-Dashboard-Token'] = DASHBOARD_TOKEN;
    return fetch(url, options);
}
function playerCtl(action) {
    secureFetch('/api/player/' + action, {method:'POST'}).catch(function(e){ console.error('[player]', e); });
}
function fmtTok(n) {
    if (n >= 1e12) return (n/1e12).toFixed(2)+'T';
    if (n >= 1e9)  return (n/1e9).toFixed(2)+'B';
    if (n >= 1e6)  return (n/1e6).toFixed(2)+'M';
    if (n >= 1e3)  return (n/1e3).toFixed(1)+'K';
    return n.toLocaleString();
}
function fmtNum(n) { return n.toLocaleString(); }
function cssVar(name) {
    return getComputedStyle(document.body).getPropertyValue(name).trim() || getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function fmtBytes(b) {
    if (b >= 1073741824) return (b/1073741824).toFixed(1)+' GB';
    if (b >= 1048576) return (b/1048576).toFixed(1)+' MB';
    if (b >= 1024) return (b/1024).toFixed(1)+' KB';
    return b+' B';
}

/* ── 节假日数据库（2025-2026 中国法定节假日）── */
var _HOLIDAYS = {
    /* 2025 */
    '2025-01-01':'元旦','2025-01-28':'春节','2025-01-29':'春节','2025-01-30':'春节',
    '2025-01-31':'春节','2025-02-01':'春节','2025-02-02':'春节','2025-02-03':'春节',
    '2025-02-04':'春节',
    '2025-04-04':'清明节','2025-04-05':'清明节','2025-04-06':'清明节',
    '2025-05-01':'劳动节','2025-05-02':'劳动节','2025-05-03':'劳动节',
    '2025-05-04':'劳动节','2025-05-05':'劳动节',
    '2025-05-31':'端午节','2025-06-01':'端午节','2025-06-02':'端午节',
    '2025-10-01':'国庆节','2025-10-02':'国庆节','2025-10-03':'国庆节',
    '2025-10-04':'国庆节','2025-10-05':'国庆节','2025-10-06':'国庆节','2025-10-07':'国庆节',
    /* 2026 */
    '2026-01-01':'元旦','2026-01-02':'元旦','2026-01-03':'元旦',
    '2026-02-17':'春节','2026-02-18':'春节','2026-02-19':'春节',
    '2026-02-20':'春节','2026-02-21':'春节','2026-02-22':'春节','2026-02-23':'春节',
    '2026-04-05':'清明节','2026-04-06':'清明节','2026-04-07':'清明节',
    '2026-05-01':'劳动节','2026-05-02':'劳动节','2026-05-03':'劳动节',
    '2026-05-04':'劳动节','2026-05-05':'劳动节',
    '2026-06-19':'端午节','2026-06-20':'端午节','2026-06-21':'端午节',
    '2026-10-01':'国庆节','2026-10-02':'国庆节','2026-10-03':'国庆节',
    '2026-10-04':'国庆节','2026-10-05':'国庆节','2026-10-06':'国庆节','2026-10-07':'国庆节',
};
/* 调休上班日（周末但需要上班） */
var _WORKDAYS = {
    '2025-01-26':1,'2025-02-08':1,'2025-04-27':1,'2025-09-28':1,'2025-10-11':1,
    '2026-02-15':1,'2026-02-28':1,'2026-04-26':1,'2026-10-10':1,
};
var WEEKDAYS = ['周日','周一','周二','周三','周四','周五','周六'];

function getDayType(d) {
    var y = d.getFullYear(), m = d.getMonth()+1, dd = d.getDate();
    var key = y+'-'+String(m).padStart(2,'0')+'-'+String(dd).padStart(2,'0');
    var dow = d.getDay();
    if (_HOLIDAYS[key]) return {type:'holiday', label:_HOLIDAYS[key]};
    if (_WORKDAYS[key]) return {type:'workday', label:WEEKDAYS[dow]};
    if (dow===0||dow===6) return {type:'weekend', label:WEEKDAYS[dow]};
    return {type:'workday', label:WEEKDAYS[dow]};
}

/* ── 闲时倍率标签配置（后端 YAML；未加载时保持旧的 00:00-08:00 行为）── */
var _offPeakBadgeConfig = {
    enabled: true,
    ranges: [{start: '00:00', end: '08:00'}]
};

function timeToMinutes(value) {
    var match = typeof value === 'string' && /^(?:[01]\d|2[0-3]):[0-5]\d$/.exec(value);
    return match ? Number(value.slice(0, 2)) * 60 + Number(value.slice(3, 5)) : null;
}

function isInOffPeakRange(minuteOfDay, ranges) {
    if (!Array.isArray(ranges)) return false;
    return ranges.some(function(range) {
        var start = timeToMinutes(range && range.start);
        var end = timeToMinutes(range && range.end);
        if (start === null || end === null || start === end) return false;
        // start < end 是同日区间；start > end 表示跨午夜。
        return start < end
            ? minuteOfDay >= start && minuteOfDay < end
            : minuteOfDay >= start || minuteOfDay < end;
    });
}

function applyOffPeakBadgeConfig(data) {
    if (!data || typeof data !== 'object') return;
    _offPeakBadgeConfig = {
        enabled: data.enabled !== false,
        ranges: Array.isArray(data.ranges) ? data.ranges : []
    };
    tickClock();
}

async function refreshOffPeakBadgeConfig() {
    try {
        var response = await secureFetch('/api/off-peak-badge');
        if (!response.ok) throw new Error('HTTP ' + response.status);
        applyOffPeakBadgeConfig(await response.json());
    } catch (e) {
        console.warn('[off-peak] config refresh failed:', e);
    }
}

function tickClock() {
    var d = new Date();
    /* 12小时制时钟 */
    var h = d.getHours();
    var ampm = h < 12 ? 'AM' : 'PM';
    var h12 = h % 12; if (h12 === 0) h12 = 12;
    var hh = String(h12).padStart(2,'0');
    var mm = String(d.getMinutes()).padStart(2,'0');
    var ss = String(d.getSeconds()).padStart(2,'0');
    document.getElementById('hdrClock').textContent = hh+':'+mm+':'+ss;
    document.getElementById('hdrAmpm').textContent = ampm;

    /* 日期 + 星期 */
    var dayInfo = getDayType(d);
    var weekday = WEEKDAYS[d.getDay()];
    var dateStr = d.getFullYear()+'年'+(d.getMonth()+1)+'月'+d.getDate()+'日 '+weekday;
    if (dayInfo.type === 'holiday') {
        dateStr += ' · '+dayInfo.label;
    }
    document.getElementById('hdrDate').textContent = dateStr;

    /* 闲时标签：按北京时间和配置的多个区间判断。 */
    var rateEl = document.getElementById('hdrRate');
    if (!_offPeakBadgeConfig.enabled) {
        rateEl.style.display = 'none';
        return;
    }
    var bjMinutes = ((d.getUTCHours() + 8) % 24) * 60 + d.getUTCMinutes();
    rateEl.textContent = isInOffPeakRange(bjMinutes, _offPeakBadgeConfig.ranges) ? '0.8x' : '1.0x';
    rateEl.style.display = 'inline';
}

/* ── 今日消耗堆叠条 ── */
function drawTodayStacked(inMiss, outTok, cacheTok) {
    var total = inMiss + outTok + cacheTok;
    if (total <= 0) return;
    document.getElementById('segCache').style.width = (cacheTok/total*100)+'%';
    document.getElementById('segIn').style.width = (inMiss/total*100)+'%';
    document.getElementById('segOut').style.width = (outTok/total*100)+'%';
    document.getElementById('todayCachePct').textContent = (cacheTok/total*100).toFixed(1)+'%';
    document.getElementById('todayInPct').textContent = (inMiss/total*100).toFixed(1)+'%';
    document.getElementById('todayOutPct').textContent = (outTok/total*100).toFixed(1)+'%';
}

/* ── 可配置的 Token Plan 环、模型条与余额 ── */
function safeNumber(value) {
    var number = Number(value);
    return Number.isFinite(number) ? number : 0;
}

function currencyPrefix(currency) {
    var code = typeof currency === 'string' ? currency.trim().toUpperCase() : '';
    var symbols = {CNY:'¥', USD:'$', EUR:'€', GBP:'£', JPY:'¥', KRW:'₩'};
    return symbols[code] || (code ? code + ' ' : '');
}

function drawRing(pct, used, limit, itemName) {
    pct = Math.max(0, Math.min(100, safeNumber(pct)));
    used = safeNumber(used);
    limit = safeNumber(limit);
    window._lastRingArgs = [pct, used, limit];
    var C = 2*Math.PI*35;
    var remain = 100 - pct;
    var remainAmt = Math.max(0, limit - used);
    var fg = document.getElementById('ringFg');
    fg.style.strokeDashoffset = C*(1-remain/100);
    fg.style.stroke = pct>=90?cssVar('--crimson'):pct>=70?cssVar('--warn'):cssVar('--gold');
    document.getElementById('ringPct').textContent = remain.toFixed(1)+'%';
    var label = document.getElementById('ringLabel');
    label.textContent = fmtTok(remainAmt)+' 剩余';
    label.title = itemName || '';
}

function resetRing() {
    var C = 2*Math.PI*35;
    var fg = document.getElementById('ringFg');
    fg.style.strokeDashoffset = C;
    fg.style.stroke = cssVar('--ring-bg');
    document.getElementById('ringPct').textContent = '--%';
    var label = document.getElementById('ringLabel');
    label.textContent = '暂无数据';
    label.title = '';
}

var _lastModelsKey = '';
function drawModelBars(data) {
    var el = document.getElementById('modelBars');
    if (!data || !data.available || !Array.isArray(data.rows) || !data.rows.length) {
        _lastModelsKey = '';
        el.innerHTML='<div class="ld">暂无数据</div>';
        return;
    }

    var rows = data.rows.map(function(row) {
        return {
            label: String(row.label || ''),
            value: safeNumber(row.value),
            requests: Math.max(0, Math.trunc(safeNumber(row.requests)))
        };
    }).filter(function(row) { return row.label; });
    if (!rows.length) {
        _lastModelsKey = '';
        el.innerHTML='<div class="ld">暂无数据</div>';
        return;
    }

    rows.sort(function(a,b){ return b.value-a.value || a.label.localeCompare(b.label); });
    var key = String(data.provider || '')+'|'+String(data.kind || '')+'|'+String(data.currency || '')+'|'+
        rows.map(function(row){return row.label+':'+row.value+':'+row.requests;}).join('|');
    if (key === _lastModelsKey) return;
    _lastModelsKey = key;

    var mx = rows[0].value || 1;
    var fills = ['f1','f2','f3'];
    var isCurrency = data.kind === 'currency';
    el.innerHTML = rows.map(function(row, index){
        var width = Math.max(0, Math.min(100, row.value/mx*100));
        var valueText = isCurrency
            ? currencyPrefix(data.currency) + row.value.toFixed(2)
            : fmtTok(row.value);
        var fill = ' class="bar-fill '+fills[index%fills.length]+'" style="width:'+width+'%"';
        return '<div class="bar-row">'+
            '<span class="bar-name" title="'+escHtml(row.label)+'">'+escHtml(row.label)+'</span>'+
            '<div class="bar-track"><div'+fill+'></div></div>'+
            '<div class="bar-info"><div class="v">'+escHtml(valueText)+'</div><div class="s">'+fmtNum(row.requests)+' 次</div></div>'+
        '</div>';
    }).join('');
}

function isHexColor(value) {
    return typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value);
}

function renderVibeBalances(items) {
    var footer = document.getElementById('vibeBalances');
    if (!footer) return;
    while (footer.firstChild) footer.removeChild(footer.firstChild);
    if (!Array.isArray(items) || !items.length) {
        footer.hidden = true;
        return;
    }

    items.slice(0, 2).forEach(function(item) {
        var row = document.createElement('span');
        row.className = 'vibe-balance';

        var name = document.createElement('span');
        name.className = 'vibe-balance-name';
        name.textContent = String(item.name || item.provider || '');

        var dot = document.createElement('span');
        dot.className = 'vibe-balance-dot';
        var color = isHexColor(item.color) ? item.color : '#888888';
        dot.style.backgroundColor = color;
        dot.style.color = color;

        var value = document.createElement('b');
        value.className = 'vibe-balance-value';
        value.textContent = currencyPrefix(item.currency) + String(item.balance == null ? '--' : item.balance);

        row.appendChild(name);
        row.appendChild(dot);
        row.appendChild(value);
        footer.appendChild(row);
    });
    footer.hidden = false;
}

function handleVibeData(data) {
    data = data || {};
    var ring = data.ring || {};
    _vibeStatusProvider = ring.provider || null;
    updateVibeHealthDot();
    if (ring.available) {
        drawRing(ring.percent, ring.used, ring.limit, ring.item);
    } else {
        resetRing();
    }
    drawModelBars(data.model_bars || {});
    renderVibeBalances(data.balances || []);
}

/* ── 系统信息（CPU / 内存 / 独显，纵向三列）── */
function drawSystem(sys) {
    if (!sys) return;
    window._lastSystemData = sys;
    var R = 30, C = 2 * Math.PI * R;

    function ringColor(pct) { return pct >= 90 ? cssVar('--crimson') : pct >= 80 ? cssVar('--warn') : cssVar('--green'); }
    function mainRing(pct, name, sub, color) {
        // 半开口仪表盘：270度弧（底部留90度缺口），从左下(135deg)顺时针到右下
        var SVG = 84, cx = SVG/2, cy = SVG/2, RV = 34;
        var C = 2 * Math.PI * RV;
        var arcLen = C * 0.75;
        var gapLen = C - arcLen;
        var filled = (pct / 100) * arcLen;
        var clr = color || ringColor(pct);
        return '<div class="hw-main-item">' +
            '<div class="hw-main-ring" style="width:'+SVG+'px;height:'+SVG+'px;">' +
            '<svg width="'+SVG+'" height="'+SVG+'" viewBox="0 0 '+SVG+' '+SVG+'">' +
            '<circle class="hw-ring-bg" cx="'+cx+'" cy="'+cy+'" r="'+RV+'" stroke-dasharray="'+arcLen+' '+gapLen+'" transform="rotate(135 '+cx+' '+cy+')"/>' +
            '<circle class="hw-ring-fg" cx="'+cx+'" cy="'+cy+'" r="'+RV+'" stroke="'+clr+'" stroke-dasharray="'+filled+' '+(C-filled)+'" transform="rotate(135 '+cx+' '+cy+')"/>' +
            '</svg>' +
            '<div class="hw-main-center"><span class="hw-main-pct">'+pct.toFixed(0)+'%</span><span class="hw-main-sub">'+sub+'</span></div>' +
            '</div>' +
            '<span class="hw-main-name" title="'+escHtml(name)+'">'+escHtml(name)+'</span></div>';
    }

    var c = sys.cpu, m = sys.memory;
    var cpuName = (c.model||'').replace(/AMD\s+Ryzen\s+/i,'R').replace(/\(R\)/g,'').replace(/\(TM\)/g,'').replace(/CPU\s*/i,'').replace(/@\s*[\d.]+GHz/i,'').replace(/\d+\s*-?\s*Core\s*Processor/gi,'').trim();
    var freqGHz = c.freq_current >= 1000 ? (c.freq_current / 1000).toFixed(2) : c.freq_current;
    var cpuSub = freqGHz + 'GHz';

    var memLabel = m.name || ((m.type || '内存') + (m.freq ? ' ' + m.freq : ''));
    var memTotalGB = m.installed ? (m.installed / 1073741824) : (m.total / 1073741824);
    var memSub = (m.used / 1073741824).toFixed(1) + '/' + memTotalGB.toFixed(1) + 'G';

    var html = '<div class="hw-main">';
    html += mainRing(c.percent, cpuName, cpuSub);
    html += mainRing(m.percent, memLabel, memSub);
    // 只显示独立显卡，过滤掉核显
    var discreteGpus = (sys.gpus || []).filter(function(g){ return g.is_discrete; });
    if (discreteGpus.length) {
        var g = discreteGpus[0];
        var sn = g.name.replace(/AMD\s+/,'').replace(/Radeon\s+/g,'').replace(/\(TM\)/g,'').replace(/NVIDIA\s+/gi,'');
        var totalVramGB = g.vram > 0 ? (g.vram / 1073741824).toFixed(0) : '';
        var usedVramGB = g.vram_used != null && g.vram_used > 0 ? (g.vram_used / 1073741824).toFixed(1) : '';
        var vs = usedVramGB && totalVramGB ? usedVramGB + '/' + totalVramGB + 'G' : (totalVramGB ? totalVramGB + 'G' : '');
        var u = g.util!=null ? g.util : 0;
        html += mainRing(u, sn, vs);
    }
    html += '</div>';
    document.getElementById('sysMain').innerHTML = html;

    drawDisks(sys.disks || []);
    drawNetwork(sys.network);

    // 开机时间
    var upEl = document.getElementById('uptimeVal');
    if (upEl && sys.system && sys.system.uptime != null) {
        var s = sys.system.uptime;
        var d = Math.floor(s / 86400); s %= 86400;
        var h = Math.floor(s / 3600); s %= 3600;
        var m = Math.floor(s / 60);
        upEl.textContent = (d > 0 ? d+'天' : '') + h+'时'+m+'分';
    }
}

/* ── 磁盘（独立卡片，两列布局）── */
function drawDisks(disksRaw) {
    var el = document.getElementById('sysDisks');
    if (!el) return;
    var disks = disksRaw.slice().sort(function(a,b){
        var order = {'SSD':0, 'NVMe':0, 'HDD':1};
        return (order[a.type]||2) - (order[b.type]||2);
    });
    var dh = '';
    for (var j = 0; j < disks.length; j++) {
            var dk = disks[j];
            var raw = (dk.model||'').replace(/\s*\([A-Z]+\)\s*$/,'').trim();
            var dn = raw
                .replace(/KINGSTON\s+SNV2S/gi,'K SNV2')
                .replace(/KINGSTON\s+SNVS/gi,'K SNVS')
                .replace(/KINGSTON\s+/gi,'K ')
                .replace(/Samsung\s+/gi,'S ')
                .replace(/Western\s*Digital\s+/gi,'W ')
                .replace(/WDC\s+/gi,'W ')
                .replace(/Phison\s+/gi,'P ')
                .replace(/ESR512GDLCG-E3C-4/gi,'512G NVMe')
                .replace(/WD30EZRX-00D8PB0/gi,'3T HDD');
            if (dn.length > 12) dn = dn.substring(0,10)+'..';
            var typeTag = (dk.type === 'SSD' || dk.type === 'HDD') ? ' ['+dk.type+']' : '';

            if (dk.total <= 0) {
                dh += '<div class="dk-disk-group"><div class="dk-disk-hd"><span class="dk-disk-name">'+dn+'</span><span class="dk-disk-info" style="color:var(--text2)">未分配</span></div></div>';
                continue;
            }
            var usedGB = (dk.used/1073741824).toFixed(0);
            var totalGB = (dk.total/1073741824).toFixed(0);
            var group = '<div class="dk-disk-group"><div class="dk-disk-hd"><span class="dk-disk-name" title="'+(dk.model||'')+typeTag+'">'+dn+'</span><span class="dk-disk-info">'+usedGB+'/'+totalGB+'G '+dk.percent+'%</span></div>';
            var parts = dk.partitions || [];
            if (parts.length > 0) {
                for (var p = 0; p < parts.length; p++) {
                    var pt = parts[p];
                    var fc = pt.percent>=90?'danger':pt.percent>=70?'warn':'ok';
                    group += '<div class="dk-part-row"><span class="dk-part-letter">'+pt.letter+'</span>' +
                        '<div class="dk-part-track"><div class="dk-part-fill '+fc+'" style="width:'+pt.percent+'%"></div></div></div>';
                }
            } else {
                var fc = dk.percent>=90?'danger':dk.percent>=70?'warn':'ok';
                group += '<div class="dk-part-row"><span class="dk-part-letter"></span>' +
                    '<div class="dk-part-track"><div class="dk-part-fill '+fc+'" style="width:'+dk.percent+'%"></div></div></div>';
            }
            group += '</div>';
            dh += group;
    }
    el.innerHTML = dh;
}

/* ── 网络状态（仅上下行速度）── */
function drawNetwork(net) {
    if (!net) return;
    var upEl = document.getElementById('netUp');
    var downEl = document.getElementById('netDown');
    if (upEl) upEl.innerHTML = fmtBytes(net.rate_up) + '<span class="net-unit">/s</span>';
    if (downEl) downEl.innerHTML = fmtBytes(net.rate_down) + '<span class="net-unit">/s</span>';
}

function drawGitHub(contrib, username) {
    var el = document.getElementById('ghGrid');
    if (!contrib||typeof contrib!=='object'||Object.keys(contrib).length===0) {
        el.innerHTML='<div class="ld">暂无数据</div>';
        document.getElementById('ghUser').textContent='@'+username;
        document.getElementById('ghTotal').textContent='0 次贡献';
        return;
    }
    var today=new Date(); today.setHours(0,0,0,0);
    var dow=today.getDay(), nw=26;
    var sd=new Date(today); sd.setDate(sd.getDate()-dow-(nw-1)*7);

    var gap = 2;
    var cw = el.clientWidth || 450;
    var ch = el.clientHeight || 140;
    var dayLabelW = 20, padX = 4;
    var monthLabelH = 10, padY = 2;
    var csW = Math.floor((cw - dayLabelW - padX - (nw-1)*gap) / nw);
    var csH = Math.floor((ch - monthLabelH - padY - 6*gap) / 7);
    var cs = Math.max(4, Math.min(csW, csH));

    var weeks=[],total=0,ml={};
    var mn=['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'];
    for (var w=0;w<nw;w++){
        var cells=[];
        for (var d=0;d<7;d++){
            var dt=new Date(sd); dt.setDate(dt.getDate()+w*7+d);
            if(dt>today){cells.push('<div class="gh-cell" style="width:'+cs+'px;height:'+cs+'px;opacity:0.08"></div>');continue;}
            var key=dt.getFullYear()+'-'+String(dt.getMonth()+1).padStart(2,'0')+'-'+String(dt.getDate()).padStart(2,'0');
            var val=contrib[key]||0; total+=val;
            var lv=''; if(val>0) lv=val<=2?'gh-l1':val<=5?'gh-l2':val<=10?'gh-l3':'gh-l4';
            cells.push('<div class="gh-cell '+lv+'" title="'+key+': '+val+'" style="width:'+cs+'px;height:'+cs+'px"></div>');
            if(d===1){var m=dt.getMonth();var pw=new Date(sd);pw.setDate(pw.getDate()+(w-1)*7+1);if(w===0||pw.getMonth()!==m)ml[w]=mn[m];}
        }
        weeks.push('<div class="gh-week" style="width:'+cs+'px;gap:'+gap+'px;">'+cells.join('')+'</div>');
    }
    // 月份标签
    var cellsW = nw * (cs + gap) - gap;
    var mr='<div class="gh-months" style="display:flex;width:'+(cellsW+dayLabelW+2)+'px;margin:0 auto;">';
    mr+='<span style="flex:none;width:'+(dayLabelW+2)+'px;"></span>';
    for(var w=0;w<nw;w++) mr+='<span class="gh-month" style="flex:none;width:'+(cs+gap)+'px">'+(ml[w]||'')+'</span>';
    mr+='</div>';
    // 日期标签
    var daysArr=['Sun','Mon','','Wed','','Fri',''];
    var dayLabels='<div class="gh-days" style="height:'+(7*cs+6*gap)+'px;">';
    for(var d=0;d<7;d++) dayLabels+='<span style="height:'+cs+'px;line-height:'+cs+'px;">'+daysArr[d]+'</span>';
    dayLabels+='</div>';
    el.innerHTML='<div class="gh-body"><div style="margin:auto;">'+mr+'<div style="display:flex;">'+dayLabels+'<div class="gh-cells">'+weeks.join('')+'</div></div></div></div>';
    document.getElementById('ghUser').textContent='@'+username;
    document.getElementById('ghTotal').textContent=fmtNum(total)+' 次贡献';
}

/* Dashboard 数据处理（WS 推送和 REST 调用共用） */
function handleDashboardData(d) {
    d = d || {};
    if (!d.success) { console.error('API Error:', d.error); }

    // 今日消耗由后端历史聚合路径提供；与 Vibe 卡片的 Provider 选择相互独立。
    var t = d.today || {};
    var inTok=t.in||0, outTok=t.out||0, totalTok=t.total||0, cacheTok=t.cache||0, inMiss=t.inMiss||0;
    if (inTok > 0 || outTok > 0 || totalTok > 0) {
        document.getElementById('todayTotal').textContent=fmtTok(totalTok);
        document.getElementById('todayCache').textContent=fmtTok(cacheTok);
        document.getElementById('todayCachePct').textContent=totalTok>0?(cacheTok/totalTok*100).toFixed(1)+'%':'0%';
        document.getElementById('todayIn').textContent=fmtTok(inMiss);
        document.getElementById('todayInPct').textContent=totalTok>0?(inMiss/totalTok*100).toFixed(1)+'%':'0%';
        document.getElementById('todayOut').textContent=fmtTok(outTok);
        document.getElementById('todayOutPct').textContent=totalTok>0?(outTok/totalTok*100).toFixed(1)+'%':'0%';
        drawTodayStacked(inMiss,outTok,cacheTok);
    }

    handleVibeData(d.vibe || {});

    // GitHub
    if(d.github) drawGitHub(d.github.contributions||{},d.github.user||'');
}

async function refresh() {
    try {
        var r = await secureFetch('/api/data');
        var d = await r.json();
        handleDashboardData(d);
    } catch(e){console.error('Refresh error:',e);}
}

async function refreshSys() {
    try {
        var r = await secureFetch('/api/system');
        var sys = await r.json();
        drawSystem(sys);
    } catch(e) { console.error('Sys refresh error:',e); }
}

/* ── 歌词 ── */
var _mediaLyrics = [];       // 逐行 lrc: [[sec, text], ...]
var _mediaLyricsYrc = [];    // 逐字 yrc: [{start:ms, chars:[{start,dur,text}]}, ...]
var _mediaHasYrc = false;    // 当前是否走 yrc 渲染路径
var _mediaTitle = '';
var _mediaStartTime = 0;
var _mediaPosition = 0;
var _mediaDuration = 0;
var _mediaPlaying = false;
var _lastLyricIdx = -2;
var _lastPositionSource = 'none';
var LYRIC_OFFSET = 1.5;  // 默认值，启动时从后端加载
secureFetch('/api/media/offset').then(r=>r.json()).then(d=>{
    LYRIC_OFFSET = d.offset;
    document.getElementById('lyricOffsetVal').textContent = LYRIC_OFFSET.toFixed(1);
}).catch(function(){ try { var v=parseFloat(localStorage.getItem('lyricOffset')); if(!isNaN(v)){LYRIC_OFFSET=v; document.getElementById('lyricOffsetVal').textContent=LYRIC_OFFSET.toFixed(1);} } catch(e){} });

function adjOffset(delta) {
    secureFetch('/api/media/offset', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({delta: delta})
    }).then(r=>r.json()).then(d=>{
        LYRIC_OFFSET = d.offset;
        document.getElementById('lyricOffsetVal').textContent = LYRIC_OFFSET.toFixed(1);
    });
}

var LYRIC_LINE_H = 34;       // 每行固定高度(px)，需与CSS .lyric-line的height一致
var _pendingIdx = -2;        // 待确认的候选索引（用于防抖，过滤UIA测量抖动）
var _pendingSince = 0;       // 候选索引首次出现的时刻（ms）
var LYRIC_DEBOUNCE_MS = 300; // 候选索引需要稳定多少毫秒才真正切换
                             // 阈值太小 → UIA 抖动会在句子边界反复切；
                             // 阈值太大 → 切换有肉眼可见的延迟。300ms 是折中。

function drawLyric(data) {
    var titleEl = document.getElementById('lyricTitle');
    var artistEl = document.getElementById('lyricArtist');
    var scrollEl = document.getElementById('lyricScroll');
    var idleEl = document.getElementById('lyricIdle');

    if (!data || data.status === 'idle' || data.status === 'error' || !data.title) {
        titleEl.textContent = '--';
        artistEl.textContent = '';
        scrollEl.innerHTML = '';
        idleEl.textContent = '未在播放';
        idleEl.style.display = 'block';
        _mediaPlaying = false;
        _mediaTitle = '';
        return;
    }

    titleEl.textContent = data.title;
    artistEl.textContent = data.artist;
    _mediaPlaying = (data.status === 'playing');

    var isNewSong = (data.title !== _mediaTitle);
    if (isNewSong) {
        _mediaTitle = data.title;
        _mediaLyrics = data.lyrics || [];
        _mediaLyricsYrc = data.lyrics_yrc || [];
        _mediaHasYrc = _mediaLyricsYrc.length > 0;
        _mediaDuration = data.duration || 0;
        _mediaPosition = data.position || 0;
        _mediaStartTime = Date.now() / 1000 - _mediaPosition;
        _lastLyricIdx = -2;
        _pendingIdx = -2;
        _pendingSince = 0;
        _lastPositionSource = data.position_source || 'none';
        var hasAny = _mediaHasYrc || _mediaLyrics.length > 0;
        idleEl.style.display = hasAny ? 'none' : 'block';
        idleEl.textContent = '暂无歌词';
        renderLyricLines();
        // 首次加载/切歌时强制同步到当前播放位置（暂停时也要定位）
        updateLyricLine(true);
    } else if (data.position_source === 'api') {
        // YesPlayMusic API 直接返回精确 position，无抖动，每次都校准
        var newPos = data.position || 0;
        var looped = (_lastPositionSource === 'api' && newPos < _mediaPosition - 5);
        _mediaPosition = newPos;
        if (data.duration) _mediaDuration = data.duration;
        _lastPositionSource = 'api';
        if (looped) {
            resetLyricState();
        }
        _mediaStartTime = Date.now() / 1000 - _mediaPosition;
    } else if (data.position_source === 'uia') {
        // UIA 读进度条有像素级抖动（1px ≈ 0.2 秒），漂移 > 0.3 秒就修正
        var newPos = data.position || 0;
        var looped = (_lastPositionSource === 'uia' && newPos < _mediaPosition - 5);
        var estPos = _mediaPlaying ? (Date.now() / 1000 - _mediaStartTime) : _mediaPosition;
        var drift = newPos - estPos;
        _mediaPosition = newPos;
        _lastPositionSource = 'uia';
        if (looped) {
            _mediaStartTime = Date.now() / 1000 - _mediaPosition;
            resetLyricState();
            updateLyricLine(true);
            return;
        }
        if (_lastPositionSource !== 'uia' || Math.abs(drift) > 0.3) {
            _mediaStartTime = Date.now() / 1000 - _mediaPosition;
        }
    } else if (_lastPositionSource !== 'uia') {
        // UIA 不可用（网易云窗口最小化等），退化为本地估算计时。
        // 若估算出的位置超过总时长，视为可能已循环重播，从0开始重新估算。
        var estPos = _mediaPlaying ? (Date.now() / 1000 - _mediaStartTime) : _mediaPosition;
        if (_mediaDuration > 0 && estPos >= _mediaDuration) {
            _mediaStartTime = Date.now() / 1000;
            _mediaPosition = 0;
            resetLyricState();
            updateLyricLine(true);
            return;
        }
    }

    updateLyricLine();
}

function resetLyricState() {
    _lastLyricIdx = -2;
    _pendingIdx = -2;
    _pendingSince = 0;
}

/* 一次性渲染全部歌词行到滚动容器（只在歌曲切换时调用一次）。
   有 yrc 时按字符渲染；否则按整行渲染（旧行为）。 */
function renderLyricLines() {
    var scrollEl = document.getElementById('lyricScroll');
    if (!scrollEl) return;

    if (_mediaHasYrc) {
        var html = _mediaLyricsYrc.map(function(line){
            var chars = line.chars.map(function(c){
                var text = (c.text === ' ' ? '&nbsp;' : escHtml(c.text || ' '));
                return '<span class="lyric-char">'+text+'</span>';
            }).join('');
            return '<div class="lyric-line">'+chars+'</div>';
        }).join('');
        scrollEl.innerHTML = html;
    } else {
        var html2 = _mediaLyrics.map(function(item){
            return '<div class="lyric-line">'+escHtml(item[1] || ' ')+'</div>';
        }).join('');
        scrollEl.innerHTML = html2;
    }
}

/* 将指定索引的歌词行滚动到容器中央，并更新高亮样式 */
function scrollToLyricIdx(idx) {
    var scrollEl = document.getElementById('lyricScroll');
    var textEl = document.getElementById('lyricText');
    if (!scrollEl || !textEl) return;

    var containerH = textEl.clientHeight || 0;
    var offset = containerH / 2 - LYRIC_LINE_H / 2 - idx * LYRIC_LINE_H;
    scrollEl.style.transform = 'translateY(' + offset + 'px)';

    var lines = scrollEl.children;
    for (var i = 0; i < lines.length; i++) {
        var off = i - idx;
        lines[i].className = 'lyric-line' +
            (off === 0 ? ' current' : (Math.abs(off) === 1 ? ' near' : ''));
    }
}

/* 更新逐字高亮：对当前行的每个字符按时间进度控制 CSS 变量。
   已过 = 完全高亮；正在唱 = 从 0% 渐变到某比例；未到 = 暗色。 */
function updateYrcHighlight(idx, posMs) {
    if (!_mediaHasYrc || idx < 0 || idx >= _mediaLyricsYrc.length) return;
    var scrollEl = document.getElementById('lyricScroll');
    if (!scrollEl) return;
    var lineEl = scrollEl.children[idx];
    if (!lineEl) return;
    var chars = _mediaLyricsYrc[idx].chars;
    var spans = lineEl.children;
    for (var i = 0; i < chars.length && i < spans.length; i++) {
        var c = chars[i];
        var endMs = c.start + c.dur;
        var span = spans[i];
        if (posMs >= endMs) {
            if (span.dataset.state !== 'done') {
                span.dataset.state = 'done';
                span.style.setProperty('--p', '100%');
            }
        } else if (posMs >= c.start) {
            var p = (posMs - c.start) / c.dur;
            if (p < 0) p = 0; else if (p > 1) p = 1;
            span.dataset.state = 'active';
            span.style.setProperty('--p', (p * 100).toFixed(1) + '%');
        } else {
            if (span.dataset.state !== 'idle') {
                span.dataset.state = 'idle';
                span.style.setProperty('--p', '0%');
            }
        }
    }
}

function updateLyricLine(force) {
    // 暂停时冻结歌词：不更新位置、不滚动、不刷逐字高亮
    if (!_mediaPlaying && !force) return;
    var lyricsForLine = _mediaHasYrc ? _mediaLyricsYrc : _mediaLyrics;
    if (!lyricsForLine.length) return;

    var posSec = (_mediaPlaying ? (Date.now() / 1000 - _mediaStartTime) : _mediaPosition) + LYRIC_OFFSET;
    var posMs = posSec * 1000;

    // 找当前行索引
    var idx = -1;
    if (_mediaHasYrc) {
        for (var i = 0; i < _mediaLyricsYrc.length; i++) {
            if (_mediaLyricsYrc[i].start <= posMs) idx = i;
            else break;
        }
    } else {
        for (var i2 = 0; i2 < _mediaLyrics.length; i2++) {
            if (_mediaLyrics[i2][0] <= posSec) idx = i2;
            else break;
        }
    }

    // 行切换逻辑（按"稳定 N 毫秒"防抖，跟定时器频率解耦）
    var lineChanged = (idx !== _lastLyricIdx);
    if (lineChanged) {
        var nowMs = Date.now();
        if (idx !== _pendingIdx) {
            _pendingIdx = idx;
            _pendingSince = nowMs;
        }
        var settled = (nowMs - _pendingSince) >= LYRIC_DEBOUNCE_MS;
        // 大跳（idx 差 > 1，比如切歌或 seek）立即生效，不做防抖；
        // 相邻行切换（正常推进）才走时间稳定检查
        var bigJump = Math.abs(idx - _lastLyricIdx) > 1;
        if (settled || force || bigJump) {
            _lastLyricIdx = idx;
            scrollToLyricIdx(idx);
        }
    } else if (force) {
        scrollToLyricIdx(_lastLyricIdx);
    }

    // yrc 每次都要刷字符高亮（不受防抖影响，保证平滑）
    if (_mediaHasYrc) updateYrcHighlight(_lastLyricIdx, posMs);
}

async function refreshMedia() {
    try {
        var r = await secureFetch('/api/media');
        var d = await r.json();
        drawLyric(d);
    } catch(e) { console.error('Media error:', e); }

}
// 逐字歌词需要平滑更新，60ms ≈ 16fps；lrc-only 时也没多花什么开销
setInterval(updateLyricLine, 60);
window.addEventListener('resize', function(){ updateLyricLine(true); });

tickClock(); setInterval(tickClock,1000);
refreshOffPeakBadgeConfig(); setInterval(refreshOffPeakBadgeConfig, 60000);

// 初始化歌词偏移量显示
document.getElementById('lyricOffsetVal').textContent = LYRIC_OFFSET.toFixed(1);

/* ── WebSocket 统一数据推送 ── */
var _ws = null, _wsRetry = 1000, _wsFallbackTimer = null;

function connectWS(){
    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    _ws = new WebSocket(proto + '//' + location.host + '/ws');
    _ws.onopen = function(){
        _wsRetry = 1000;
        if(_wsFallbackTimer){clearInterval(_wsFallbackTimer); _wsFallbackTimer=null;}
        // vibe 状态由后端通过 WS vibe_state 消息主动推送，无需 REST 请求
        console.log('[ws] connected');
    };
    _ws.onmessage = function(ev){
        try {
            var msg = JSON.parse(ev.data);
            if(msg.type === 'system') drawSystem(msg.data);
            else if(msg.type === 'media') drawLyric(msg.data);
            else if(msg.type === 'github'){ drawGitHub(msg.data.contributions||{}, msg.data.user||''); }
            else if(msg.type === 'dashboard_data'){ handleDashboardData(msg.data); refreshHealth(); }
            else if(msg.type === 'config_updated'){
                refreshOffPeakBadgeConfig();
                if (_ws && _ws.readyState === 1) {
                    try { _ws.send(JSON.stringify({type: 'init'})); } catch(e) {}
                }
            }
            else if(msg.type === 'vibe_state'){
                _vibeActive = !!msg.data.active;
                _vibeSyncedFromServer = true;
                try { localStorage.setItem('vibeActive', _vibeActive ? '1' : '0'); } catch(e) {}
                applyVibeUI();
                // 后端主动推送的即是权威值，无需再回推
            }
            else if(msg.type === 'theme'){ applyTheme(msg.data); }
        } catch(e){ console.error('[ws] parse error:', e); }
    };
    _ws.onclose = function(){
        console.log('[ws] disconnected, retry in ' + (_wsRetry/1000) + 's');
        setTimeout(connectWS, _wsRetry);
        _wsRetry = Math.min(_wsRetry * 2, 30000);
        // 重连期间回退 REST 轮询
        if(!_wsFallbackTimer){
            refreshSys(); refreshMedia();
            _wsFallbackTimer = setInterval(function(){ refreshSys(); refreshMedia(); }, 2000);
        }
    };
}
connectWS();

/* ── Vibe Coding 状态切换（后端 config 是唯一真值，WS + REST 双通道同步）── */
var _vibeActive = false;
var _vibeSyncedFromServer = false;

function applyVibeUI() {
    var toggle = document.getElementById('vibeToggle');
    var label = document.getElementById('vibeToggleLabel');
    if (!toggle || !label) return;
    if (_vibeActive) {
        toggle.classList.add('active');
        label.textContent = 'Coding';
    } else {
        toggle.classList.remove('active');
        label.textContent = 'Chilling';
    }
}

/** 把当前 vibe 状态推送到后端（WS 优先，失败或未就绪时走 REST 兜底）。 */
function sendVibeState() {
    var payload = {type: 'vibe', active: _vibeActive};
    if (_ws && _ws.readyState === 1) {
        try { _ws.send(JSON.stringify(payload)); return; } catch(e) { /* fall through */ }
    }
    // WS 未就绪 → REST 兜底，保证后端 config 一定被更新
    secureFetch('/api/vibe', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({active: _vibeActive})
    }).catch(function(e){ console.error('[vibe] REST sync failed:', e); });
}

function toggleVibe() {
    _vibeActive = !_vibeActive;
    try { localStorage.setItem('vibeActive', _vibeActive ? '1' : '0'); } catch(e) {}
    applyVibeUI();
    sendVibeState();
    refresh(); // 切换时立即请求一次聚合看板数据
}

/** 从后端 REST 拉取权威 vibe 状态（作为 WS 兜底，避免依赖 localStorage 缓存）。 */
function fetchVibeFromServer() {
    if (_vibeSyncedFromServer) return;
    secureFetch('/api/vibe').then(function(r){ return r.json(); }).then(function(d){
        if (_vibeSyncedFromServer) return; // WS 已经先到，就不要覆盖
        _vibeActive = !!d.active;
        _vibeSyncedFromServer = true;
        try { localStorage.setItem('vibeActive', _vibeActive ? '1' : '0'); } catch(e) {}
        applyVibeUI();
    }).catch(function(e){ console.error('[vibe] REST fetch failed:', e); });
}

(function initVibe(){
    // 先用 localStorage 快速渲染（避免闪烁），再用后端权威值覆盖
    try { _vibeActive = localStorage.getItem('vibeActive') === '1'; } catch(e) { _vibeActive = false; }
    applyVibeUI();
    // 立即拉一次，但如果 WS 更快到，会自己把 _vibeSyncedFromServer 置为 true
    fetchVibeFromServer();
})();

// 首次数据由 WS init 推送，不再单独 REST 拉取（避免和 WS 数据竞争 DOM）
// refresh();

/* ── 主题切换（点击左上角红圈，通过后端 API 循环切换）── */
function applyTheme(d){
    var name = d.theme || 'dark', bg = d.bg || {};
    document.body.dataset.theme = name;
    document.body.classList.toggle('mono', name === 'mono');
    // 背景：强绑定主题
    if(bg.bg_type === 'image' && bg.bg_image){
        document.body.style.background = "url('"+bg.bg_image+"') center/cover no-repeat fixed";
        document.body.style.backgroundColor = bg.bg_color || '#000';
    } else {
        document.body.style.background = bg.bg_color || '#0a0618';
    }
    try { localStorage.setItem('themeData', JSON.stringify(d)); } catch(e){}
    if (window._lastRingArgs) drawRing(window._lastRingArgs[0], window._lastRingArgs[1], window._lastRingArgs[2]);
    if (window._lastSystemData) drawSystem(window._lastSystemData);
}
// 启动时从后端读取当前主题
secureFetch('/api/theme').then(r=>r.json()).then(applyTheme).catch(()=>{
    // 后端不可用时回退到 localStorage 缓存
    try { var c=JSON.parse(localStorage.getItem('themeData')); if(c) applyTheme(c); } catch(e){}
});
// 点击红圈 → 后端切换下一个主题
document.querySelector('.mark').addEventListener('click', function(){
    secureFetch('/api/theme/next',{method:'POST'}).then(r=>r.json()).then(applyTheme);
});