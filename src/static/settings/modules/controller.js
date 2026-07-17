import {$} from './dom.js';
import {requestJson} from './api.js';
import {state, showMessage} from './state.js';
import {collect} from './collect.js';
import {render} from './render.js';

export async function loadSettings() {
    const loading = $('#loadingState');
    const errorState = $('#errorState');
    const app = $('#settingsApp');
    const footer = $('#settingsFooter');
    if (loading) loading.hidden = false;
    if (errorState) errorState.hidden = true;
    if (app) app.hidden = true;
    if (footer) footer.hidden = true;
    try {
        const payload = await requestJson('/api/settings');
        render(payload);
        if (app) app.hidden = false;
        if (footer) footer.hidden = false;
    } catch (error) {
        console.error('[settings] load failed:', error);
        if (errorState) {
            errorState.textContent = `无法读取配置：${error.message}`;
            errorState.hidden = false;
        }
    } finally {
        if (loading) loading.hidden = true;
    }
}


export async function saveSettings() {
    if (state.saving) return;
    state.saving = true;
    const button = $('#saveButton');
    button.disabled = true;
    showMessage('正在保存…');
    try {
        const result = await requestJson('/api/settings', {method: 'POST', body: collect()});
        render(result);
        if (result.errors && result.errors.length) {
            showMessage(`已保存，但部分运行时模块刷新失败：${result.errors.join('；')}`, 'error');
        } else {
            showMessage('已保存，配置已立即生效', 'success');
        }
    } catch (error) {
        const field = error.payload && error.payload.error && error.payload.error.field;
        showMessage(`${field ? `${field}：` : ''}${error.message}`, 'error');
    } finally {
        state.saving = false;
        button.disabled = false;
    }
}
