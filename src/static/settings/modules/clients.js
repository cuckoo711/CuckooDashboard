import {$, escHtml} from './dom.js';
import {requestJson} from './api.js';
import {showMessage} from './state.js';

const PAGE_LABELS = {dashboard: '看板', music: '音乐舞台', settings: '设置页', unknown: '未知'};
let settingsWebSocket = null;
let settingsWebSocketRetry = 1000;
let latestClients = [];
let clientWorkspaces = [{id: 'main', name: '主工作区'}];

function targetOptions(client) {
    const current = client.page === 'music' ? 'page:music' : `workspace:${client.workspace_id || 'main'}`;
    const workspaceOptions = clientWorkspaces.map((workspace) => {
        const value = `workspace:${workspace.id}`;
        return `<option value="${escHtml(value)}" ${value === current ? 'selected' : ''}>${escHtml(workspace.name)} (${escHtml(workspace.id)})</option>`;
    }).join('');
    return workspaceOptions + `<option value="page:music" ${current === 'page:music' ? 'selected' : ''}>音乐舞台</option>`;
}

function renderClientsList(clients) {
    latestClients = Array.isArray(clients) ? clients : [];
    const container = $('#clientsList');
    if (!container) return;
    if (!latestClients.length) {
        container.innerHTML = '<div class="empty-row">暂无在线客户端</div>';
        return;
    }
    container.innerHTML = latestClients.map((client) => {
        const page = client.page || 'unknown';
        const workspace = clientWorkspaces.find((item) => item.id === client.workspace_id);
        const label = page === 'dashboard' && workspace ? workspace.name : (PAGE_LABELS[page] || page);
        return '<div class="client-row" data-client-id="' + escHtml(client.id) + '">' +
            '<span class="client-id">' + escHtml(client.id) + '</span>' +
            '<span class="client-page">当前：' + escHtml(label) + '</span>' +
            '<select class="client-target-select" aria-label="选择客户端目标">' + targetOptions(client) + '</select>' +
            '<button type="button" class="small-btn client-screenshot-btn" data-client-id="' + escHtml(client.id) + '">截图</button>' +
            '<button type="button" class="small-btn client-nav-btn">发送</button>' +
            '</div>';
    }).join('');
}

export function getLatestClients() {
    return latestClients.map((client) => ({...client}));
}

export function setClientWorkspaces(workspaces) {
    const normalized = (Array.isArray(workspaces) ? workspaces : [])
        .map((workspace) => ({id: String(workspace?.id || ''), name: String(workspace?.name || workspace?.id || '')}))
        .filter((workspace) => workspace.id);
    if (!normalized.some((workspace) => workspace.id === 'main')) {
        normalized.unshift({id: 'main', name: '主工作区'});
    }
    clientWorkspaces = normalized;
    if (latestClients.length) renderClientsList(latestClients);
}

export async function refreshClientsList() {
    const button = $('#refreshClientsBtn');
    if (button) button.disabled = true;
    try {
        const data = await requestJson('/api/settings/clients');
        renderClientsList(data.clients || []);
        if (typeof window !== 'undefined' && typeof CustomEvent === 'function') {
            window.dispatchEvent(new CustomEvent('workspace-clients-updated'));
        }
    } catch (error) {
        const container = $('#clientsList');
        if (container) container.innerHTML = `<div class="empty-row state-error">获取失败：${escHtml(error.message)}</div>`;
    } finally {
        if (button) button.disabled = false;
    }
}

function downloadScreenshot(dataUrl, clientId, timestamp) {
    const link = document.createElement('a');
    link.href = dataUrl;
    const date = new Date(timestamp * 1000);
    const dateString = date.getFullYear() +
        String(date.getMonth() + 1).padStart(2, '0') +
        String(date.getDate()).padStart(2, '0') + '_' +
        String(date.getHours()).padStart(2, '0') +
        String(date.getMinutes()).padStart(2, '0') +
        String(date.getSeconds()).padStart(2, '0');
    link.download = `cuckoo_screenshot_${clientId}_${dateString}.png`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    showMessage('截图已下载', 'success');
    const button = document.querySelector(`.client-screenshot-btn[data-client-id="${clientId}"]`);
    if (button) {
        button.textContent = '截图';
        button.disabled = false;
    }
}

