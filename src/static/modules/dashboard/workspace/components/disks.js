import { escHtml } from '../../utils.js';

export function createDisksComponent() {
    let root = null;

    return {
        mount(context) {
            if (root) return;
            root = document.createElement('div');
            root.className = 'card diskCard';
            root.id = 'diskCard';
            root.dataset.workspaceSlot = context.slot;
            root.innerHTML = '<div class="card-head"><span class="card-head-l"><span class="bar"></span>磁盘</span></div>'
                + '<div class="card-body dk-section" id="sysDisks">'
                + '<div class="skeleton" style="height:9px;width:70%;margin-bottom:4px;"></div>'
                + '<div class="skeleton" style="height:5px;width:100%;margin-bottom:8px;"></div>'
                + '<div class="skeleton" style="height:9px;width:60%;margin-bottom:4px;"></div>'
                + '<div class="skeleton" style="height:5px;width:100%;margin-bottom:8px;"></div>'
                + '<div class="skeleton" style="height:9px;width:65%;margin-bottom:4px;"></div>'
                + '<div class="skeleton" style="height:5px;width:100%;"></div></div>';
            context.root.appendChild(root);
        },
        onData(payload) {
            const element = root?.querySelector('#sysDisks');
            if (!element) return;
            const logical = [];
            (payload?.disks || []).forEach((disk) => {
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
        },
        destroy() {
            root?.remove();
            root = null;
        },
    };
}
