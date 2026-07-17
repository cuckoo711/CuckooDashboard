import {requestJson} from './api.js';
import {$ , escHtml} from './dom.js';
import {showMessage} from './state.js';

let latestDevices = [];
let deviceWorkspaces = [{id: 'main', name: '主工作区'}];

const STATUS_LABELS = {
    pending: '待审批',
    approved: '已批准',
    disabled: '已禁用',
};

export function setDeviceWorkspaces(workspaces) {
    const normalized = (Array.isArray(workspaces) ? workspaces : [])
        .map((workspace) => ({
            id: String(workspace?.id || ''),
            name: String(workspace?.name || workspace?.id || ''),
        }))
        .filter((workspace) => workspace.id);
    if (!normalized.some((workspace) => workspace.id === 'main')) {
        normalized.unshift({id: 'main', name: '主工作区'});
    }
    deviceWorkspaces = normalized;
    if (latestDevices.length) renderDevices(latestDevices);
}

function workspaceOptions(selected) {
    return deviceWorkspaces.map((workspace) => {
        const selectedAttr = workspace.id === selected ? 'selected' : '';
        return `<option value="${escHtml(workspace.id)}" ${selectedAttr}>${escHtml(workspace.name)} (${escHtml(workspace.id)})</option>`;
    }).join('');
}

function renderDevices(devices) {
    latestDevices = Array.isArray(devices) ? devices : [];
    const container = $('#devicesList');
    if (!container) return;
    if (!latestDevices.length) {
        container.innerHTML = '<div class="empty-row">暂无显示终端</div>';
        return;
    }
    container.innerHTML = latestDevices.map((device) => {
        const status = device.status || 'pending';
        const online = device.online ? '在线' : '离线';
        const note = device.note || '';
        const scaleMode = device.scale_mode || 'auto';
        const scale = Number(device.scale || 1);
        return `
            <div class="device-row" data-device-id="${escHtml(device.id)}">
                <div class="device-main">
                    <div class="device-id" title="${escHtml(device.id)}">${escHtml(device.id.slice(0, 8))}…${escHtml(device.id.slice(-4))}</div>
                    <span class="device-status status-${escHtml(status)}">${escHtml(STATUS_LABELS[status] || status)}</span>
                    <span class="device-online ${device.online ? 'is-online' : ''}">${online}</span>
                    <span class="device-page">${escHtml(device.display_name || device.last_page || '—')}</span>
                </div>
                <div class="device-controls">
                    <label>工作区
                        <select class="device-workspace">${workspaceOptions(device.workspace_id || 'main')}</select>
                    </label>
                    <label>缩放
                        <select class="device-scale-mode">
                            <option value="auto" ${scaleMode === 'auto' ? 'selected' : ''}>自动</option>
                            <option value="fixed" ${scaleMode === 'fixed' ? 'selected' : ''}>固定</option>
                        </select>
                    </label>
                    <label>比例
                        <input class="device-scale" type="number" min="0.25" max="4" step="0.05" value="${escHtml(String(scale))}">
                    </label>
                    <label class="device-note-field">备注
                        <input class="device-note" type="text" maxlength="500" value="${escHtml(note)}" placeholder="例如：客厅副屏">
                    </label>
                    <div class="device-actions">
                        ${status === 'pending' ? '<button type="button" class="small-btn device-approve-btn">批准</button>' : ''}
                        ${status === 'approved' ? '<button type="button" class="small-btn danger-btn device-disable-btn">禁用</button>' : ''}
                        ${status === 'disabled' ? '<button type="button" class="small-btn device-approve-btn">重新启用</button>' : ''}
                        <button type="button" class="small-btn device-save-btn">保存</button>
                        <button type="button" class="small-btn danger-btn device-delete-btn">删除</button>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function setDeviceMessage(text, kind = '') {
    const message = $('#deviceMessage');
    if (!message) return;
    message.className = `workspace-message ${kind}`.trim();
    message.textContent = text;
}

export async function refreshDevicesList() {
    const button = $('#refreshDevicesBtn');
    if (button) button.disabled = true;
    try {
        const data = await requestJson('/api/settings/devices');
        // Backend already excludes settings sessions from online presence; keep a
        // defensive client-side filter in case older servers are still running.
        const devices = (data.devices || []).map((device) => {
            const sessions = Array.isArray(device.sessions)
                ? device.sessions.filter((session) => String(session?.page || '').toLowerCase() !== 'settings')
                : [];
            return {
                ...device,
                sessions,
                session_count: sessions.length,
                online: sessions.length > 0,
            };
        });
        renderDevices(devices);
        setDeviceMessage(`已加载 ${devices.length} 个终端`);
    } catch (error) {
        const container = $('#devicesList');
        if (container) {
            container.innerHTML = `<div class="empty-row state-error">获取失败：${escHtml(error.message)}</div>`;
        }
        setDeviceMessage(`获取失败：${error.message}`, 'error');
    } finally {
        if (button) button.disabled = false;
    }
}

async function patchDevice(deviceId, body) {
    return requestJson(`/api/settings/devices/${encodeURIComponent(deviceId)}`, {
        method: 'PATCH',
        body,
    });
}

function collectDeviceDraft(row) {
    return {
        workspace_id: $('.device-workspace', row)?.value || 'main',
        scale_mode: $('.device-scale-mode', row)?.value || 'auto',
        scale: Number($('.device-scale', row)?.value || 1),
        note: $('.device-note', row)?.value || '',
    };
}

async function handleDeviceAction(event) {
    const button = event.target.closest?.('button');
    if (!button) return;
    const row = button.closest('.device-row');
    if (!row) return;
    const deviceId = row.dataset.deviceId;
    if (!deviceId) return;
    button.disabled = true;
    try {
        if (button.classList.contains('device-approve-btn')) {
            await patchDevice(deviceId, {
                status: 'approved',
                ...collectDeviceDraft(row),
            });
            showMessage('终端已批准', 'success');
        } else if (button.classList.contains('device-disable-btn')) {
            await patchDevice(deviceId, {status: 'disabled'});
            showMessage('终端已禁用', 'success');
        } else if (button.classList.contains('device-save-btn')) {
            await patchDevice(deviceId, collectDeviceDraft(row));
            showMessage('终端配置已保存', 'success');
        } else if (button.classList.contains('device-delete-btn')) {
            const shortId = `${deviceId.slice(0, 8)}…${deviceId.slice(-4)}`;
            if (!window.confirm(`确定删除终端 ${shortId} 吗？删除后该浏览器需要重新申请审批。`)) {
                return;
            }
            await requestJson(`/api/settings/devices/${encodeURIComponent(deviceId)}`, {
                method: 'DELETE',
            });
            showMessage('终端已删除', 'success');
        } else {
            return;
        }
        await refreshDevicesList();
    } catch (error) {
        showMessage(`终端操作失败：${error.message}`, 'error');
        setDeviceMessage(error.message, 'error');
    } finally {
        button.disabled = false;
    }
}


export function bindDeviceEvents() {
    const refreshButton = $('#refreshDevicesBtn');
    if (refreshButton) refreshButton.addEventListener('click', refreshDevicesList);
    const list = $('#devicesList');
    if (list) list.addEventListener('click', handleDeviceAction);
    refreshDevicesList();
}
