import {$, escHtml} from './dom.js';
import {requestJson} from './api.js';
import {showMessage} from './state.js';

const PAGE_LABELS = {dashboard: '看板', music: '音乐舞台', unknown: '未知'};
const PAGE_ICONS = {dashboard: '📊', music: '🎵', unknown: '❓'};
let settingsWebSocket = null;
let settingsWebSocketRetry = 1000;

function renderClientsList(clients) {
    const container = $('#clientsList');
    if (!clients || !clients.length) {
        container.innerHTML = '<div class="empty-row">暂无在线客户端</div>';
        return;
    }
    container.innerHTML = clients.map((client) => {
        const page = client.page || 'unknown';
        const label = PAGE_LABELS[page] || page;
        const icon = PAGE_ICONS[page] || '❓';
        const targetPage = page === 'music' ? 'dashboard' : 'music';
        const targetLabel = PAGE_LABELS[targetPage];
        return '<div class="client-row" data-client-id="' + escHtml(client.id) + '">' +
            '<span class="client-id">' + escHtml(client.id) + '</span>' +
            '<span class="client-page">' + icon + ' ' + escHtml(label) + '</span>' +
            '<button type="button" class="small-btn client-screenshot-btn" data-client-id="' + escHtml(client.id) + '">截图</button>' +
            '<button type="button" class="small-btn client-nav-btn" data-navigate-to="' + targetPage + '">切换到 ' + escHtml(targetLabel) + '</button>' +
            '</div>';
    }).join('');
}

export async function refreshClientsList() {
    const button = $('#refreshClientsBtn');
    if (button) button.disabled = true;
    try {
        const data = await requestJson('/api/settings/clients');
        renderClientsList(data.clients || []);
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
    const targetPage = button.dataset.navigateTo;
    if (!clientId || !targetPage) return;
    button.disabled = true;
    const original = button.textContent;
    button.textContent = '发送中…';
    try {
        await requestJson(`/api/settings/clients/${encodeURIComponent(clientId)}/navigate`, {
            method: 'POST',
            body: {page: targetPage},
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
