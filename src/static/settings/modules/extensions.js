import {requestJson} from './api.js';
import {$, escHtml} from './dom.js';

const extensionState = {
    revision: 0,
    extensions: [],
    loading: false,
    onStateChange: null,
};
let requestSequence = 0;
let eventsBound = false;

const STATUS_LABELS = Object.freeze({
    active: '已启用',
    disabled: '已禁用',
    pending_enable: '重启后启用',
    pending_disable: '重启后停用',
    missing: '文件缺失',
    error: '加载错误',
});

function setMessage(text, kind = '') {
    const node = $('#extensionMessage');
    if (!node) return;
    node.className = `extension-message ${kind}`;
    node.textContent = text;
}

function contributionSummary(extension) {
    const sources = extension.contributions?.data_sources?.length || 0;
    const widgets = extension.contributions?.widgets?.length || 0;
    return `${sources} 个数据源 · ${widgets} 种卡片`;
}

function renderReferences(error) {
    const references = error?.references || [];
    if (!references.length) return '';
    const rows = references.map((reference) => {
        const widgets = (reference.widgets || []).map((widget) => widget.type || widget.id).join('、');
        return `<li><strong>${escHtml(reference.workspace_name || reference.workspace_id)}</strong>`
            + `<span>${escHtml(widgets)}</span></li>`;
    }).join('');
    return `<ul class="extension-reference-list">${rows}</ul>`;
}

function renderExtensions() {
    const container = $('#extensionsList');
    if (!container) return;
    if (!extensionState.extensions.length) {
        container.innerHTML = '<div class="empty-row">未发现扩展包</div>';
        return;
    }
    container.innerHTML = extensionState.extensions.map((extension) => {
        const status = extension.status || 'disabled';
        const locked = !!extension.locked;
        const error = extension.error || extension.diagnostics?.[0] || null;
        const cannotToggle = locked
            || (status === 'missing' && !extension.desired_enabled)
            || (status === 'error' && !extension.desired_enabled);
        const restart = extension.restart_required
            ? '<span class="extension-restart">需要重启</span>' : '';
        const errorHtml = error
            ? `<div class="extension-error"><strong>${escHtml(error.code || 'error')}</strong>`
                + `<span>${escHtml(error.message || '扩展不可用')}</span>${renderReferences(error)}</div>`
            : '';
        const dependencies = (extension.requires || []).map((item) => item.id || item).filter(Boolean);
        const dependencyHtml = dependencies.length
            ? `<small>依赖：${escHtml(dependencies.join('、'))}</small>` : '';
        const source = extension.source === 'core' ? '内置核心' : (extension.source === 'builtin' ? '随应用分发' : (extension.source === 'user' ? '本地目录' : extension.source));
        return `<article class="extension-card" data-extension-id="${escHtml(extension.id)}">`
            + '<div class="extension-card-head"><div>'
            + `<h3>${escHtml(extension.name || extension.id)}</h3>`
            + `<code>${escHtml(extension.id)}</code></div>`
            + `<label class="switch extension-toggle" title="${locked ? '核心扩展不可停用' : '修改期望启用状态'}">`
            + `<input type="checkbox" data-extension-toggle="${escHtml(extension.id)}" ${extension.desired_enabled ? 'checked' : ''} ${cannotToggle || extensionState.loading ? 'disabled' : ''}><span></span></label></div>`
            + `<p>${escHtml(extension.description || '无描述')}</p>`
            + '<div class="extension-meta">'
            + `<span>${escHtml(source || '未知来源')}</span><span>v${escHtml(extension.version || '—')}</span>`
            + `<span>${escHtml(contributionSummary(extension))}</span>${dependencyHtml}</div>`
            + '<div class="extension-status-row">'
            + `<span class="extension-status ${escHtml(status)}">${escHtml(STATUS_LABELS[status] || status)}</span>`
            + `${locked ? '<span class="extension-locked">锁定</span>' : ''}${restart}</div>`
            + errorHtml
            + '</article>';
    }).join('');
}

export async function loadExtensions() {
    const sequence = ++requestSequence;
    extensionState.loading = true;
    renderExtensions();
    setMessage('正在读取扩展状态…');
    try {
        const payload = await requestJson('/api/settings/extensions');
        if (sequence !== requestSequence) return;
        extensionState.revision = Number(payload.revision || 0);
        extensionState.extensions = Array.isArray(payload.extensions) ? payload.extensions : [];
        renderExtensions();
        const pending = extensionState.extensions.filter((item) => item.restart_required).length;
        setMessage(pending ? `${pending} 个扩展状态将在重启 Dashboard 后生效。` : '扩展状态已同步。', pending ? 'warning' : '');
    } catch (error) {
        if (sequence !== requestSequence) return;
        extensionState.extensions = [];
        renderExtensions();
        setMessage(`读取扩展失败：${error.message}`, 'error');
    } finally {
        if (sequence === requestSequence) {
            extensionState.loading = false;
            renderExtensions();
        }
    }
}

async function updateExtension(extensionId, desiredEnabled) {
    if (extensionState.loading) return;
    extensionState.loading = true;
    renderExtensions();
    setMessage('正在保存扩展状态…');
    try {
        const payload = await requestJson(`/api/settings/extensions/${encodeURIComponent(extensionId)}`, {
            method: 'PUT',
            body: {revision: extensionState.revision, desired_enabled: desiredEnabled},
        });
        extensionState.revision = Number(payload.revision ?? extensionState.revision);
        await loadExtensions();
        if (extensionState.onStateChange) await extensionState.onStateChange(extensionId);
    } catch (error) {
        const details = error.payload?.error || {};
        if (error.status === 409 && details.code === 'extension_conflict') {
            await loadExtensions();
            setMessage('扩展状态已被其他 Settings 页面修改，列表已刷新。', 'error');
        } else {
            await loadExtensions();
            const references = details.references || [];
            if (references.length) {
                const extension = extensionState.extensions.find((item) => item.id === extensionId);
                if (extension) extension.error = details;
                renderExtensions();
            }
            setMessage(
                references.length
                    ? `无法停用：仍有 ${references.length} 个工作区引用该扩展。`
                    : `保存失败：${error.message}`,
                'error',
            );
        }
    } finally {
        extensionState.loading = false;
        renderExtensions();
    }
}

async function rescanExtensions() {
    if (extensionState.loading) return;
    extensionState.loading = true;
    renderExtensions();
    setMessage('正在重新扫描本地扩展目录…');
    try {
        const payload = await requestJson('/api/settings/extensions/rescan', {method: 'POST'});
        extensionState.revision = Number(payload.revision ?? extensionState.revision);
        extensionState.extensions = Array.isArray(payload.extensions) ? payload.extensions : [];
        setMessage('扫描完成；新代码与启停状态需要重启 Dashboard 后生效。', 'success');
    } catch (error) {
        setMessage(`重新扫描失败：${error.message}`, 'error');
    } finally {
        extensionState.loading = false;
        renderExtensions();
    }
}

function bindEvents() {
    if (eventsBound) return;
    eventsBound = true;
    $('#extensionsList')?.addEventListener('change', (event) => {
        const toggle = event.target.closest?.('[data-extension-toggle]');
        if (!toggle) return;
        updateExtension(toggle.dataset.extensionToggle, !!toggle.checked);
    });
    $('#extensionsRescanButton')?.addEventListener('click', rescanExtensions);
}

export function initExtensions(options = {}) {
    extensionState.onStateChange = typeof options.onStateChange === 'function'
        ? options.onStateChange : null;
    bindEvents();
    renderExtensions();
    return loadExtensions();
}
