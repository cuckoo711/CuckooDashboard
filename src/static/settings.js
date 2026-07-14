/* Schema-driven configuration backend. Sensitive values stay out of browser storage. */
(function () {
    'use strict';

    var state = {payload: null, dirty: false, saving: false, providerPanels: []};
    var idSeed = 0;
    var $ = function (selector, root) { return (root || document).querySelector(selector); };
    var $$ = function (selector, root) { return Array.prototype.slice.call((root || document).querySelectorAll(selector)); };

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function nextId(prefix) {
        idSeed += 1;
        return prefix + '_' + idSeed;
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
        if (autoLabel) {
            var auto = document.createElement('option');
            auto.value = '';
            auto.textContent = autoLabel;
            select.appendChild(auto);
        }
        (values || []).forEach(function (item) {
            var value = typeof item === 'object' ? item.value : item;
            var label = typeof item === 'object' ? (item.label || item.value) : item;
            var option = document.createElement('option');
            option.value = value;
            option.textContent = label;
            select.appendChild(option);
        });
        if (current && !(values || []).some(function (item) {
            return (typeof item === 'object' ? item.value : item) === current;
        })) {
            var currentOption = document.createElement('option');
            currentOption.value = current;
            currentOption.textContent = current + '（当前值）';
            select.appendChild(currentOption);
        }
        select.value = current || '';
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

    function setupSecretInput(input, meta, path, options) {
        options = options || {};
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

    function createSecretControl(container, labelText, meta, path, options) {
        options = options || {};
        var field = el('div', options.row ? 'row-field' : 'field');
        var label = el('label', options.row ? 'row-label' : '', labelText);
        var control = el('div', 'secret-control');
        var input = document.createElement('input');
        input.id = nextId('secret');
        setupSecretInput(input, meta, path, options);
        var reveal = el('button', 'small-btn reveal-btn', '查看');
        reveal.type = 'button';
        reveal.dataset.revealFor = input.id;
        var clear = el('button', 'small-btn danger-btn clear-secret-btn', '清空');
        clear.type = 'button';
        clear.dataset.clearFor = input.id;
        control.appendChild(input);
        control.appendChild(reveal);
        control.appendChild(clear);
        field.appendChild(label);
        field.appendChild(control);
        if (!options.row) field.appendChild(el('div', 'field-help'));
        container.appendChild(field);
        return input;
    }

    function addScalarControl(container, spec, value, row) {
        var field = el('div', row ? 'row-field' : 'field');
        var label = el('label', row ? 'row-label' : '', spec.label || spec.key);
        field.appendChild(label);
        var input;
        var type = spec.type;
        if (type === 'select') {
            input = document.createElement('select');
            fillSelect(input, spec.options || [], value || '', spec.allow_empty ? '自动选择' : '');
        } else {
            input = document.createElement('input');
            input.type = type === 'integer' || type === 'number' ? 'number' : type;
            if (input.type === 'number') {
                input.step = type === 'integer' ? '1' : (spec.step || 'any');
                if (spec.min !== undefined) input.min = spec.min;
            }
            if (value !== undefined && value !== null) input.value = value;
        }
        input.className = row ? 'object-input' : 'provider-input';
        input.dataset.fieldKey = spec.key;
        field.appendChild(input);
        container.appendChild(field);
        return input;
    }

    function makeProviderField(spec, value, path) {
        var field = el('div', 'field provider-field');
        field.dataset.fieldKey = spec.key;
        field._providerSpec = spec;
        var type = spec.type;
        if (type === 'boolean') {
            var checkLine = el('label', 'check-line provider-check');
            var checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.className = 'provider-input';
            checkbox.dataset.fieldKey = spec.key;
            checkbox.checked = !!value;
            checkLine.appendChild(checkbox);
            checkLine.appendChild(el('span', '', spec.label || spec.key));
            field.appendChild(checkLine);
            if (spec.description) field.appendChild(el('div', 'field-help', spec.description));
            return field;
        }
        if (type === 'secret') {
            createSecretControl(field, spec.label || spec.key, value, path, {});
            return field;
        }
        if (type === 'string_list') {
            var listLabel = el('label', '', spec.label || spec.key);
            field.appendChild(listLabel);
            var textarea = document.createElement('textarea');
            textarea.rows = 3;
            textarea.className = 'provider-input';
            textarea.dataset.fieldKey = spec.key;
            textarea.value = Array.isArray(value) ? value.join('\n') : '';
            field.appendChild(textarea);
            if (spec.description) field.appendChild(el('div', 'field-help', spec.description));
            return field;
        }
        if (type === 'key_value_map') {
            renderMapField(field, spec, value || {});
            return field;
        }
        if (type === 'object_list') {
            renderObjectListField(field, spec, value || [], path);
            return field;
        }
        addScalarControl(field, spec, value, false);
        if (spec.description) field.appendChild(el('div', 'field-help', spec.description));
        return field;
    }

    function renderMapField(field, spec, value) {
        field.classList.add('provider-map-field');
        field.appendChild(el('label', '', spec.label || spec.key));
        var list = el('div', 'repeat-list provider-map-rows');
        Object.keys(value || {}).forEach(function (key) { addMapRow(list, key, value[key], spec); });
        if (!list.children.length) list.appendChild(el('div', 'empty-row', '未配置映射'));
        field.appendChild(list);
        var add = el('button', 'link-btn', '+ 添加映射');
        add.type = 'button';
        add.dataset.addMap = '1';
        add._mapSpec = spec;
        field.appendChild(add);
    }

    function addMapRow(list, key, value, spec) {
        var empty = $('.empty-row', list);
        if (empty) empty.remove();
        var parts = rowParts('provider-map');
        parts.row.classList.add('provider-map-row');
        parts.fields.className += ' two';
        var keyInput = addScalarControl(parts.fields, {key: 'map_key', label: '名称', type: 'string'}, key, true);
        var valueInput = addScalarControl(parts.fields, {key: 'map_value', label: '数值', type: spec.value_type === 'integer' ? 'integer' : 'number', min: spec.min}, value, true);
        keyInput.classList.add('map-key');
        valueInput.classList.add('map-value');
        list.appendChild(parts.row);
    }

    function renderObjectListField(field, spec, value, path) {
        field.classList.add('provider-object-field');
        field.appendChild(el('label', '', spec.label || spec.key));
        if (spec.description) field.appendChild(el('div', 'field-help', spec.description));
        var list = el('div', 'repeat-list provider-object-rows');
        (Array.isArray(value) ? value : []).forEach(function (item) {
            addObjectRow(list, spec, item || {}, path);
        });
        if (!list.children.length) list.appendChild(el('div', 'empty-row', '未配置列表项'));
        field.appendChild(list);
        var add = el('button', 'link-btn', '+ 添加条目');
        add.type = 'button';
        add.dataset.addObject = '1';
        add._objectSpec = spec;
        add._objectPath = path;
        field.appendChild(add);
    }

    function addObjectRow(list, spec, value, path) {
        var empty = $('.empty-row', list);
        if (empty) empty.remove();
        var parts = rowParts('provider-object');
        parts.row.classList.add('provider-object-row');
        var identityKey = spec.identity_key;
        var original = value['__original_' + identityKey] || value[identityKey] || '';
        parts.row.dataset.originalIdentity = original;
        parts.row.dataset.identityKey = identityKey || '';
        parts.fields.className += ' provider-object-fields';
        (spec.item_fields || []).forEach(function (itemSpec) {
            if (itemSpec.type === 'secret') {
                var input = createSecretControl(
                    parts.fields,
                    itemSpec.label || itemSpec.key,
                    value[itemSpec.key],
                    path,
                    {row: true, field: itemSpec.key, identityKey: identityKey}
                );
                input.dataset.objectListPath = path;
            } else {
                var input = addScalarControl(parts.fields, itemSpec, value[itemSpec.key], true);
                input.classList.add('object-input');
            }
        });
        list.appendChild(parts.row);
    }

    function renderProviderPanels(panels) {
        var container = $('#providerPanels');
        container.replaceChildren();
        state.providerPanels = panels || [];
        (panels || []).forEach(function (panel) {
            var card = el('section', 'provider-card');
            card.dataset.configKey = panel.config_key;
            card._providerPanel = panel;
            var head = el('div', 'provider-card-head');
            var titleWrap = el('div');
            titleWrap.appendChild(el('h3', '', panel.title || panel.provider));
            if (panel.description) titleWrap.appendChild(el('p', 'provider-description', panel.description));
            head.appendChild(titleWrap);
            var status = panel.status || {};
            var badge = el('span', 'provider-status ' + (status.status || 'unknown'), status.status || 'unknown');
            head.appendChild(badge);
            card.appendChild(head);
            if (panel.status_only_auth) {
                card.appendChild(el('div', 'provider-auth-note', '认证信息由独立登录流程管理，敏感凭据不会在此页面展示。'));
            }
            var fields = el('div', 'provider-field-grid');
            (panel.fields || []).forEach(function (spec) {
                var path = 'providers.' + panel.config_key + '.' + spec.key;
                fields.appendChild(makeProviderField(spec, (panel.values || {})[spec.key], path));
            });
            card.appendChild(fields);
            container.appendChild(card);
        });
        if (!(panels || []).length) container.appendChild(el('div', 'empty-row', '当前没有声明配置 Schema 的 Provider'));
    }

    function renderOffPeakRanges(ranges) {
        var container = $('#offPeakRanges');
        container.replaceChildren();
        (ranges || []).forEach(function (item) {
            var parts = rowParts('off-peak');
            parts.fields.className += ' three';
            addScalarControl(parts.fields, {key: 'start', label: '开始', type: 'time'}, item.start || '00:00', true).classList.add('range-start');
            addScalarControl(parts.fields, {key: 'end', label: '结束', type: 'time'}, item.end || '08:00', true).classList.add('range-end');
            parts.fields.appendChild(el('div', 'row-field row-note', '支持跨午夜'));
            container.appendChild(parts.row);
        });
        if (!container.children.length) container.appendChild(el('div', 'empty-row', '未配置闲时区间'));
    }

    function addOffPeakRange() {
        var container = $('#offPeakRanges');
        var empty = $('.empty-row', container);
        if (empty) empty.remove();
        var parts = rowParts('off-peak');
        parts.fields.className += ' three';
        addScalarControl(parts.fields, {key: 'start', label: '开始', type: 'time'}, '00:00', true).classList.add('range-start');
        addScalarControl(parts.fields, {key: 'end', label: '结束', type: 'time'}, '08:00', true).classList.add('range-end');
        parts.fields.appendChild(el('div', 'row-field row-note', '支持跨午夜'));
        container.appendChild(parts.row);
        setDirty(true);
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
        var provider = addScalarControl(parts.fields, {key: 'provider', label: 'Provider', type: 'select', options: options.balance_providers || [], allow_empty: true}, entry.provider, true);
        provider.classList.add('balance-provider');
        addScalarControl(parts.fields, {key: 'name', label: '显示名称', type: 'string'}, entry.name || '', true).classList.add('balance-name');
        addScalarControl(parts.fields, {key: 'color', label: '颜色', type: 'color'}, entry.color || '#888888', true).classList.add('balance-color');
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

    function addBalance() {
        var container = $('#balanceRows');
        var empty = $('.empty-row', container);
        if (empty) empty.remove();
        addBalanceRow(null, state.payload.options || {});
        setDirty(true);
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
        addScalarControl(parts.fields, {key: 'name', label: 'GPU 名称', type: 'string'}, name || '', true).classList.add('vram-name');
        var amountInput = addScalarControl(parts.fields, {key: 'amount', label: '显存（GB）', type: 'number', min: 0.01, step: 0.01}, amount === undefined ? '' : amount, true);
        amountInput.classList.add('vram-amount');
        container.appendChild(parts.row);
    }

    function addVram() {
        var container = $('#vramRows');
        var empty = $('.empty-row', container);
        if (empty) empty.remove();
        addVramRow('', '');
        setDirty(true);
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

    function setupGlobalSecret(id, meta, path) {
        var input = document.getElementById(id);
        if (!input) return;
        setupSecretInput(input, meta, path, {});
        input.disabled = false;
    }

    function render(payload) {
        state.payload = payload;
        var cfg = payload.config || {};
        var dashboard = cfg.dashboard || {};
        var offPeak = dashboard.off_peak_badge || {};
        var vibe = dashboard.vibe_coding || {};
        var hardware = cfg.hardware_overrides || {};
        var logging = cfg.logging || {};

        setupGlobalSecret('dashboardToken', dashboard.token, 'dashboard.token');
        setupGlobalSecret('githubToken', cfg.github_token, 'github_token');
        setChecked('offPeakEnabled', offPeak.enabled !== false);
        renderOffPeakRanges(offPeak.ranges || []);
        fillSelect($('#ringProvider'), payload.options.ring_providers || [], (vibe.ring || {}).provider || '', '自动选择');
        setValue('ringItem', (vibe.ring || {}).item || '');
        fillSelect($('#modelBarsProvider'), payload.options.model_bar_providers || [], (vibe.model_bars || {}).provider || '', '自动选择');
        renderBalances(vibe.balances || [], payload.options || {});
        renderProviderPanels(payload.providers || []);

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
        renderThemeOptions(payload.options || {}, cfg.theme || 'dark');
        setValue('lyricOffset', cfg.lyric_offset === undefined ? 0 : cfg.lyric_offset);
        setChecked('vibeActive', cfg.vibe_active);
        setDirty(false);
    }

    function numberValue(selector, fallback) {
        var node = $(selector);
        var value = node ? node.value.trim() : '';
        if (!value) return fallback;
        var number = Number(value);
        return Number.isFinite(number) ? number : fallback;
    }

    function secretAction(input) {
        var action = input && input.dataset.secretAction || 'keep';
        var result = {action: action};
        if (action === 'set') result.value = input.value;
        return result;
    }

    function collectGlobalSecrets() {
        var result = {};
        result['dashboard.token'] = secretAction($('#dashboardToken'));
        result.github_token = secretAction($('#githubToken'));
        return result;
    }

    function collectGlobalConfig() {
        var ranges = $$('#offPeakRanges .repeat-row').map(function (row) {
            return {start: $('.range-start', row).value, end: $('.range-end', row).value};
        });
        var balances = $$('#balanceRows .repeat-row').map(function (row) {
            return {
                provider: $('.balance-provider', row).value,
                name: $('.balance-name', row).value,
                color: $('.balance-color', row).value,
                enabled: $('.balance-enabled', row).checked
            };
        });
        var vram = {};
        $$('#vramRows .repeat-row').forEach(function (row) {
            var name = $('.vram-name', row).value.trim();
            var amount = $('.vram-amount', row).value.trim();
            if (name || amount) vram[name] = amount ? Number(amount) : '';
        });
        var apu = $('#apuDeviceIds').value.split(/[\s,，]+/).map(function (item) { return item.trim(); }).filter(Boolean);
        return {
            dashboard: {
                off_peak_badge: {enabled: $('#offPeakEnabled').checked, ranges: ranges},
                vibe_coding: {
                    ring: {provider: $('#ringProvider').value, item: $('#ringItem').value},
                    model_bars: {provider: $('#modelBarsProvider').value},
                    balances: balances
                }
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
        };
    }

    function collectProviderField(field, spec, path) {
        if (spec.type === 'secret') {
            return {secret: true, update: secretAction($('.secret-control input', field))};
        }
        if (spec.type === 'boolean') return {value: $('.provider-input', field).checked};
        if (spec.type === 'string_list') {
            return {value: $('.provider-input', field).value.split(/[\s,，]+/).map(function (item) { return item.trim(); }).filter(Boolean)};
        }
        if (spec.type === 'key_value_map') {
            var map = {};
            $$('.provider-map-row', field).forEach(function (row) {
                var key = $('.map-key', row).value.trim();
                var value = $('.map-value', row).value.trim();
                if (key || value) map[key] = value ? Number(value) : '';
            });
            return {value: map};
        }
        if (spec.type === 'object_list') {
            var rows = [];
            var updates = [];
            $$('.provider-object-row', field).forEach(function (row) {
                var identityKey = spec.identity_key;
                var identityInput = $$('.object-input', row).find(function (input) { return input.dataset.fieldKey === identityKey; });
                var identity = identityInput ? identityInput.value.trim() : '';
                if (!identity) return;
                var rowValue = {};
                var rowSecrets = {};
                (spec.item_fields || []).forEach(function (itemSpec) {
                    if (itemSpec.type === 'secret') {
                        var input = $$('.secret-control input', row).find(function (node) { return node.dataset.objectField === itemSpec.key; });
                        rowSecrets[itemSpec.key] = secretAction(input);
                    } else {
                        var input = $$('.object-input', row).find(function (node) { return node.dataset.fieldKey === itemSpec.key; });
                        if (!input) return;
                        if (itemSpec.type === 'integer' || itemSpec.type === 'number') rowValue[itemSpec.key] = input.value ? Number(input.value) : '';
                        else rowValue[itemSpec.key] = input.value;
                    }
                });
                rowValue[identityKey] = identity;
                rowValue['__original_' + identityKey] = row.dataset.originalIdentity || identity;
                rows.push(rowValue);
                updates.push({
                    original_identity: row.dataset.originalIdentity || identity,
                    identity: identity,
                    fields: rowSecrets
                });
            });
            return {value: rows, secretUpdates: updates};
        }
        var input = $('.provider-input', field);
        if (!input) return {value: ''};
        if (spec.type === 'integer' || spec.type === 'number') return {value: input.value ? Number(input.value) : ''};
        return {value: input.value};
    }

    function collectProviders() {
        var values = {};
        var secrets = {};
        $$('#providerPanels .provider-card').forEach(function (card) {
            var configKey = card.dataset.configKey;
            var panel = card._providerPanel || {};
            var providerValue = {};
            (panel.fields || []).forEach(function (spec) {
                var field = $$('.provider-field', card).find(function (node) { return node.dataset.fieldKey === spec.key; });
                if (!field) return;
                var path = 'providers.' + configKey + '.' + spec.key;
                var collected = collectProviderField(field, spec, path);
                if (collected.secret) secrets[path] = collected.update;
                else {
                    providerValue[spec.key] = collected.value;
                    if (collected.secretUpdates) secrets[path] = collected.secretUpdates;
                }
            });
            values[configKey] = providerValue;
        });
        return {values: values, secrets: secrets};
    }

    function collect() {
        var providers = collectProviders();
        var secrets = collectGlobalSecrets();
        Object.keys(providers.secrets).forEach(function (key) { secrets[key] = providers.secrets[key]; });
        var config = collectGlobalConfig();
        config.providers = providers.values;
        return {config: config, secrets: secrets};
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
            if (result.errors && result.errors.length) showMessage('已保存，但部分运行时模块刷新失败：' + result.errors.join('；'), 'error');
            else showMessage('已保存，配置已立即生效', 'success');
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
        var input = document.getElementById(revealFor || clearFor);
        if (!input) return;
        if (revealFor) {
            if (!input.dataset.secretPath) return;
            if (input.dataset.revealed === '1') {
                input.type = input.type === 'password' ? 'text' : 'password';
                target.textContent = input.type === 'password' ? '查看' : '隐藏';
                return;
            }
            var body = {path: input.dataset.secretPath};
            var row = input.closest('.provider-object-row');
            if (row) {
                var identityKey = input.dataset.identityKey;
                var identityInput = $$('.object-input', row).find(function (node) { return node.dataset.fieldKey === identityKey; });
                body.identity = identityInput && identityInput.value.trim();
                body.field = input.dataset.objectField;
                if (!body.identity) {
                    showMessage('请先填写列表项的标识字段', 'error');
                    return;
                }
            }
            target.disabled = true;
            requestJson('/api/settings/reveal', {method: 'POST', body: body})
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
            input.value = '';
            input.type = 'password';
            input.dataset.secretAction = 'clear';
            input.dataset.revealed = '0';
            input.placeholder = '保存后清空';
            var revealButton = $('[data-reveal-for="' + clearFor + '"]');
            if (revealButton) revealButton.textContent = '查看';
            setDirty(true);
        }
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
            return;
        }
        if (target.dataset.addObject) {
            var objectField = target.closest('.provider-field');
            var list = $('.provider-object-rows', objectField);
            addObjectRow(list, target._objectSpec, {}, target._objectPath);
            setDirty(true);
            return;
        }
        if (target.dataset.addMap) {
            var mapField = target.closest('.provider-field');
            var mapList = $('.provider-map-rows', mapField);
            addMapRow(mapList, '', '', target._mapSpec);
            setDirty(true);
        }
    });

    $('#settingsForm').addEventListener('input', function (event) {
        var input = event.target;
        if (input.matches('[data-secret-path]')) {
            if (input.value) input.dataset.secretAction = 'set';
            else if (input.dataset.revealed === '1' || input.dataset.secretAction === 'clear') input.dataset.secretAction = 'clear';
            else input.dataset.secretAction = 'keep';
        }
        setDirty(true);
    });
    $('#settingsForm').addEventListener('change', function () { setDirty(true); });
    $('#addOffPeakRange').addEventListener('click', addOffPeakRange);
    $('#addBalance').addEventListener('click', addBalance);
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
