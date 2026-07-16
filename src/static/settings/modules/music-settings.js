import {$, setChecked, setValue} from './dom.js';
import {requestJson} from './api.js';
import {state, showMessage} from './state.js';

export function fillCaptureDeviceSelect(devices = [], current) {
    const select = $('#musicCaptureDevice');
    if (!select) return 0;
    const preferred = devices.filter((item) => {
        if (!item || item.disabled || item.id === '__none__') return false;
        if (item.id === 'auto' || item.kind === 'auto') return true;
        return item.kind === 'loopback' || item.backend === 'soundcard';
    });
    const advanced = devices.filter((item) => (
        item && !item.disabled && item.id !== '__none__' && !preferred.includes(item)
    ));
    select.replaceChildren();

    const appendOption = (item, parent = select) => {
        const option = document.createElement('option');
        option.value = item.id || 'auto';
        option.textContent = item.label || item.id || '';
        parent.appendChild(option);
    };
    if (preferred.length) {
        preferred.forEach((item) => appendOption(item));
    } else {
        const empty = document.createElement('option');
        empty.value = 'auto';
        empty.textContent = '未检测到 Loopback（请点刷新设备 / 重启 Dashboard）';
        select.appendChild(empty);
    }
    if (advanced.length) {
        const group = document.createElement('optgroup');
        group.label = '备选/可能无效（不推荐）';
        advanced.forEach((item) => appendOption(item, group));
        select.appendChild(group);
    }

    const selected = current || 'auto';
    if (selected && !devices.some((device) => device && device.id === selected)) {
        const stale = document.createElement('option');
        stale.value = selected;
        stale.textContent = `${selected}（已保存，但当前列表找不到）`;
        select.appendChild(stale);
    }
    select.value = selected;
    return preferred.filter((device) => device.kind === 'loopback').length;
}

export async function refreshCaptureDevices(showToast) {
    const help = $('#musicDeviceStatus');
    try {
        if (help) help.textContent = '正在刷新设备列表…';
        const data = await requestJson('/api/music/capture-devices');
        const devices = (data && data.devices) || [];
        const current = (data && data.current) || ($('#musicCaptureDevice') && $('#musicCaptureDevice').value) || 'auto';
        const loopCount = fillCaptureDeviceSelect(devices, current);
        if (state.payload && state.payload.options) state.payload.options.capture_devices = devices;
        if (help) {
            const live = (data && data.status && data.status.device) || '';
            help.textContent = `检测到 Loopback ${data.loopback_count != null ? data.loopback_count : loopCount} 个。当前配置：${current}${live ? `；当前采集：${live}` : ''}。请选正在出声的 Loopback 设备后保存。`;
        }
        if (showToast) {
            showMessage(
                loopCount ? `已刷新，Loopback ${loopCount} 个` : '已刷新，但仍未发现 Loopback',
                loopCount ? 'ok' : 'warn',
            );
        }
        return data;
    } catch (error) {
        if (help) help.textContent = `刷新设备失败：${error.message || error}`;
        if (showToast) showMessage(`刷新设备失败：${error.message || error}`, 'error');
        throw error;
    }
}

export function renderMusicPanel(music = {}, options = {}) {
    const devices = options.capture_devices || [];
    const loopCount = fillCaptureDeviceSelect(devices, music.capture_device || 'auto');
    setChecked('musicSpectrumEnabled', music.spectrum_enabled !== false);
    setChecked('musicAutoCalibrate', music.auto_calibrate !== false);
    setValue('musicSpectrumOffset', music.spectrum_offset_ms === undefined ? 40 : music.spectrum_offset_ms);
    setValue('musicBeatLead', music.beat_lead_ms === undefined ? 20 : music.beat_lead_ms);
    setValue('musicBins', music.bins === undefined ? 48 : music.bins);
    setValue('musicRenderFps', music.render_fps === undefined ? 0 : music.render_fps);
    setValue('musicRenderBars', music.render_bars === undefined ? 0 : music.render_bars);
    const help = $('#musicDeviceStatus');
    if (help) {
        help.textContent = `初步列表 Loopback ${loopCount} 个；若只有“自动”，请点“刷新设备”。当前：${music.capture_device || 'auto'}`;
    }
    refreshCaptureDevices(false).catch(() => {});
}
