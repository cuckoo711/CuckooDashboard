import { fmtNum } from '../../utils.js';

function githubPayload(payload, source) {
    if (source === 'dashboard.aggregate') return payload?.github || null;
    return payload;
}

export function createGitHubComponent() {
    let root = null;

    function draw(payload, source) {
        const data = githubPayload(payload, source);
        if (!root || !data) return;
        const contributions = data.contributions || {};
        const username = data.user || '';
        const element = root.querySelector('#ghGrid');
        if (!contributions || typeof contributions !== 'object' || Object.keys(contributions).length === 0) {
            element.innerHTML = '<div class="ld">暂无数据</div>';
            root.querySelector('#ghUser').textContent = `@${username}`;
            root.querySelector('#ghTotal').textContent = '0 次贡献';
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
        root.querySelector('#ghUser').textContent = `@${username}`;
        root.querySelector('#ghTotal').textContent = `${fmtNum(total)} 次贡献`;
    }

    return {
        mount(context) {
            if (root) return root;
            root = document.createElement('div');
            root.className = 'card gh-wrap';
            root.dataset.workspaceSlot = context.slot;
            root.innerHTML = '<div class="card-head"><span class="card-head-l"><span class="bar"></span>GitHub 贡献<span class="svc-dot" id="dot-github"></span></span></div>'
                + '<div class="card-body gh-grid" id="ghGrid"><div class="skeleton" style="flex:1; width:100%;"></div></div>'
                + '<div class="card-foot"><span class="card-foot-l" id="ghUser"></span><span class="card-foot-r" id="ghTotal"></span></div>';
            context.root.appendChild(root);
            context.subscribe('github.contributions', (data, meta) => this.update(data, meta));
            return root;
        },
        update(payload, meta = {}) {
            draw(payload, meta.channel);
        },
        destroy() {
            root?.remove();
            root = null;
        },
    };
}
