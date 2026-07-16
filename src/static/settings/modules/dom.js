let idSeed = 0;

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

export function escHtml(value) {
    const node = document.createElement('div');
    node.appendChild(document.createTextNode(value || ''));
    return node.innerHTML;
}

export function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

export function nextId(prefix) {
    idSeed += 1;
    return `${prefix}_${idSeed}`;
}

export function setValue(id, value) {
    const node = document.getElementById(id);
    if (node) node.value = value === null || value === undefined ? '' : String(value);
}

export function setChecked(id, value) {
    const node = document.getElementById(id);
    if (node) node.checked = !!value;
}

export function fillSelect(select, values, current, autoLabel) {
    select.replaceChildren();
    if (autoLabel) {
        const auto = document.createElement('option');
        auto.value = '';
        auto.textContent = autoLabel;
        select.appendChild(auto);
    }
    (values || []).forEach((item) => {
        const value = typeof item === 'object' ? item.value : item;
        const label = typeof item === 'object' ? (item.label || item.value) : item;
        const option = document.createElement('option');
        option.value = value;
        option.textContent = label;
        select.appendChild(option);
    });
    if (current && !(values || []).some((item) => (typeof item === 'object' ? item.value : item) === current)) {
        const currentOption = document.createElement('option');
        currentOption.value = current;
        currentOption.textContent = `${current}（当前值）`;
        select.appendChild(currentOption);
    }
    select.value = current || '';
}

const REMOVE_ICON = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>';

export function rowParts(kind) {
    const row = el('div', 'repeat-row');
    row.dataset.rowKind = kind;
    const fields = el('div', 'row-fields');
    const remove = el('button', 'remove-btn');
    remove.type = 'button';
    remove.innerHTML = REMOVE_ICON;
    remove.title = '移除';
    remove.dataset.removeRow = '1';
    fields.appendChild(remove);
    row.appendChild(fields);
    return {row, fields};
}
