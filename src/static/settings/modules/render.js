import {$, el, fillSelect, rowParts, setChecked, setValue} from './dom.js';
import {state, setDirty} from './state.js';
import {setupGlobalSecret} from './secrets.js';
import {addScalarControl} from './schema/fields.js';
import {renderProviderPanels} from './schema/provider-panels.js';
import {renderFontPanel, renderFontSizePanel, setFontUploadStatus} from './fonts.js';
import {renderMusicPanel} from './music-settings.js';

export function renderOffPeakRanges(ranges) {
    const container = $('#offPeakRanges');
    container.replaceChildren();
    (ranges || []).forEach((item) => {
        const parts = rowParts('off-peak');
        parts.fields.className += ' three';
        addScalarControl(parts.fields, {key: 'start', label: '开始', type: 'time'}, item.start || '00:00', true).classList.add('range-start');
        addScalarControl(parts.fields, {key: 'end', label: '结束', type: 'time'}, item.end || '08:00', true).classList.add('range-end');
        parts.fields.appendChild(el('div', 'row-field row-note', '支持跨午夜'));
        container.appendChild(parts.row);
    });
    if (!container.children.length) container.appendChild(el('div', 'empty-row', '未配置闲时区间'));
}

export function addOffPeakRange() {
    const container = $('#offPeakRanges');
    const empty = $('.empty-row', container);
    if (empty) empty.remove();
    const parts = rowParts('off-peak');
    parts.fields.className += ' three';
    addScalarControl(parts.fields, {key: 'start', label: '开始', type: 'time'}, '00:00', true).classList.add('range-start');
    addScalarControl(parts.fields, {key: 'end', label: '结束', type: 'time'}, '08:00', true).classList.add('range-end');
    parts.fields.appendChild(el('div', 'row-field row-note', '支持跨午夜'));
    container.appendChild(parts.row);
    setDirty(true);
}

function addBalanceRow(entry, options = {}) {
    entry = entry || {provider: '', name: '', color: '#888888', enabled: true};
    const container = $('#balanceRows');
    const parts = rowParts('balance');
    parts.fields.className += ' four';
    const provider = addScalarControl(parts.fields, {
        key: 'provider',
        label: 'Provider',
        type: 'select',
        options: options.balance_providers || [],
        allow_empty: true,
    }, entry.provider, true);
    provider.classList.add('balance-provider');
    addScalarControl(parts.fields, {key: 'name', label: '显示名称', type: 'string'}, entry.name || '', true).classList.add('balance-name');
    addScalarControl(parts.fields, {key: 'color', label: '颜色', type: 'color'}, entry.color || '#888888', true).classList.add('balance-color');

    const enabledField = el('label', 'row-field check-row');
    const switchLabel = el('label', 'switch');
    const enabled = document.createElement('input');
    enabled.type = 'checkbox';
    enabled.className = 'balance-enabled';
    enabled.checked = entry.enabled !== false;
    switchLabel.append(enabled, el('span'));
    enabledField.append(switchLabel, el('span', 'balance-enabled-label', '启用'));
    parts.fields.appendChild(enabledField);
    container.appendChild(parts.row);
}

export function renderBalances(entries, options) {
    const container = $('#balanceRows');
    container.replaceChildren();
    (entries || []).forEach((entry) => addBalanceRow(entry, options));
    if (!container.children.length) container.appendChild(el('div', 'empty-row', '未配置余额 Footer'));
}

export function addBalance() {
    const container = $('#balanceRows');
    const empty = $('.empty-row', container);
    if (empty) empty.remove();
    addBalanceRow(undefined, (state.payload && state.payload.options) || {});
    setDirty(true);
}

function addVramRow(name, amount) {
    const container = $('#vramRows');
    const parts = rowParts('vram');
    parts.fields.className += ' two';
    addScalarControl(parts.fields, {key: 'name', label: 'GPU 名称', type: 'string'}, name || '', true).classList.add('vram-name');
    const amountInput = addScalarControl(parts.fields, {
        key: 'amount',
        label: '显存（GB）',
        type: 'number',
        min: 0.01,
        step: 0.01,
    }, amount === undefined ? '' : amount, true);
    amountInput.classList.add('vram-amount');
    container.appendChild(parts.row);
}

export function renderVram(mapping) {
    const container = $('#vramRows');
    container.replaceChildren();
    Object.keys(mapping || {}).forEach((name) => addVramRow(name, mapping[name]));
    if (!container.children.length) container.appendChild(el('div', 'empty-row', '未配置显存覆盖'));
}

export function addVram() {
    const container = $('#vramRows');
    const empty = $('.empty-row', container);
    if (empty) empty.remove();
    addVramRow('', '');
    setDirty(true);
}

function renderThemeOptions(options, current) {
    const select = $('#theme');
    select.replaceChildren();
    (options.themes || []).forEach((name) => {
        const option = document.createElement('option');
        option.value = name;
        option.textContent = name === 'dark' ? 'Dark' : (name === 'mono' ? 'Mono' : name);
        select.appendChild(option);
    });
    select.value = current || (options.themes || [])[0] || '';
}

export function render(payload) {
    state.payload = payload;
    state.credentialRevision = payload.credential_revision === undefined ? null : payload.credential_revision;
    const config = payload.config || {};
    const dashboard = config.dashboard || {};
    const offPeak = dashboard.off_peak_badge || {};
    const vibe = dashboard.vibe_coding || {};
    const hardware = config.hardware_overrides || {};
    const logging = config.logging || {};
    const options = payload.options || {};

    setupGlobalSecret('dashboardToken', dashboard.token, 'dashboard.token');
    setupGlobalSecret('githubToken', config.github_token, 'github_token');
    setChecked('offPeakEnabled', offPeak.enabled !== false);
    renderOffPeakRanges(offPeak.ranges || []);
    fillSelect($('#ringProvider'), options.ring_providers || [], (vibe.ring || {}).provider || '', '自动选择');
    setValue('ringItem', (vibe.ring || {}).item || '');
    fillSelect($('#modelBarsProvider'), options.model_bar_providers || [], (vibe.model_bars || {}).provider || '', '自动选择');
    renderBalances(vibe.balances || [], options);
    renderProviderPanels(payload.providers || []);

    const font = dashboard.font || {};
    renderFontPanel(options.fonts || [], font.filename || '', !!font.enabled);
    setFontUploadStatus('');
    renderFontSizePanel(dashboard.font_size || {});

    setValue('cpuModel', hardware.cpu_model);
    setValue('memInstalled', hardware.mem_installed_gb);
    setValue('memName', hardware.mem_name);
    setValue('gpuModel', hardware.gpu_model);
    renderVram(hardware.gpu_vram_gb || {});
    setValue('apuDeviceIds', (hardware.apu_device_ids || []).join('\n'));
    setValue('logLevel', logging.level || 'INFO');
    setValue('logMode', logging.mode || 'daily');
    setValue('logDir', logging.dir || 'logs');
    setValue('keepDays', logging.keep_days);
    setValue('maxSize', logging.max_size_mb);
    setValue('maxBackups', logging.max_backups);
    setChecked('logConsole', logging.console !== false);
    renderThemeOptions(options, config.theme || 'dark');
    setValue('lyricOffset', config.lyric_offset === undefined ? 0 : config.lyric_offset);
    setChecked('vibeActive', config.vibe_active);
    renderMusicPanel(config.music || {}, options);
    setDirty(false);
}
