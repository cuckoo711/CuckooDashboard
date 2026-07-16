import { state } from './state.js';
import { setVibeStatusProvider } from './connection.js';
import { currencyPrefix, cssVar, escHtml, fmtNum, fmtTok, safeNumber } from './utils.js';

export function drawTodayStacked(inMiss, outTok, cacheTok) {
    const total = inMiss + outTok + cacheTok;
    if (total <= 0) return;
    const cacheSegment = document.getElementById('segCache');
    const inputSegment = document.getElementById('segIn');
    const outputSegment = document.getElementById('segOut');
    const cachePercent = document.getElementById('todayCachePct');
    const inputPercent = document.getElementById('todayInPct');
    const outputPercent = document.getElementById('todayOutPct');
    if (!cacheSegment || !inputSegment || !outputSegment || !cachePercent || !inputPercent || !outputPercent) return;
    cacheSegment.style.width = `${cacheTok / total * 100}%`;
    inputSegment.style.width = `${inMiss / total * 100}%`;
    outputSegment.style.width = `${outTok / total * 100}%`;
    cachePercent.textContent = `${(cacheTok / total * 100).toFixed(1)}%`;
    inputPercent.textContent = `${(inMiss / total * 100).toFixed(1)}%`;
    outputPercent.textContent = `${(outTok / total * 100).toFixed(1)}%`;
}

export function drawRing(percent, used, limit, itemName) {
    const pct = Math.max(0, Math.min(100, safeNumber(percent)));
    const normalizedUsed = safeNumber(used);
    const normalizedLimit = safeNumber(limit);
    state.dashboard.lastRingArgs = [pct, normalizedUsed, normalizedLimit, itemName];
    const circumference = 2 * Math.PI * 35;
    const remain = 100 - pct;
    const remainAmount = Math.max(0, normalizedLimit - normalizedUsed);
    const foreground = document.getElementById('ringFg');
    const percentElement = document.getElementById('ringPct');
    const label = document.getElementById('ringLabel');
    if (!foreground || !percentElement || !label) return;
    foreground.style.strokeDashoffset = circumference * (1 - remain / 100);
    foreground.style.stroke = pct >= 90 ? cssVar('--crimson') : pct >= 70 ? cssVar('--warn') : cssVar('--gold');
    percentElement.textContent = `${remain.toFixed(1)}%`;
    label.textContent = `${fmtTok(remainAmount)} 剩余`;
    label.title = itemName || '';
}

function resetRing() {
    const foreground = document.getElementById('ringFg');
    const percentElement = document.getElementById('ringPct');
    const label = document.getElementById('ringLabel');
    if (!foreground || !percentElement || !label) return;
    foreground.style.strokeDashoffset = 2 * Math.PI * 35;
    foreground.style.stroke = cssVar('--ring-bg');
    percentElement.textContent = '--%';
    label.textContent = '暂无数据';
    label.title = '';
}

function drawModelBars(data) {
    const element = document.getElementById('modelBars');
    if (!element) return;
    if (!data?.available || !Array.isArray(data.rows) || !data.rows.length) {
        state.dashboard.lastModelsKey = '';
        element.innerHTML = '<div class="ld">暂无数据</div>';
        return;
    }
    let rows = data.rows.map((row) => ({
        label: String(row.label || ''),
        value: safeNumber(row.value),
        requests: Math.max(0, Math.trunc(safeNumber(row.requests))),
    })).filter((row) => row.label);
    if (!rows.length) {
        state.dashboard.lastModelsKey = '';
        element.innerHTML = '<div class="ld">暂无数据</div>';
        return;
    }
    rows.sort((a, b) => b.value - a.value || a.label.localeCompare(b.label));
    rows = rows.slice(0, 3);
    const key = `${data.provider || ''}|${data.kind || ''}|${data.currency || ''}|${rows.map((row) => `${row.label}:${row.value}:${row.requests}`).join('|')}`;
    if (key === state.dashboard.lastModelsKey) return;
    state.dashboard.lastModelsKey = key;
    const maxValue = rows[0].value || 1;
    const fills = ['f1', 'f2', 'f3'];
    const isCurrency = data.kind === 'currency';
    element.innerHTML = rows.map((row, index) => {
        const width = Math.max(0, Math.min(100, row.value / maxValue * 100));
        const valueText = isCurrency ? `${currencyPrefix(data.currency)}${row.value.toFixed(2)}` : fmtTok(row.value);
        return `<div class="bar-row"><span class="bar-name" title="${escHtml(row.label)}">${escHtml(row.label)}</span>`
            + `<div class="bar-track"><div class="bar-fill ${fills[index % fills.length]}" style="width:${width}%"></div></div>`
            + `<div class="bar-info"><div class="v">${escHtml(valueText)}</div><div class="s">${fmtNum(row.requests)} 次</div></div></div>`;
    }).join('');
}

function renderVibeBalances(items) {
    const footer = document.getElementById('vibeBalances');
    if (!footer) return;
    footer.replaceChildren();
    if (!Array.isArray(items) || !items.length) {
        footer.hidden = true;
        return;
    }
    items.slice(0, 2).forEach((item) => {
        const row = document.createElement('span');
        row.className = 'vibe-balance';
        const name = document.createElement('span');
        name.className = 'vibe-balance-name';
        name.textContent = String(item.name || item.provider || '');
        const dot = document.createElement('span');
        dot.className = 'vibe-balance-dot';
        const color = typeof item.color === 'string' && /^#[0-9a-f]{6}$/i.test(item.color) ? item.color : '#888888';
        dot.style.backgroundColor = color;
        dot.style.color = color;
        const value = document.createElement('b');
        value.className = 'vibe-balance-value';
        value.textContent = `${currencyPrefix(item.currency)}${item.balance == null ? '--' : item.balance}`;
        row.append(name, dot, value);
        footer.appendChild(row);
    });
    footer.hidden = false;
}

function handleVibeData(data = {}) {
    const ring = data.ring || {};
    setVibeStatusProvider(ring.provider || null);
    if (ring.available) drawRing(ring.percent, ring.used, ring.limit, ring.item);
    else resetRing();
    drawModelBars(data.model_bars || {});
    renderVibeBalances(data.balances || []);
}

export function handleDashboardData(data = {}) {
    if (!data.success) console.error('API Error:', data.error);
    const today = data.today || {};
    const input = today.in || 0;
    const output = today.out || 0;
    const total = today.total || 0;
    const cache = today.cache || 0;
    const inputMiss = today.inMiss || 0;
    if (input > 0 || output > 0 || total > 0) {
        const elements = {
            total: document.getElementById('todayTotal'),
            cache: document.getElementById('todayCache'),
            cachePercent: document.getElementById('todayCachePct'),
            input: document.getElementById('todayIn'),
            inputPercent: document.getElementById('todayInPct'),
            output: document.getElementById('todayOut'),
            outputPercent: document.getElementById('todayOutPct'),
        };
        if (Object.values(elements).every(Boolean)) {
            elements.total.textContent = fmtTok(total);
            elements.cache.textContent = fmtTok(cache);
            elements.cachePercent.textContent = total > 0 ? `${(cache / total * 100).toFixed(1)}%` : '0%';
            elements.input.textContent = fmtTok(inputMiss);
            elements.inputPercent.textContent = total > 0 ? `${(inputMiss / total * 100).toFixed(1)}%` : '0%';
            elements.output.textContent = fmtTok(output);
            elements.outputPercent.textContent = total > 0 ? `${(output / total * 100).toFixed(1)}%` : '0%';
            drawTodayStacked(inputMiss, output, cache);
        }
    }
    handleVibeData(data.vibe || {});
}
