export function createUptimeComponent() {
    let root = null;

    return {
        mount(context) {
            if (root) return;
            root = document.createElement('div');
            root.className = 'card uptimeCard';
            root.dataset.workspaceSlot = context.slot;
            root.innerHTML = '<div class="card-head"><span class="card-head-l"><span class="bar"></span>已运行</span></div>'
                + '<div class="card-body uptime-body"><span class="uptime-val" id="uptimeVal">--</span></div>';
            context.root.appendChild(root);
        },
        onData(payload) {
            const uptime = payload?.system?.uptime;
            const element = root?.querySelector('#uptimeVal');
            if (!element || uptime == null) return;
            let seconds = Number(uptime) || 0;
            const days = Math.floor(seconds / 86400);
            seconds %= 86400;
            const hours = Math.floor(seconds / 3600);
            seconds %= 3600;
            const minutes = Math.floor(seconds / 60);
            element.textContent = `${days > 0 ? `${days}天` : ''}${hours}时${minutes}分`;
        },
        destroy() {
            root?.remove();
            root = null;
        },
    };
}
