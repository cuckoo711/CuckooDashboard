import {$, $$, el, nextId} from './dom.js';
import {requestJson} from './api.js';
import {setDirty, showMessage} from './state.js';

export function setupSecretInput(input, meta, path, options = {}) {
    input.type = 'password';
    input.value = '';
    input.dataset.secretPath = path || '';
    input.dataset.secretAction = 'keep';
    input.dataset.revealed = '0';
    input.dataset.objectField = options.field || '';
    input.dataset.identityKey = options.identityKey || '';
    input.placeholder = meta && meta.configured ? '已设置，留空保持当前值' : '尚未设置';
    if (!path) input.disabled = true;
}

export function createSecretControl(container, labelText, meta, path, options = {}) {
    const field = el('div', options.row ? 'row-field' : 'field');
    const label = el('label', options.row ? 'row-label' : '', labelText);
    const control = el('div', 'secret-control');
    const input = document.createElement('input');
    input.id = nextId('secret');
    setupSecretInput(input, meta, path, options);

    const reveal = el('button', 'small-btn reveal-btn', '查看');
    reveal.type = 'button';
    reveal.dataset.revealFor = input.id;
    const clear = el('button', 'small-btn danger-btn clear-secret-btn', '清空');
    clear.type = 'button';
    clear.dataset.clearFor = input.id;

    control.append(input, reveal, clear);
    field.append(label, control);
    if (!options.row) field.appendChild(el('div', 'field-help'));
    container.appendChild(field);
    return input;
}

export function setupGlobalSecret(id, meta, path) {
    const input = document.getElementById(id);
    if (!input) return;
    setupSecretInput(input, meta, path);
    input.disabled = false;
}

export function secretAction(input) {
    const action = (input && input.dataset.secretAction) || 'keep';
    const result = {action};
    if (action === 'set') result.value = input.value;
    return result;
}

export function updateSecretAction(input) {
    if (!input.matches('[data-secret-path]')) return;
    if (input.value) input.dataset.secretAction = 'set';
    else if (input.dataset.revealed === '1' || input.dataset.secretAction === 'clear') input.dataset.secretAction = 'clear';
    else input.dataset.secretAction = 'keep';
}

export async function handleSecretClick(target) {
    const revealFor = target.dataset.revealFor;
    const clearFor = target.dataset.clearFor;
    const input = document.getElementById(revealFor || clearFor);
    if (!input) return;

    if (revealFor) {
        if (!input.dataset.secretPath) return;
        if (input.dataset.revealed === '1') {
            input.type = input.type === 'password' ? 'text' : 'password';
            target.textContent = input.type === 'password' ? '查看' : '隐藏';
            return;
        }
        const body = {path: input.dataset.secretPath};
        const row = input.closest('.provider-object-row');
        if (row) {
            const identityKey = input.dataset.identityKey;
            const identityInput = $$('.object-input', row).find((node) => node.dataset.fieldKey === identityKey);
            body.identity = identityInput && identityInput.value.trim();
            body.field = input.dataset.objectField;
            if (!body.identity) {
                showMessage('请先填写列表项的标识字段', 'error');
                return;
            }
        }
        target.disabled = true;
        try {
            const result = await requestJson('/api/settings/reveal', {method: 'POST', body});
            input.value = result.value || '';
            input.dataset.secretAction = result.value ? 'set' : 'keep';
            input.dataset.revealed = '1';
            input.type = 'text';
            target.textContent = '隐藏';
        } catch (error) {
            showMessage(`查看敏感字段失败：${error.message}`, 'error');
        } finally {
            target.disabled = false;
        }
    }

    if (clearFor) {
        input.value = '';
        input.type = 'password';
        input.dataset.secretAction = 'clear';
        input.dataset.revealed = '0';
        input.placeholder = '保存后清空';
        const revealButton = $(`[data-reveal-for="${clearFor}"]`);
        if (revealButton) revealButton.textContent = '查看';
        setDirty(true);
    }
}
