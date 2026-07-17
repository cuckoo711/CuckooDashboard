import { deviceHeaders, deviceIdentity, getDeviceId } from '../shared/device-id.js';

export { deviceHeaders, deviceIdentity, getDeviceId };

export async function handshakeDevice(secureFetch, identity = {}) {
    const response = await secureFetch('/api/device/session', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            ...deviceHeaders(),
        },
        body: JSON.stringify(deviceIdentity(identity)),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        const error = new Error(payload?.error?.message || `HTTP ${response.status}`);
        error.status = response.status;
        error.payload = payload;
        throw error;
    }
    return payload;
}

export function showDeviceGate(root, session) {
    if (!root) return;
    const reason = session?.reason || 'device_pending';
    const title = reason === 'device_disabled' ? '终端已禁用' : '等待管理员审批';
    const detail = reason === 'device_disabled'
        ? '当前浏览器终端已被禁用。请在本机 Settings 中重新启用。'
        : '本浏览器已生成终端标识，请在本机 Settings → 显示终端 中批准后刷新。';
    const deviceId = session?.device?.id || getDeviceId();
    root.innerHTML = '';
    const card = document.createElement('section');
    card.className = 'device-gate card';
    card.innerHTML = `
        <div class="card-title">${title}</div>
        <p class="device-gate-detail">${detail}</p>
        <code class="device-gate-id">${deviceId}</code>
        <button type="button" class="small-btn" data-device-retry>重新检查</button>
    `;
    root.appendChild(card);
    card.querySelector('[data-device-retry]')?.addEventListener('click', () => window.location.reload());
}
