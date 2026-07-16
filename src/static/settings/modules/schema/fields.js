import {el, fillSelect} from '../dom.js';
import {createSecretControl} from '../secrets.js';

export function addScalarControl(container, spec, value, row) {
    const field = el('div', row ? 'row-field' : 'field');
    const label = el('label', row ? 'row-label' : '', spec.label || spec.key);
    field.appendChild(label);

    let input;
    const type = spec.type;
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

export function makeProviderField(spec, value, path, handlers) {
    const field = el('div', 'field provider-field');
    field.dataset.fieldKey = spec.key;
    field._providerSpec = spec;
    const type = spec.type;

    if (type === 'boolean') {
        const switchLabel = el('label', 'switch provider-switch');
        const switchInput = document.createElement('input');
        switchInput.type = 'checkbox';
        switchInput.className = 'provider-input';
        switchInput.dataset.fieldKey = spec.key;
        switchInput.checked = !!value;
        switchLabel.append(switchInput, el('span'));
        field.append(switchLabel, el('span', 'provider-switch-label', spec.label || spec.key));
        if (spec.description) field.appendChild(el('div', 'field-help', spec.description));
        return field;
    }
    if (type === 'secret') {
        createSecretControl(field, spec.label || spec.key, value, path);
        return field;
    }
    if (type === 'string_list') {
        field.appendChild(el('label', '', spec.label || spec.key));
        const textarea = document.createElement('textarea');
        textarea.rows = 3;
        textarea.className = 'provider-input';
        textarea.dataset.fieldKey = spec.key;
        textarea.value = Array.isArray(value) ? value.join('\n') : '';
        field.appendChild(textarea);
        if (spec.description) field.appendChild(el('div', 'field-help', spec.description));
        return field;
    }
    if (type === 'key_value_map') {
        handlers.renderMapField(field, spec, value || {});
        return field;
    }
    if (type === 'object_list') {
        handlers.renderObjectListField(field, spec, value || [], path);
        return field;
    }
    addScalarControl(field, spec, value, false);
    if (spec.description) field.appendChild(el('div', 'field-help', spec.description));
    return field;
}
