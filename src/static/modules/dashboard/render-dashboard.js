import { state } from './state.js';
import { setVibeStatusProvider } from './connection.js';
import { currencyPrefix, cssVar, escHtml, fmtBytes, fmtNum, fmtTok, safeNumber } from './utils.js';

export function drawTodayStacked(inMiss, outTok, cacheTok) {
    const total = inMiss + outTok + cacheTok;
    if (total <= 0) return;
    document.getElementById('segCache').style.width = `${cacheTok / total * 100}%`;
    document.getElementById('segIn').style.width = `${inMiss / total * 100}%`;
    document.getElementById('segOut').style.width = `${outTok / total * 100}%`;
    document.getElementById('todayCachePct').textContent = `${(cacheTok / total * 100).toFixed(1)}%`;
    document.getElementById('todayInPct').textContent = `${(inMiss / total * 100).toFixed(1)}%`;
    document.getElementById('todayOutPct').textContent = `${(outTok / total * 100).toFixed(1)}%`;
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
    foreground.style.strokeDashoffset = circumference * (1 - remain / 100);
    foreground.style.stroke = pct >= 90 ? cssVar('--crimson') : pct >= 70 ? cssVar('--warn') : cssVar('--gold');
    document.getElementById('ringPct').textContent = `${remain.toFixed(1)}%`;
    const label = document.getElementById('ringLabel');
    label.textContent = `${fmtTok(remainAmount)} 剩余`;
    label.title = itemName || '';
}

function resetRing() {
    const foreground = document.getElementById('ringFg');
    foreground.style.strokeDashoffset = 2 * Math.PI * 35;
    foreground.style.stroke = cssVar('--ring-bg');
    document.getElementById('ringPct').textContent = '--%';
    const label = document.getElementById('ringLabel');
    label.textContent = '暂无数据';
    label.title = '';
}

