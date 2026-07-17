const WIDGET_TYPE = 'com.cuckoo.runtime-health.card';

function numericCount(value) {
    if (Array.isArray(value)) return value.length;
    const number = Number(value);
    return Number.isFinite(number) && number >= 0 ? number : null;
}

function healthData(payload) {
    if (!payload || typeof payload !== 'object') return {};
    return payload.runtime_health || payload.runtimeHealth || payload;
}

function deriveCounts(data) {
    let healthy = numericCount(data.healthy_services ?? data.healthy_count ?? data.healthy);
    let abnormal = numericCount(
        data.abnormal_services ?? data.unhealthy_services ?? data.abnormal_count ?? data.unhealthy_count,
    );
    const services = Array.isArray(data.services)
        ? data.services
        : (data.services && typeof data.services === 'object' ? Object.values(data.services) : []);
    if (services.length) {
        const statuses = services.map((service) => String(service?.status || '').toLowerCase());
        if (healthy === null) healthy = statuses.filter((status) => ['healthy', 'ok', 'up', 'running'].includes(status)).length;
        if (abnormal === null) abnormal = statuses.length - (healthy ?? 0);
    }
    return { healthy: healthy ?? 0, abnormal: abnormal ?? 0 };
}

function formatUpdatedAt(value) {
    if (value === undefined || value === null || value === '') return '--';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function createRuntimeHealthComponent() {
    let root = null;

    return {
        mount(context) {
            if (root) return root;
            const doc = context.root.ownerDocument;
            root = doc.createElement('div');
            root.className = 'card runtime-health-card';
            root.dataset.workspaceSlot = context.slot;
            root.innerHTML = '<div class="card-head"><span class="card-head-l"><span class="bar"></span>运行时健康</span></div>'
                + '<div class="card-body" style="display:grid;grid-template-columns:1fr 1fr;gap:10px;align-content:center;">'
                + '<div style="grid-column:1 / -1;"><span style="opacity:.65;">总体状态</span>'
                + '<strong data-health-status style="display:block;font-size:1.35em;">等待数据</strong></div>'
                + '<div><span style="opacity:.65;">健康服务</span><strong data-health-healthy style="display:block;">0</strong></div>'
                + '<div><span style="opacity:.65;">异常服务</span><strong data-health-abnormal style="display:block;">0</strong></div></div>'
                + '<div class="card-foot"><span class="card-foot-l">更新时间</span><span class="card-foot-r" data-health-updated>--</span></div>';
            context.root.appendChild(root);
            context.subscribe('com.cuckoo.runtime-health.snapshot', (data, meta) => this.update(data, meta));
            return root;
        },
        update(payload) {
            if (!root) return;
            const data = healthData(payload);
            const { healthy, abnormal } = deriveCounts(data);
            const status = data.overall_status || data.overallStatus || data.status
                || (abnormal > 0 ? '异常' : healthy > 0 ? '健康' : '未知');
            root.querySelector('[data-health-status]').textContent = String(status);
            root.querySelector('[data-health-healthy]').textContent = String(healthy);
            root.querySelector('[data-health-abnormal]').textContent = String(abnormal);
            root.querySelector('[data-health-updated]').textContent = formatUpdatedAt(
                data.updated_at ?? data.updatedAt ?? data.timestamp,
            );
        },
        destroy() {
            root?.remove();
            root = null;
        },
    };
}

export function registerCuckooExtension(api) {
    api.registerWidget(WIDGET_TYPE, { create: createRuntimeHealthComponent, singleInstance: true });
}
