import {$, el, rowParts} from '../dom.js';
import {state} from '../state.js';
import {createSecretControl} from '../secrets.js';
import {addScalarControl, makeProviderField} from './fields.js';

export function addMapRow(list, key, value, spec) {
    const empty = $('.empty-row', list);
    if (empty) empty.remove();
    const parts = rowParts('provider-map');
    parts.row.classList.add('provider-map-row');
    parts.fields.className += ' two';
    const keyInput = addScalarControl(parts.fields, {key: 'map_key', label: '名称', type: 'string'}, key, true);
    const valueInput = addScalarControl(parts.fields, {
        key: 'map_value',
        label: '数值',
        type: spec.value_type === 'integer' ? 'integer' : 'number',
        min: spec.min,
    }, value, true);
    keyInput.classList.add('map-key');
    valueInput.classList.add('map-value');
    list.appendChild(parts.row);
}

export function renderMapField(field, spec, value) {
    field.classList.add('provider-map-field');
    field.appendChild(el('label', '', spec.label || spec.key));
    const list = el('div', 'repeat-list provider-map-rows');
    Object.keys(value || {}).forEach((key) => addMapRow(list, key, value[key], spec));
    if (!list.children.length) list.appendChild(el('div', 'empty-row', '未配置映射'));
    field.appendChild(list);
    const add = el('button', 'link-btn', '+ 添加映射');
    add.type = 'button';
    add.dataset.addMap = '1';
    add._mapSpec = spec;
    field.appendChild(add);
}

export function addObjectRow(list, spec, value, path) {
    const empty = $('.empty-row', list);
    if (empty) empty.remove();
    const parts = rowParts('provider-object');
    parts.row.classList.add('provider-object-row');
    const identityKey = spec.identity_key;
    const original = value[`__original_${identityKey}`] || value[identityKey] || '';
    parts.row.dataset.originalIdentity = original;
    parts.row.dataset.identityKey = identityKey || '';
    parts.fields.className += ' provider-object-fields';

    (spec.item_fields || []).forEach((itemSpec) => {
        if (itemSpec.type === 'secret') {
            const input = createSecretControl(
                parts.fields,
                itemSpec.label || itemSpec.key,
                value[itemSpec.key],
                path,
                {row: true, field: itemSpec.key, identityKey},
            );
            input.dataset.objectListPath = path;
        } else {
            const input = addScalarControl(parts.fields, itemSpec, value[itemSpec.key], true);
            input.classList.add('object-input');
        }
    });
    list.appendChild(parts.row);
}

export function renderObjectListField(field, spec, value, path) {
    field.classList.add('provider-object-field');
    field.appendChild(el('label', '', spec.label || spec.key));
    if (spec.description) field.appendChild(el('div', 'field-help', spec.description));
    const list = el('div', 'repeat-list provider-object-rows');
    (Array.isArray(value) ? value : []).forEach((item) => addObjectRow(list, spec, item || {}, path));
    if (!list.children.length) list.appendChild(el('div', 'empty-row', '未配置列表项'));
    field.appendChild(list);
    const add = el('button', 'link-btn', '+ 添加条目');
    add.type = 'button';
    add.dataset.addObject = '1';
    add._objectSpec = spec;
    add._objectPath = path;
    field.appendChild(add);
}

const fieldHandlers = {renderMapField, renderObjectListField};

export function renderProviderPanels(panels) {
    const container = $('#providerPanels');
    container.replaceChildren();
    state.providerPanels = panels || [];

    (panels || []).forEach((panel) => {
        const card = el('section', 'provider-card');
        card.dataset.configKey = panel.config_key;
        card._providerPanel = panel;
        const head = el('div', 'provider-card-head');
        const titleWrap = el('div');
        titleWrap.appendChild(el('h3', '', panel.title || panel.provider));
        if (panel.description) titleWrap.appendChild(el('p', 'provider-description', panel.description));
        head.appendChild(titleWrap);
        const status = panel.status || {};
        head.appendChild(el('span', `provider-status ${status.status || 'unknown'}`, status.status || 'unknown'));
        card.appendChild(head);

        const auth = panel.auth || {};
        const authDescriptor = panel.auth_descriptor || {};
        if (panel.status_only_auth || authDescriptor.auth_path) {
            const authNote = el('div', 'provider-auth-note');
            const authText = `${auth.status || 'unknown'}${auth.active_account_label ? ` · ${auth.active_account_label}` : ''}`;
            authNote.appendChild(el('span', '', `认证状态：${authText}`));
            if (auth.last_error) authNote.appendChild(el('span', 'provider-auth-error', ` · ${auth.last_error}`));
            if (authDescriptor.auth_path) {
                const authButton = el('button', 'small-btn', '管理认证/账户');
                authButton.type = 'button';
                authButton.addEventListener('click', () => window.open(authDescriptor.auth_path, '_blank', 'noopener'));
                authNote.appendChild(authButton);
            }
            card.appendChild(authNote);
        }

        const fields = el('div', 'provider-field-grid');
        (panel.fields || []).forEach((spec) => {
            const path = `providers.${panel.config_key}.${spec.key}`;
            fields.appendChild(makeProviderField(spec, (panel.values || {})[spec.key], path, fieldHandlers));
        });
        card.appendChild(fields);
        container.appendChild(card);
    });

    if (!(panels || []).length) {
        container.appendChild(el('div', 'empty-row', '当前没有声明配置 Schema 的 Provider'));
    }
}