function drawModelBars(data) {
    const element = document.getElementById('modelBars');
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

function drawDisks(disksRaw) {
    const element = document.getElementById('sysDisks');
    if (!element) return;
    const logical = [];
    (disksRaw || []).forEach((disk) => {
        (disk.partitions || []).forEach((partition) => {
            const letter = String(partition.letter || '').replace(':', '').toUpperCase();
            if (!letter) return;
            logical.push({
                letter,
                total: Number(partition.total || 0),
                used: Number(partition.used || 0),
                percent: Number(partition.percent || 0),
            });
        });
    });
    logical.sort((a, b) => a.letter.localeCompare(b.letter, 'en', { numeric: true, sensitivity: 'base' }));
    if (!logical.length) {
        element.innerHTML = '<div class="dk-empty">暂无逻辑盘数据</div>';
        return;
    }
    element.innerHTML = logical.map((disk) => {
        const pct = Math.max(0, Math.min(100, disk.percent || 0));
        const fillClass = pct >= 90 ? 'danger' : pct >= 70 ? 'warn' : 'ok';
        const usedGb = (disk.used / 1073741824).toFixed(0);
        const totalGb = (disk.total / 1073741824).toFixed(0);
        return `<div class="dk-drive-row" title="${escHtml(`${disk.letter}: ${usedGb}/${totalGb}G ${pct.toFixed(1)}%`)}">`
            + `<span class="dk-drive-letter">${escHtml(disk.letter)}:</span>`
            + `<div class="dk-part-track"><div class="dk-part-fill ${fillClass}" style="width:${pct}%"></div></div></div>`;
    }).join('');
}

function drawNetwork(network) {
    if (!network) return;
    const upload = document.getElementById('netUp');
    const download = document.getElementById('netDown');
    if (upload) upload.innerHTML = `${fmtBytes(network.rate_up)}<span class="net-unit">/s</span>`;
    if (download) download.innerHTML = `${fmtBytes(network.rate_down)}<span class="net-unit">/s</span>`;
}

export function drawSystem(system) {
    if (!system) return;
    state.dashboard.lastSystemData = system;
    const ringColor = (pct) => pct >= 90 ? cssVar('--crimson') : pct >= 80 ? cssVar('--warn') : cssVar('--green');
    const mainRing = (pct, name, sub, color) => {
        const svgSize = 84;
        const center = svgSize / 2;
        const radius = 34;
        const circumference = 2 * Math.PI * radius;
        const arcLength = circumference * 0.75;
        const gapLength = circumference - arcLength;
        const filled = (pct / 100) * arcLength;
        const ring = color || ringColor(pct);
        return `<div class="hw-main-item"><div class="hw-main-ring" style="width:${svgSize}px;height:${svgSize}px;">`
            + `<svg width="${svgSize}" height="${svgSize}" viewBox="0 0 ${svgSize} ${svgSize}">`
            + `<circle class="hw-ring-bg" cx="${center}" cy="${center}" r="${radius}" stroke-dasharray="${arcLength} ${gapLength}" transform="rotate(135 ${center} ${center})"/>`
            + `<circle class="hw-ring-fg" cx="${center}" cy="${center}" r="${radius}" stroke="${ring}" stroke-dasharray="${filled} ${circumference - filled}" transform="rotate(135 ${center} ${center})"/></svg>`
            + `<div class="hw-main-center"><span class="hw-main-pct">${pct.toFixed(0)}%</span><span class="hw-main-sub">${sub}</span></div></div>`
            + `<span class="hw-main-name" title="${escHtml(name)}">${escHtml(name)}</span></div>`;
    };
    const cpu = system.cpu;
    const memory = system.memory;
    const cpuName = (cpu.model || '').replace(/AMD\s+Ryzen\s+/i, 'R').replace(/\(R\)/g, '').replace(/\(TM\)/g, '')
        .replace(/CPU\s*/i, '').replace(/@\s*[\d.]+GHz/i, '').replace(/\d+\s*-?\s*Core\s*Processor/gi, '').trim();
    const frequency = cpu.freq_current >= 1000 ? (cpu.freq_current / 1000).toFixed(2) : cpu.freq_current;
    const memoryLabel = memory.name || `${memory.type || '内存'}${memory.freq ? ` ${memory.freq}` : ''}`;
    const memoryTotalGb = memory.installed ? memory.installed / 1073741824 : memory.total / 1073741824;
    let html = '<div class="hw-main">';
    html += mainRing(cpu.percent, cpuName, `${frequency}GHz`);
    html += mainRing(memory.percent, memoryLabel, `${(memory.used / 1073741824).toFixed(1)}/${memoryTotalGb.toFixed(1)}G`);
    const discreteGpus = (system.gpus || []).filter((gpu) => gpu.is_discrete);
    if (discreteGpus.length) {
        const gpu = discreteGpus[0];
        const name = gpu.name.replace(/AMD\s+/, '').replace(/Radeon\s+/g, '').replace(/\(TM\)/g, '').replace(/NVIDIA\s+/gi, '');
        const totalVram = gpu.vram > 0 ? (gpu.vram / 1073741824).toFixed(0) : '';
        const usedVram = gpu.vram_used != null && gpu.vram_used > 0 ? (gpu.vram_used / 1073741824).toFixed(1) : '';
        const summary = usedVram && totalVram ? `${usedVram}/${totalVram}G` : totalVram ? `${totalVram}G` : '';
        html += mainRing(gpu.util != null ? gpu.util : 0, name, summary);
    }
    html += '</div>';
    document.getElementById('sysMain').innerHTML = html;
    drawDisks(system.disks || []);
    drawNetwork(system.network);
    const uptime = document.getElementById('uptimeVal');
    if (uptime && system.system?.uptime != null) {
        let seconds = system.system.uptime;
        const days = Math.floor(seconds / 86400);
        seconds %= 86400;
        const hours = Math.floor(seconds / 3600);
        seconds %= 3600;
        const minutes = Math.floor(seconds / 60);
        uptime.textContent = `${days > 0 ? `${days}天` : ''}${hours}时${minutes}分`;
    }
}

export function drawGitHub(contributions, username) {
    const element = document.getElementById('ghGrid');
    if (!contributions || typeof contributions !== 'object' || Object.keys(contributions).length === 0) {
        element.innerHTML = '<div class="ld">暂无数据</div>';
        document.getElementById('ghUser').textContent = `@${username}`;
        document.getElementById('ghTotal').textContent = '0 次贡献';
        return;
    }
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const weeksCount = 26;
    const startDate = new Date(today);
    startDate.setDate(startDate.getDate() - today.getDay() - (weeksCount - 1) * 7);
    const gap = 2;
    const width = element.clientWidth || 450;
    const height = element.clientHeight || 140;
    const dayLabelWidth = 20;
    const cellWidth = Math.floor((width - dayLabelWidth - 4 - (weeksCount - 1) * gap) / weeksCount);
    const cellHeight = Math.floor((height - 10 - 2 - 6 * gap) / 7);
    const cellSize = Math.max(4, Math.min(cellWidth, cellHeight));
    const weeks = [];
    const monthLabels = {};
    const monthNames = ['1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月'];
    let total = 0;
    for (let week = 0; week < weeksCount; week += 1) {
        const cells = [];
        for (let day = 0; day < 7; day += 1) {
            const date = new Date(startDate);
            date.setDate(date.getDate() + week * 7 + day);
            if (date > today) {
                cells.push(`<div class="gh-cell" style="width:${cellSize}px;height:${cellSize}px;opacity:0.08"></div>`);
                continue;
            }
            const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
            const value = contributions[key] || 0;
            total += value;
            const level = value > 0 ? value <= 2 ? 'gh-l1' : value <= 5 ? 'gh-l2' : value <= 10 ? 'gh-l3' : 'gh-l4' : '';
            cells.push(`<div class="gh-cell ${level}" title="${key}: ${value}" style="width:${cellSize}px;height:${cellSize}px"></div>`);
            if (day === 1) {
                const month = date.getMonth();
                const previousWeek = new Date(startDate);
                previousWeek.setDate(previousWeek.getDate() + (week - 1) * 7 + 1);
                if (week === 0 || previousWeek.getMonth() !== month) monthLabels[week] = monthNames[month];
            }
        }
        weeks.push(`<div class="gh-week" style="width:${cellSize}px;gap:${gap}px;">${cells.join('')}</div>`);
    }
    const cellsWidth = weeksCount * (cellSize + gap) - gap;
    let months = `<div class="gh-months" style="display:flex;width:${cellsWidth + dayLabelWidth + 2}px;margin:0 auto;">`;
    months += `<span style="flex:none;width:${dayLabelWidth + 2}px;"></span>`;
    for (let week = 0; week < weeksCount; week += 1) {
        months += `<span class="gh-month" style="flex:none;width:${cellSize + gap}px">${monthLabels[week] || ''}</span>`;
    }
    months += '</div>';
    const dayNames = ['Sun', 'Mon', '', 'Wed', '', 'Fri', ''];
    const days = `<div class="gh-days" style="height:${7 * cellSize + 6 * gap}px;">${dayNames.map((name) => `<span style="height:${cellSize}px;line-height:${cellSize}px;">${name}</span>`).join('')}</div>`;
    element.innerHTML = `<div class="gh-body"><div style="margin:auto;">${months}<div style="display:flex;">${days}<div class="gh-cells">${weeks.join('')}</div></div></div></div>`;
    document.getElementById('ghUser').textContent = `@${username}`;
    document.getElementById('ghTotal').textContent = `${fmtNum(total)} 次贡献`;
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
        document.getElementById('todayTotal').textContent = fmtTok(total);
        document.getElementById('todayCache').textContent = fmtTok(cache);
        document.getElementById('todayCachePct').textContent = total > 0 ? `${(cache / total * 100).toFixed(1)}%` : '0%';
        document.getElementById('todayIn').textContent = fmtTok(inputMiss);
        document.getElementById('todayInPct').textContent = total > 0 ? `${(inputMiss / total * 100).toFixed(1)}%` : '0%';
        document.getElementById('todayOut').textContent = fmtTok(output);
        document.getElementById('todayOutPct').textContent = total > 0 ? `${(output / total * 100).toFixed(1)}%` : '0%';
        drawTodayStacked(inputMiss, output, cache);
    }
    handleVibeData(data.vibe || {});
    if (data.github) drawGitHub(data.github.contributions || {}, data.github.user || '');
}
