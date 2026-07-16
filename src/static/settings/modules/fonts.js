import {$, el, setChecked, setValue} from './dom.js';
import {requestJson} from './api.js';
import {state, setDirty} from './state.js';

function formatBytes(bytes) {
    if (!bytes) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    let index = 0;
    let value = bytes;
    while (value >= 1024 && index < units.length - 1) {
        value /= 1024;
        index += 1;
    }
    return `${value.toFixed(value < 10 && index > 0 ? 2 : 1)} ${units[index]}`;
}

export function renderFontPanel(fonts, currentFilename, enabled) {
    const select = $('#fontFilename');
    const list = $('#fontList');
    if (!select || !list) return;
    select.replaceChildren();

    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = fonts.length ? '未选择' : '暂无可用字体，请先上传';
    select.appendChild(placeholder);
    fonts.forEach((item) => {
        const option = document.createElement('option');
        option.value = item.filename;
        option.textContent = `${item.filename}  (${formatBytes(item.size)})`;
        select.appendChild(option);
    });
    if (currentFilename && !fonts.some((item) => item.filename === currentFilename)) {
        const missing = document.createElement('option');
        missing.value = currentFilename;
        missing.textContent = `${currentFilename}（缺失）`;
        select.appendChild(missing);
    }
    select.value = currentFilename || '';
    setChecked('fontEnabled', !!enabled);

    list.replaceChildren();
    if (!fonts.length) {
        list.appendChild(el('div', 'empty-row', '尚未上传字体'));
        return;
    }
    fonts.forEach((item) => {
        const row = el('div', `font-item${item.filename === currentFilename ? ' is-current' : ''}`);
        row.appendChild(el('span', 'font-item-name', item.filename));
        row.appendChild(el('span', 'font-item-size', formatBytes(item.size)));
        const remove = el('button', 'small-btn danger-btn', '删除');
        remove.type = 'button';
        remove.dataset.deleteFont = item.filename;
        row.appendChild(remove);
        list.appendChild(row);
    });
}

export function renderFontSizePanel(config = {}) {
    setValue('fontSizeTitleText', config.title_text || 'Cuckoo Dashboard');
    setValue('fontSizeTitle', config.title);
    setValue('fontSizeClock', config.clock);
    setValue('fontSizeDate', config.date);
    setValue('fontSizeCardHead', config.card_head);
    setValue('fontSizeCardFoot', config.card_foot);
    setValue('fontSizeCardBody', config.card_body);
    setValue('fontSizeOffset', config.offset);
}

export function setFontUploadStatus(text, kind) {
    const node = $('#fontUploadStatus');
    if (!node) return;
    node.className = `field-help font-upload-status${kind ? ` ${kind}` : ''}`;
    node.textContent = text || '';
}

function readFileAsBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            const result = reader.result || '';
            const index = String(result).indexOf(',');
            resolve(index >= 0 ? String(result).slice(index + 1) : String(result));
        };
        reader.onerror = () => reject(new Error('读取文件失败'));
        reader.readAsDataURL(file);
    });
}

export async function refreshFontList(currentFilename) {
    const data = await requestJson('/api/fonts');
    const fonts = (data && data.fonts) || [];
    if (state.payload && state.payload.options) state.payload.options.fonts = fonts;
    const enabled = !!$('#fontEnabled').checked;
    const selected = currentFilename !== undefined ? currentFilename : $('#fontFilename').value;
    renderFontPanel(fonts, selected, enabled);
}

export async function handleFontUpload(file) {
    if (!file) return;
    if (file.size > 20 * 1024 * 1024) {
        setFontUploadStatus('文件超过 20MB，无法上传', 'error');
        return;
    }
    const button = $('#fontUploadBtn');
    button.disabled = true;
    setFontUploadStatus(`正在上传 ${file.name}…`);
    try {
        const data = await readFileAsBase64(file);
        const result = await requestJson('/api/fonts/upload', {
            method: 'POST',
            body: {filename: file.name, data},
        });
        setFontUploadStatus(`已上传 ${result.filename}`, 'success');
        await refreshFontList(result.filename);
        setDirty(true);
    } catch (error) {
        const field = error.payload && error.payload.error && error.payload.error.field;
        setFontUploadStatus(`${field ? `${field}：` : ''}${error.message}`, 'error');
    } finally {
        button.disabled = false;
    }
}

export async function handleFontDelete(filename) {
    if (!filename || !window.confirm(`确定删除字体 ${filename} 吗？`)) return;
    try {
        await requestJson('/api/fonts/delete', {method: 'POST', body: {filename}});
        setFontUploadStatus(`已删除 ${filename}`, 'success');
        const current = $('#fontFilename').value;
        await refreshFontList(current === filename ? '' : current);
        if (current === filename) setDirty(true);
    } catch (error) {
        setFontUploadStatus(error.message, 'error');
    }
}

export function bindFontEvents() {
    $('#fontUploadBtn').addEventListener('click', () => $('#fontUploadInput').click());
    $('#fontUploadInput').addEventListener('change', (event) => {
        const file = event.target.files && event.target.files[0];
        handleFontUpload(file);
        event.target.value = '';
    });

    const zone = $('#fontDropZone');
    if (zone) {
        let dragCount = 0;
        zone.addEventListener('dragenter', (event) => {
            event.preventDefault();
            dragCount += 1;
            zone.classList.add('drag-over');
        });
        zone.addEventListener('dragover', (event) => {
            event.preventDefault();
            event.dataTransfer.dropEffect = 'copy';
        });
        zone.addEventListener('dragleave', (event) => {
            event.preventDefault();
            dragCount -= 1;
            if (dragCount <= 0) {
                dragCount = 0;
                zone.classList.remove('drag-over');
            }
        });
        zone.addEventListener('drop', (event) => {
            event.preventDefault();
            dragCount = 0;
            zone.classList.remove('drag-over');
            const files = event.dataTransfer && event.dataTransfer.files;
            if (files && files.length) handleFontUpload(files[0]);
        });
    }

    document.addEventListener('click', (event) => {
        const button = event.target.closest ? event.target.closest('[data-delete-font]') : null;
        if (!button) return;
        event.preventDefault();
        handleFontDelete(button.dataset.deleteFont);
    });
}
