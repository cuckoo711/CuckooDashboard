import {$} from './dom.js';

export const state = {
    payload: null,
    dirty: false,
    saving: false,
    providerPanels: [],
    credentialRevision: null,
};

export function setDirty(dirty) {
    state.dirty = !!dirty;
    const badge = $('#dirtyBadge');
    const message = $('#saveMessage');
    if (badge) badge.hidden = !state.dirty;
    if (message && !state.saving) {
        message.className = 'save-message';
        message.textContent = state.dirty ? '有未保存修改' : '配置未修改';
    }
}

export function showMessage(text, kind) {
    const message = $('#saveMessage');
    if (!message) return;
    message.className = `save-message ${kind || ''}`;
    message.textContent = text;
}
