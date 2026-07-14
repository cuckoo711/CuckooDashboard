/* 配置后台：只通过同源 API 读写，不在浏览器存储敏感值。 */
(function () {
    'use strict';

    var state = {payload: null, dirty: false, saving: false};
    var $ = function (selector, root) { return (root || document).querySelector(selector); };
    var $$ = function (selector, root) { return Array.prototype.slice.call((root || document).querySelectorAll(selector)); };

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function setValue(id, value) {
        var node = document.getElementById(id);
        if (node) node.value = value === null || value === undefined ? '' : String(value);
    }

    function setChecked(id, value) {
        var node = document.getElementById(id);
        if (node) node.checked = !!value;
    }

    function setDirty(dirty) {
        state.dirty = !!dirty;
        var badge = $('#dirtyBadge');
        var message = $('#saveMessage');
        if (badge) badge.hidden = !state.dirty;
        if (message && !state.saving) {
            message.className = 'save-message';
            message.textContent = state.dirty ? '有未保存修改' : '配置未修改';
        }
    }

    function showMessage(text, kind) {
        var message = $('#saveMessage');
        if (!message) return;
        message.className = 'save-message ' + (kind || '');
        message.textContent = text;
    }

    async function requestJson(url, options) {
        options = options || {};
        options.credentials = 'same-origin';
        options.headers = options.headers || {};
        options.headers['X-Requested-With'] = 'CuckooSettings';
        if (options.body && typeof options.body !== 'string') {
            options.headers['Content-Type'] = 'application/json';
            options.body = JSON.stringify(options.body);
        }
        var response = await fetch(url, options);
        var data = {};
        try { data = await response.json(); } catch (e) {}
        if (!response.ok) {
            var error = new Error((data.error && data.error.message) || ('HTTP ' + response.status));
            error.payload = data;
            error.status = response.status;
            throw error;
        }
        return data;
    }

    function fillSelect(select, values, current, autoLabel) {
        select.replaceChildren();
        var auto = document.createElement('option');
        auto.value = '';
        auto.textContent = autoLabel || '自动选择';
        select.appendChild(auto);
        (values || []).forEach(function (value) {
            var option = document.createElement('option');
            option.value = value;
            option.textContent = value;
            select.appendChild(option);
        });
        if (current && !(values || []).some(function (item) { return item === current; })) {
            var currentOption = document.createElement('option');
            currentOption.value = current;
            currentOption.textContent = current + '（当前值）';
            select.appendChild(currentOption);
        }
        select.value = current || '';
    }

    function addLabeledControl(container, labelText, type, value, className) {
        var field = el('div', 'row-field' + (className ? ' ' + className : ''));
        var label = el('label', 'row-label', labelText);
        var input = document.createElement(type === 'select' ? 'select' : 'input');
        if (type !== 'select') input.type = type;
        if (value !== undefined && value !== null) input.value = value;
        field.appendChild(label);
        field.appendChild(input);
        container.appendChild(field);
        return input;
    }

    function rowParts(kind) {
        var row = el('div', 'repeat-row');
        row.dataset.rowKind = kind;
        var fields = el('div', 'row-fields');
        var remove = el('button', 'remove-btn', '移除');
        remove.type = 'button';
        remove.dataset.removeRow = '1';
        row.appendChild(fields);
        row.appendChild(remove);
        return {row: row, fields: fields};
    }

    function createSecretField(container, labelText, inputId, path, meta, compact) {
        var field = el('div', 'row-field' + (compact ? ' compact-secret-field' : ''));
        var label = el('label', 'row-label', labelText);
        var control = el('div', 'secret-control');
        var input = document.createElement('input');
        input.id = inputId;
        input.type = 'password';
        input.placeholder = meta && meta.configured ? '已设置，留空保持当前值' : '尚未设置';
        input.dataset.secretPath = path || '';
        input.dataset.secretAction = 'keep';
        input.dataset.revealed = '0';
        var reveal = el('button', 'small-btn reveal-btn', '查看');
        reveal.type = 'button';
        reveal.dataset.revealFor = inputId;
        if (!path) reveal.disabled = true;
        var clear = el('button', 'small-btn danger-btn clear-secret-btn', '清空');
        clear.type = 'button';
        clear.dataset.clearFor = inputId;
        control.appendChild(input);
        control.appendChild(reveal);
        control.appendChild(clear);
        field.appendChild(label);
        field.appendChild(control);
        container.appendChild(field);
        return input;
    }

    function setupSecretInput(input, meta) {
        if (!input) return;
        input.value = '';
        input.type = 'password';
        input.dataset.secretAction = 'keep';
        input.dataset.revealed = '0';
        input.placeholder = meta && meta.configured ? '已设置，留空保持当前值' : '尚未设置';
    }

    function renderOffPeakRanges(ranges) {
        var container = $('#offPeakRanges');
        container.replaceChildren();
        (ranges || []).forEach(function (item) {
            var parts = rowParts('off-peak');
            parts.fields.className += ' three';
            addLabeledControl(parts.fields, '开始', 'time', item.start || '00:00', 'range-start');
            addLabeledControl(parts.fields, '结束', 'time', item.end || '08:00', 'range-end');
            var hint = el('div', 'row-field row-note', '支持跨午夜');
            parts.fields.appendChild(hint);
            container.appendChild(parts.row);
        });
        if (!container.children.length) container.appendChild(el('div', 'empty-row', '未配置闲时区间'));
    }

    function renderBalances(entries, options) {
        var container = $('#balanceRows');
        container.replaceChildren();
        (entries || []).forEach(function (entry) { addBalanceRow(entry, options); });
        if (!container.children.length) container.appendChild(el('div', 'empty-row', '未配置余额 Footer'));
    }

    function addBalanceRow(entry, options) {
        entry = entry || {provider: '', name: '', color: '#888888', enabled: true};
        var container = $('#balanceRows');
        var parts = rowParts('balance');
        parts.fields.className += ' four';
        var provider = addLabeledControl(parts.fields, 'Provider', 'select', '', 'balance-provider');
        fillSelect(provider, options.balance_providers, entry.provider, '选择 Provider');
        addLabeledControl(parts.fields, '显示名称', 'text', entry.name || '', 'balance-name');
        addLabeledControl(parts.fields, '颜色', 'color', entry.color || '#888888', 'balance-color');
        var enabledField = el('label', 'row-field check-row');
        var enabled = document.createElement('input');
        enabled.type = 'checkbox';
        enabled.className = 'balance-enabled';
        enabled.checked = entry.enabled !== false;
        enabledField.appendChild(enabled);
        enabledField.appendChild(el('span', 'row-label check-label', '启用'));
        parts.fields.appendChild(enabledField);
        container.appendChild(parts.row);
    }

    function renderLocalUrls(entries) {
        var container = $('#localUrlRows');
        container.replaceChildren();
        (entries || []).forEach(function (entry, index) { addLocalUrlRow(entry, index); });
        if (!container.children.length) container.appendChild(el('div', 'empty-row', '未配置本地平台实例'));
    }

    function addLocalUrlRow(entry, index) {
        entry = entry || {url: '', original_url: '', password: {configured: false}};
        var container = $('#localUrlRows');
        var parts = rowParts('local-url');
        parts.fields.className += ' two';
        var url = addLabeledControl(parts.fields, '服务 URL', 'url', entry.url || '', 'local-url');
        url.dataset.originalUrl = entry.original_url || entry.url || '';
        var passwordId = 'localUrlPassword_' + Date.now() + '_' + Math.floor(Math.random() * 10000);
        var secret = createSecretField(parts.fields, '单实例密码覆盖', passwordId,
            index === undefined ? '' : ('local_platforms.urls[' + index + '].password'), entry.password, true);
        secret.dataset.originalUrl = entry.original_url || entry.url || '';
        parts.row.dataset.originalUrl = entry.original_url || entry.url || '';
        container.appendChild(parts.row);
    }

    function renderVram(mapping) {
        var container = $('#vramRows');
        container.replaceChildren();
        Object.keys(mapping || {}).forEach(function (name) { addVramRow(name, mapping[name]); });
        if (!container.children.length) container.appendChild(el('div', 'empty-row', '未配置显存覆盖'));
    }

    function addVramRow(name, amount) {
        var container = $('#vramRows');
        var parts = rowParts('vram');
        parts.fields.className += ' two';
        addLabeledControl(parts.fields, 'GPU 名称', 'text', name || '', 'vram-name');
        var amountInput = addLabeledControl(parts.fields, '显存（GB）', 'number', amount === undefined ? '' : amount, 'vram-amount');
        amountInput.min = '0.01';
        amountInput.step = '0.01';
        container.appendChild(parts.row);
    }

    function renderThemeOptions(options, current) {
        var select = $('#theme');
        select.replaceChildren();
        (options.themes || []).forEach(function (name) {
            var option = document.createElement('option');
            option.value = name;
            option.textContent = name === 'dark' ? 'Dark' : (name === 'mono' ? 'Mono' : name);
            select.appendChild(option);
        });
        select.value = current || (options.themes || [])[0] || '';
    }

    function render(payload) {
        state.payload = payload;
        var cfg = payload.config || {};
        var options = payload.options || {};
        var dashboard = cfg.dashboard || {};
        var offPeak = dashboard.off_peak_badge || {};
        var vibe = dashboard.vibe_coding || {};
        var local = cfg.local_platforms || {};
        var nug = cfg.nug || {};
        var hardware = cfg.hardware_overrides || {};
        var logging = cfg.logging || {};

        setupSecretInput($('#dashboardToken'), dashboard.token);
        setupSecretInput($('#githubToken'), cfg.github_token);
        setupSecretInput($('#localPassword'), local.password);
        setupSecretInput($('#nugPassword'), nug.password);
        setChecked('offPeakEnabled', offPeak.enabled !== false);
        renderOffPeakRanges(offPeak.ranges || []);

        fillSelect($('#ringProvider'), options.ring_providers || [], (vibe.ring || {}).provider || '', '自动选择');
        setValue('ringItem', (vibe.ring || {}).item || '');
        fillSelect($('#modelBarsProvider'), options.model_bar_providers || [], (vibe.model_bars || {}).provider || '', '自动选择');
        renderBalances(vibe.balances || [], options);

        setChecked('localEnabled', local.enabled);
        setValue('localUsername', local.username);
        renderLocalUrls(local.urls || []);
        setChecked('nugEnabled', nug.enabled);
        setValue('nugUrl', nug.url);
        setValue('nugUsername', nug.username);

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

        renderThemeOptions(options, cfg.theme || 'dark');
        setValue('lyricOffset', cfg.lyric_offset === undefined ? 0 : cfg.lyric_offset);
        setChecked('vibeActive', cfg.vibe_active);
        setDirty(false);
    }

    function numberValue(id, fallback) {
        var value = $(id).value.trim();
        if (!value) return fallback;
        var number = Number(value);
        return Number.isFinite(number) ? number : fallback;
    }

    function collectSecret(id, path) {
        var input = document.getElementById(id);
        var action = input && input.dataset.secretAction || 'keep';
        var result = {action: action};
        if (action === 'set') result.value = input.value;
        return [path, result];
    }

    function collect() {
        var ranges = $$('#offPeakRanges .repeat-row').map(function (row) {
            return {
                start: $('.range-start', row).value,
                end: $('.range-end', row).value
            };
        });
        var balances = $$('#balanceRows .repeat-row').map(function (row) {
            return {
                provider: $('.balance-provider', row).value,
                name: $('.balance-name', row).value,
                color: $('.balance-color', row).value,
                enabled: $('.balance-enabled', row).checked
            };
        });
        var urls = $$('#localUrlRows .repeat-row').map(function (row) {
            var input = $('.local-url', row);
            return {url: input.value, original_url: input.dataset.originalUrl || input.value};
        });
        var urlSecrets = $$('#localUrlRows .repeat-row').map(function (row) {
            var input = $('.secret-control input', row);
            var action = input.dataset.secretAction || 'keep';
            var result = {
                url: $('.local-url', row).value,
                original_url: input.dataset.originalUrl || $('.local-url', row).dataset.originalUrl || $('.local-url', row).value,
                action: action
            };
            if (action === 'set') result.value = input.value;
            return result;
        });
        var vram = {};
        $$('#vramRows .repeat-row').forEach(function (row) {
            var name = $('.vram-name', row).value.trim();
            var amount = $('.vram-amount', row).value.trim();
            if (name || amount) vram[name] = amount ? Number(amount) : '';
        });
        var apu = $('#apuDeviceIds').value.split(/[\s,，]+/).map(function (item) { return item.trim(); }).filter(Boolean);
        var secrets = {};
        [collectSecret('dashboardToken', 'dashboard.token'), collectSecret('githubToken', 'github_token'),
            collectSecret('localPassword', 'local_platforms.password'), collectSecret('nugPassword', 'nug.password')]
            .forEach(function (item) { secrets[item[0]] = item[1]; });
        secrets['local_platforms.url_passwords'] = urlSecrets;

        return {
            config: {
                dashboard: {
                    off_peak_badge: {enabled: $('#offPeakEnabled').checked, ranges: ranges},
                    vibe_coding: {
                        ring: {provider: $('#ringProvider').value, item: $('#ringItem').value},
                        model_bars: {provider: $('#modelBarsProvider').value},
                        balances: balances
                    }
                },
                local_platforms: {
                    enabled: $('#localEnabled').checked,
                    username: $('#localUsername').value,
                    urls: urls
                },
                nug: {
                    enabled: $('#nugEnabled').checked,
                    url: $('#nugUrl').value,
                    username: $('#nugUsername').value
                },
                hardware_overrides: {
                    cpu_model: $('#cpuModel').value,
                    mem_installed_gb: $('#memInstalled').value ? numberValue('#memInstalled', null) : null,
                    mem_name: $('#memName').value,
                    gpu_model: $('#gpuModel').value,
                    gpu_vram_gb: vram,
                    apu_device_ids: apu.length ? apu : null
                },
                logging: {
                    level: $('#logLevel').value,
                    mode: $('#logMode').value,
                    dir: $('#logDir').value,
                    keep_days: numberValue('#keepDays', 0),
                    max_size_mb: numberValue('#maxSize', 1),
                    max_backups: numberValue('#maxBackups', 0),
                    console: $('#logConsole').checked
                },
                theme: $('#theme').value,
                lyric_offset: numberValue('#lyricOffset', 0),
                vibe_active: $('#vibeActive').checked
            },
            secrets: secrets
        };
    }

    async function loadSettings() {
        $('#loadingState').hidden = false;
        $('#errorState').hidden = true;
        $('#settingsApp').hidden = true;
        $('#settingsFooter').hidden = true;
        try {
            var payload = await requestJson('/api/settings');
            render(payload);
            $('#settingsApp').hidden = false;
            $('#settingsFooter').hidden = false;
        } catch (error) {
            $('#errorState').textContent = '无法读取配置：' + error.message;
            $('#errorState').hidden = false;
        } finally {
            $('#loadingState').hidden = true;
        }
    }

    async function saveSettings() {
        if (state.saving) return;
        state.saving = true;
        var button = $('#saveButton');
        button.disabled = true;
        showMessage('正在保存…');
        try {
            var result = await requestJson('/api/settings', {method: 'POST', body: collect()});
            render(result);
            if (result.errors && result.errors.length) {
                showMessage('已保存，但部分运行时模块刷新失败：' + result.errors.join('；'), 'error');
            } else {
                showMessage('已保存，配置已立即生效', 'success');
            }
        } catch (error) {
            var field = error.payload && error.payload.error && error.payload.error.field;
            showMessage((field ? field + '：' : '') + error.message, 'error');
        } finally {
            state.saving = false;
            button.disabled = false;
        }
    }

    function handleSecretClick(target) {
        var revealFor = target.dataset.revealFor;
        var clearFor = target.dataset.clearFor;
        if (revealFor) {
            var input = document.getElementById(revealFor);
            if (!input || !input.dataset.secretPath) return;
            if (input.dataset.revealed === '1') {
                input.type = input.type === 'password' ? 'text' : 'password';
                target.textContent = input.type === 'password' ? '查看' : '隐藏';
                return;
            }
            target.disabled = true;
            requestJson('/api/settings/reveal', {method: 'POST', body: {path: input.dataset.secretPath}})
                .then(function (result) {
                    input.value = result.value || '';
                    input.dataset.secretAction = result.value ? 'set' : 'keep';
                    input.dataset.revealed = '1';
                    input.type = 'text';
                    target.textContent = '隐藏';
                })
                .catch(function (error) { showMessage('查看敏感字段失败：' + error.message, 'error'); })
                .finally(function () { target.disabled = false; });
        }
        if (clearFor) {
            var clearInput = document.getElementById(clearFor);
            if (!clearInput) return;
            clearInput.value = '';
            clearInput.type = 'password';
            clearInput.dataset.secretAction = 'clear';
            clearInput.dataset.revealed = '0';
            clearInput.placeholder = '保存后清空';
            var revealButton = $('[data-reveal-for="' + clearFor + '"]');
            if (revealButton) revealButton.textContent = '查看';
            setDirty(true);
        }
    }

    function addOffPeakRange() {
        var container = $('#offPeakRanges');
        var empty = $('.empty-row', container);
        if (empty) empty.remove();
        var parts = rowParts('off-peak');
        parts.fields.className += ' three';
        addLabeledControl(parts.fields, '开始', 'time', '00:00', 'range-start');
        addLabeledControl(parts.fields, '结束', 'time', '08:00', 'range-end');
        parts.fields.appendChild(el('div', 'row-field row-note', '支持跨午夜'));
        container.appendChild(parts.row);
        setDirty(true);
    }

    function addLocalUrl() {
        var container = $('#localUrlRows');
        var empty = $('.empty-row', container);
        if (empty) empty.remove();
        addLocalUrlRow({url: '', original_url: '', password: {configured: false}}, undefined);
        setDirty(true);
    }

    function addVram() {
        var container = $('#vramRows');
        var empty = $('.empty-row', container);
        if (empty) empty.remove();
        addVramRow('', '');
        setDirty(true);
    }

    function addBalance() {
        var container = $('#balanceRows');
        var empty = $('.empty-row', container);
        if (empty) empty.remove();
        addBalanceRow(null, state.payload.options || {});
        setDirty(true);
    }

    document.addEventListener('click', function (event) {
        var target = event.target.closest ? event.target.closest('button') : event.target;
        if (!target) return;
        if (target.dataset.revealFor || target.dataset.clearFor) {
            event.preventDefault();
            handleSecretClick(target);
            return;
        }
        if (target.dataset.removeRow) {
            var row = target.closest('.repeat-row');
            if (row) row.remove();
            setDirty(true);
        }
    });

    $('#settingsForm').addEventListener('input', function (event) {
        var input = event.target;
        if (input.matches('[data-secret-path]')) {
            if (input.value) {
                input.dataset.secretAction = 'set';
            } else if (input.dataset.revealed === '1' || input.dataset.secretAction === 'clear') {
                input.dataset.secretAction = 'clear';
            } else {
                input.dataset.secretAction = 'keep';
            }
        }
        setDirty(true);
    });
    $('#settingsForm').addEventListener('change', function () { setDirty(true); });
    $('#addOffPeakRange').addEventListener('click', addOffPeakRange);
    $('#addBalance').addEventListener('click', addBalance);
    $('#addLocalUrl').addEventListener('click', addLocalUrl);
    $('#addVram').addEventListener('click', addVram);
    $('#saveButton').addEventListener('click', saveSettings);
    $('#reloadButton').addEventListener('click', function () {
        if (!state.dirty || window.confirm('当前有未保存修改，确定重新加载吗？')) loadSettings();
    });
    window.addEventListener('beforeunload', function (event) {
        if (state.dirty) { event.preventDefault(); event.returnValue = ''; }
    });

    loadSettings();
}());