export function connectSettingsWebSocket() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    settingsWebSocket = new WebSocket(`${protocol}//${location.host}/ws`);
    settingsWebSocket.onopen = () => {
        settingsWebSocketRetry = 1000;
        try {
            settingsWebSocket.send(JSON.stringify({type: 'report', page: 'settings'}));
        } catch (_error) {
            // The close handler will reconnect if the socket disappeared.
        }
        console.log('[settings-ws] connected');
    };
    settingsWebSocket.onmessage = (event) => {
        try {
            const message = JSON.parse(event.data);
            if (message.type === 'screenshot_result') {
                downloadScreenshot(message.data, message.client_id, message.timestamp);
            }
        } catch (_error) {
            // Ignore messages that are unrelated to Settings or malformed.
        }
    };
    settingsWebSocket.onclose = () => {
        console.log(`[settings-ws] disconnected, retry in ${settingsWebSocketRetry / 1000}s`);
        setTimeout(connectSettingsWebSocket, settingsWebSocketRetry);
        settingsWebSocketRetry = Math.min(settingsWebSocketRetry * 2, 30000);
    };
}

async function reloadClients(button) {
    button.disabled = true;
    button.textContent = '正在刷新…';
    try {
        await requestJson('/api/settings/reload-clients', {method: 'POST'});
        button.textContent = '已发送';
        setTimeout(() => {
            button.textContent = '刷新看板';
            button.disabled = false;
        }, 1500);
    } catch (error) {
        button.textContent = '失败';
        showMessage(`刷新看板失败：${error.message}`, 'error');
        setTimeout(() => {
            button.textContent = '刷新看板';
            button.disabled = false;
        }, 1500);
    }
}

async function navigateClient(button) {
    const row = button.closest('.client-row');
    if (!row) return;
    const clientId = row.dataset.clientId;
    const select = $('.client-target-select', row);
    const target = select?.value || '';
    if (!clientId || !target) return;
    const body = target === 'page:music'
        ? {page: 'music'}
        : {workspace_id: target.replace(/^workspace:/, '')};
    button.disabled = true;
    if (select) select.disabled = true;
    const original = button.textContent;
    button.textContent = '发送中…';
    try {
        await requestJson(`/api/settings/clients/${encodeURIComponent(clientId)}/navigate`, {
            method: 'POST',
            body,
        });
        button.textContent = '已发送';
        setTimeout(refreshClientsList, 1500);
    } catch (error) {
        button.textContent = '失败';
        showMessage(`切换失败：${error.message}`, 'error');
    } finally {
        setTimeout(() => {
            button.textContent = original;
            button.disabled = false;
            if (select) select.disabled = false;
        }, 2000);
    }
}

async function requestScreenshot(button) {
    const clientId = button.dataset.clientId;
    if (!clientId) return;
    button.disabled = true;
    button.textContent = '截图中…';
    try {
        await requestJson(`/api/settings/clients/${encodeURIComponent(clientId)}/screenshot`, {method: 'POST'});
        button.textContent = '等待响应…';
    } catch (error) {
        button.textContent = '失败';
        showMessage(`截图请求失败：${error.message}`, 'error');
        setTimeout(() => {
            button.textContent = '截图';
            button.disabled = false;
        }, 2000);
    }
}

export function bindClientEvents() {
    const refreshButton = $('#refreshClientsBtn');
    if (refreshButton) refreshButton.addEventListener('click', refreshClientsList);
    $('#reloadClientsButton').addEventListener('click', (event) => reloadClients(event.currentTarget));
    document.addEventListener('click', (event) => {
        const navigateButton = event.target.closest ? event.target.closest('.client-nav-btn') : null;
        if (navigateButton) navigateClient(navigateButton);
        const screenshotButton = event.target.closest ? event.target.closest('.client-screenshot-btn') : null;
        if (screenshotButton) requestScreenshot(screenshotButton);
    });
}
