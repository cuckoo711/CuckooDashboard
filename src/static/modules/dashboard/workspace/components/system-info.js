import { escHtml } from '../../utils.js';

function ringColor(percent) {
    return percent >= 90 ? 'var(--crimson)' : percent >= 80 ? 'var(--warn)' : 'var(--green)';
}

function mainRing(percentRaw, name, sub) {
    const percent = Math.max(0, Math.min(100, Number(percentRaw) || 0));
    const svgSize = 84;
    const center = svgSize / 2;
    const radius = 34;
    const circumference = 2 * Math.PI * radius;
    const arcLength = circumference * 0.75;
    const gapLength = circumference - arcLength;
    const filled = (percent / 100) * arcLength;
    return `<div class="hw-main-item"><div class="hw-main-ring" style="width:${svgSize}px;height:${svgSize}px;">`
        + `<svg width="${svgSize}" height="${svgSize}" viewBox="0 0 ${svgSize} ${svgSize}">`
        + `<circle class="hw-ring-bg" cx="${center}" cy="${center}" r="${radius}" stroke-dasharray="${arcLength} ${gapLength}" transform="rotate(135 ${center} ${center})"/>`
        + `<circle class="hw-ring-fg" cx="${center}" cy="${center}" r="${radius}" stroke="${ringColor(percent)}" stroke-dasharray="${filled} ${circumference - filled}" transform="rotate(135 ${center} ${center})"/></svg>`
        + `<div class="hw-main-center"><span class="hw-main-pct">${percent.toFixed(0)}%</span><span class="hw-main-sub">${escHtml(sub)}</span></div></div>`
        + `<span class="hw-main-name" title="${escHtml(name)}">${escHtml(name)}</span></div>`;
}

export function createSystemInfoComponent() {
    let root = null;

    return {
        mount(context) {
            if (root) return root;
            root = document.createElement('div');
            root.className = 'card sysCard';
            root.id = 'sysCard';
            root.dataset.workspaceSlot = context.slot;
            root.innerHTML = '<div class="card-head"><span class="card-head-l"><span class="bar"></span>系统信息<span class="svc-dot" id="dot-system"></span></span></div>'
                + '<div class="card-body" id="sysMain"><div class="hw-main">'
                + '<div class="hw-main-item"><div class="skeleton" style="width:76px;height:76px;border-radius:50%;"></div><div class="skeleton" style="width:44px;height:9px;margin-top:6px;"></div></div>'
                + '<div class="hw-main-item"><div class="skeleton" style="width:76px;height:76px;border-radius:50%;"></div><div class="skeleton" style="width:44px;height:9px;margin-top:6px;"></div></div>'
                + '<div class="hw-main-item"><div class="skeleton" style="width:76px;height:76px;border-radius:50%;"></div><div class="skeleton" style="width:44px;height:9px;margin-top:6px;"></div></div>'
                + '</div></div>';
            context.root.appendChild(root);
            return root;
        },
        onData(system) {
            if (!root || !system) return;
            const cpu = system.cpu || {};
            const memory = system.memory || {};
            const cpuName = (cpu.model || '').replace(/AMD\s+Ryzen\s+/i, 'R').replace(/\(R\)/g, '').replace(/\(TM\)/g, '')
                .replace(/CPU\s*/i, '').replace(/@\s*[\d.]+GHz/i, '').replace(/\d+\s*-?\s*Core\s*Processor/gi, '').trim();
            const frequencyValue = Number(cpu.freq_current) || 0;
            const frequency = frequencyValue >= 1000 ? (frequencyValue / 1000).toFixed(2) : frequencyValue;
            const memoryLabel = memory.name || `${memory.type || '内存'}${memory.freq ? ` ${memory.freq}` : ''}`;
            const memoryTotal = Number(memory.installed || memory.total) || 0;
            const memoryTotalGb = memoryTotal / 1073741824;
            let html = '<div class="hw-main">';
            html += mainRing(cpu.percent, cpuName, `${frequency}GHz`);
            html += mainRing(memory.percent, memoryLabel, `${((Number(memory.used) || 0) / 1073741824).toFixed(1)}/${memoryTotalGb.toFixed(1)}G`);
            const gpu = (system.gpus || []).find((item) => item.is_discrete);
            if (gpu) {
                const name = String(gpu.name || '').replace(/AMD\s+/, '').replace(/Radeon\s+/g, '').replace(/\(TM\)/g, '').replace(/NVIDIA\s+/gi, '');
                const totalVram = gpu.vram > 0 ? (gpu.vram / 1073741824).toFixed(0) : '';
                const usedVram = gpu.vram_used != null && gpu.vram_used > 0 ? (gpu.vram_used / 1073741824).toFixed(1) : '';
                const summary = usedVram && totalVram ? `${usedVram}/${totalVram}G` : totalVram ? `${totalVram}G` : '';
                html += mainRing(gpu.util, name, summary);
            }
            html += '</div>';
            const main = root.querySelector('#sysMain');
            if (main) main.innerHTML = html;
        },
        destroy() {
            root?.remove();
            root = null;
        },
    };
}
