import {$, $$} from './dom.js';
import {state} from './state.js';
import {secretAction} from './secrets.js';

function numberValue(selector, fallback) {
    const node = $(selector);
    const value = node ? node.value.trim() : '';
    if (!value) return fallback;
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
}

function collectGlobalSecrets() {
    return {
        'dashboard.token': secretAction($('#dashboardToken')),
        github_token: secretAction($('#githubToken')),
    };
}

function collectGlobalConfig() {
    const ranges = $$('#offPeakRanges .repeat-row').map((row) => ({
        start: $('.range-start', row).value,
        end: $('.range-end', row).value,
    }));
    const balances = $$('#balanceRows .repeat-row').map((row) => ({
        provider: $('.balance-provider', row).value,
        name: $('.balance-name', row).value,
        color: $('.balance-color', row).value,
        enabled: $('.balance-enabled', row).checked,
    }));
    const vram = {};
    $$('#vramRows .repeat-row').forEach((row) => {
        const name = $('.vram-name', row).value.trim();
        const amount = $('.vram-amount', row).value.trim();
        if (name || amount) vram[name] = amount ? Number(amount) : '';
    });
    const apu = $('#apuDeviceIds').value
        .split(/[\s,，]+/)
        .map((item) => item.trim())
        .filter(Boolean);

    return {
        dashboard: {
            off_peak_badge: {enabled: $('#offPeakEnabled').checked, ranges},
            vibe_coding: {
                ring: {provider: $('#ringProvider').value, item: $('#ringItem').value},
                model_bars: {provider: $('#modelBarsProvider').value},
                balances,
            },
            font: {enabled: $('#fontEnabled').checked, filename: $('#fontFilename').value},
            font_size: {
                title_text: $('#fontSizeTitleText').value || 'Cuckoo Dashboard',
                title: numberValue('#fontSizeTitle', 16),
                clock: numberValue('#fontSizeClock', 22),
                date: numberValue('#fontSizeDate', 15),
                card_head: numberValue('#fontSizeCardHead', 10),
                card_foot: numberValue('#fontSizeCardFoot', 10),
                card_body: numberValue('#fontSizeCardBody', 10),
                offset: numberValue('#fontSizeOffset', 0),
            },
        },
        hardware_overrides: {
            cpu_model: $('#cpuModel').value,
            mem_installed_gb: $('#memInstalled').value ? numberValue('#memInstalled', null) : null,
            mem_name: $('#memName').value,
            gpu_model: $('#gpuModel').value,
            gpu_vram_gb: vram,
            apu_device_ids: apu.length ? apu : null,
        },
        logging: {
            level: $('#logLevel').value,
            mode: $('#logMode').value,
            dir: $('#logDir').value,
            keep_days: numberValue('#keepDays', 0),
            max_size_mb: numberValue('#maxSize', 1),
            max_backups: numberValue('#maxBackups', 0),
            console: $('#logConsole').checked,
        },
        theme: $('#theme').value,
        lyric_offset: numberValue('#lyricOffset', 0),
        vibe_active: $('#vibeActive').checked,
        music: {
            spectrum_enabled: $('#musicSpectrumEnabled') ? $('#musicSpectrumEnabled').checked : true,
            capture_device: $('#musicCaptureDevice') ? ($('#musicCaptureDevice').value || 'auto') : 'auto',
            spectrum_offset_ms: numberValue('#musicSpectrumOffset', 40),
            bins: numberValue('#musicBins', 48),
            render_fps: numberValue('#musicRenderFps', 0),
            render_bars: numberValue('#musicRenderBars', 0),
        },
    };
}

function collectProviderField(field, spec) {
    if (spec.type === 'secret') {
        return {secret: true, update: secretAction($('.secret-control input', field))};
    }
    if (spec.type === 'boolean') return {value: $('.provider-input', field).checked};
    if (spec.type === 'string_list') {
        return {
            value: $('.provider-input', field).value
                .split(/[\s,，]+/)
                .map((item) => item.trim())
                .filter(Boolean),
        };
    }
    if (spec.type === 'key_value_map') {
        const map = {};
        $$('.provider-map-row', field).forEach((row) => {
            const key = $('.map-key', row).value.trim();
            const value = $('.map-value', row).value.trim();
            if (key || value) map[key] = value ? Number(value) : '';
        });
        return {value: map};
    }
    if (spec.type === 'object_list') {
        const rows = [];
        const updates = [];
        $$('.provider-object-row', field).forEach((row) => {
            const identityKey = spec.identity_key;
            const identityInput = $$('.object-input', row).find((input) => input.dataset.fieldKey === identityKey);
            const identity = identityInput ? identityInput.value.trim() : '';
            if (!identity) return;
            const rowValue = {};
            const rowSecrets = {};
            (spec.item_fields || []).forEach((itemSpec) => {
                if (itemSpec.type === 'secret') {
                    const input = $$('.secret-control input', row).find((node) => node.dataset.objectField === itemSpec.key);
                    rowSecrets[itemSpec.key] = secretAction(input);
                } else {
                    const input = $$('.object-input', row).find((node) => node.dataset.fieldKey === itemSpec.key);
                    if (!input) return;
                    rowValue[itemSpec.key] = itemSpec.type === 'integer' || itemSpec.type === 'number'
                        ? (input.value ? Number(input.value) : '')
                        : input.value;
                }
            });
            rowValue[identityKey] = identity;
            rowValue[`__original_${identityKey}`] = row.dataset.originalIdentity || identity;
            rows.push(rowValue);
            updates.push({
                original_identity: row.dataset.originalIdentity || identity,
                identity,
                fields: rowSecrets,
            });
        });
        return {value: rows, secretUpdates: updates};
    }
    const input = $('.provider-input', field);
    if (!input) return {value: ''};
    if (spec.type === 'integer' || spec.type === 'number') {
        return {value: input.value ? Number(input.value) : ''};
    }
    return {value: input.value};
}

function collectProviders() {
    const values = {};
    const secrets = {};
    $$('#providerPanels .provider-card').forEach((card) => {
        const configKey = card.dataset.configKey;
        const panel = card._providerPanel || {};
        const providerValue = {};
        (panel.fields || []).forEach((spec) => {
            const field = $$('.provider-field', card).find((node) => node.dataset.fieldKey === spec.key);
            if (!field) return;
            const path = `providers.${configKey}.${spec.key}`;
            const collected = collectProviderField(field, spec);
            if (collected.secret) secrets[path] = collected.update;
            else {
                providerValue[spec.key] = collected.value;
                if (collected.secretUpdates) secrets[path] = collected.secretUpdates;
            }
        });
        values[configKey] = providerValue;
    });
    return {values, secrets};
}

export function collect() {
    const providers = collectProviders();
    const secrets = collectGlobalSecrets();
    Object.assign(secrets, providers.secrets);
    const config = collectGlobalConfig();
    config.providers = providers.values;
    const result = {config, secrets};
    if (state.credentialRevision !== null) result.credential_revision = state.credentialRevision;
    return result;
}
