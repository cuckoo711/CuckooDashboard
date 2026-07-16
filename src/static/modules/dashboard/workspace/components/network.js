import { fmtBytes } from '../../utils.js';

export function createNetworkComponent() {
    let root = null;

    return {
        mount(context) {
            if (root) return;
            root = document.createElement('div');
            root.className = 'card netCard';
            root.dataset.workspaceSlot = context.slot;
            root.innerHTML = '<div class="card-head"><span class="card-head-l"><span class="bar"></span>网络</span></div>'
                + '<div class="card-body net-rows"><div class="net-row"><span class="net-icon up">↑</span>'
                + '<span class="net-label">上传</span><span class="net-value" id="netUp">-- <span class="net-unit">KB/s</span></span></div>'
                + '<div class="net-row"><span class="net-icon down">↓</span><span class="net-label">下载</span>'
                + '<span class="net-value" id="netDown">-- <span class="net-unit">KB/s</span></span></div></div>';
            context.root.appendChild(root);
        },
        onData(payload) {
            const network = payload?.network;
            if (!root || !network) return;
            const upload = root.querySelector('#netUp');
            const download = root.querySelector('#netDown');
            if (upload) upload.innerHTML = `${fmtBytes(network.rate_up)}<span class="net-unit">/s</span>`;
            if (download) download.innerHTML = `${fmtBytes(network.rate_down)}<span class="net-unit">/s</span>`;
        },
        destroy() {
            root?.remove();
            root = null;
        },
    };
}
