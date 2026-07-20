import { handleDashboardData } from '../../render-dashboard.js';
import { state } from '../../state.js';

export const VIBE_SINGLE_INSTANCE = true;

export function createVibeComponent() {
    let root = null;

    return {
        mount(context) {
            if (root) return root;
            root = document.createElement('div');
            root.className = 'card tokenCard';
            root.id = 'vibeCard';
            root.dataset.workspaceSlot = context.slot;
            root.innerHTML = '<div class="card-head">'
                + '<span class="card-head-l"><span class="bar"></span>Vibe Coding<span class="svc-dot" id="dot-vibe"></span></span>'
                + '<span class="card-head-r"><span class="vibe-toggle" id="vibeToggle" data-action="toggle-vibe">'
                + '<span class="dot"></span><span id="vibeToggleLabel">Chilling</span></span></span></div>'
                + '<div class="card-body" style="position:relative;"><div class="combined"><div class="combined-left">'
                + '<div style="position:relative;"><svg class="ring-svg" width="90" height="90" viewBox="0 0 90 90">'
                + '<circle class="ring-bg" cx="45" cy="45" r="35"></circle>'
                + '<circle class="ring-fg" id="ringFg" cx="45" cy="45" r="35" stroke-dasharray="219.9" stroke-dashoffset="219.9"></circle>'
                + '</svg><div class="ring-center"><div class="ring-pct" id="ringPct">--%</div>'
                + '<div class="ring-label" id="ringLabel">-- / --</div></div></div></div>'
                + '<div class="combined-right" id="modelBars">'
                + '<div class="bar-row"><div class="skeleton" style="width:60px;height:9px;"></div><div class="skeleton" style="flex:1;height:12px;margin-left:4px;"></div></div>'
                + '<div class="bar-row"><div class="skeleton" style="width:60px;height:9px;"></div><div class="skeleton" style="flex:1;height:12px;margin-left:4px;"></div></div>'
                + '</div></div><div class="today-sep"></div><div class="today-top">'
                + '<span class="today-big" id="todayTotal">--</span><span class="today-big-sub">总 Token</span></div>'
                + '<div class="stacked-wrap"><div class="stacked-bar">'
                + '<div class="stacked-seg s-cache" id="segCache" style="width:34%"></div>'
                + '<div class="stacked-seg s-in" id="segIn" style="width:33%"></div>'
                + '<div class="stacked-seg s-out" id="segOut" style="width:33%"></div></div></div>'
                + '<div class="legend">'
                + '<div class="leg-row"><span class="leg-dot" style="background:var(--stack-cache)"></span>缓存命中<b id="todayCache">--</b><span class="leg-pct" id="todayCachePct"></span></div>'
                + '<div class="leg-row"><span class="leg-dot" style="background:var(--accent)"></span>未命中<b id="todayIn">--</b><span class="leg-pct" id="todayInPct"></span></div>'
                + '<div class="leg-row"><span class="leg-dot" style="background:var(--crimson)"></span>输出<b id="todayOut">--</b><span class="leg-pct" id="todayOutPct"></span></div>'
                + '</div></div><div class="card-foot vibe-balances" id="vibeBalances" hidden></div>';
            context.root.appendChild(root);
            // #modelBars 是带骨架屏的新 DOM；清掉模块级渲染 key，否则重放的
            // 相同 aggregate 会命中缓存早退，骨架屏一直留到数据真正变化。
            state.dashboard.lastModelsKey = '';
            context.subscribe('dashboard.aggregate', (data, meta) => this.update(data, meta));
            return root;
        },
        update(payload) {
            if (root) handleDashboardData(payload || {});
        },
        destroy() {
            root?.remove();
            root = null;
        },
    };
}
