import {requestJson} from './api.js';
import {$, escHtml} from './dom.js';
import {constrain, firstFit, isPlacementValid, normalizeGrid, normalizeRect, validate} from './grid-layout.js';
import {setWorkspaceDirty, state} from './state.js';

const BUILTIN_LAYOUT_DEFAULTS = Object.freeze({
    'builtin.dashboard.system-info': {defaultSize: {w: 6, h: 5}, constraints: {min_width: 4, min_height: 4, max_width: 16, max_height: 15}},
    'builtin.dashboard.network': {defaultSize: {w: 2, h: 3}, constraints: {min_width: 2, min_height: 2, max_width: 16, max_height: 15}},
    'builtin.dashboard.uptime': {defaultSize: {w: 2, h: 2}, constraints: {min_width: 2, min_height: 2, max_width: 16, max_height: 15}},
    'builtin.dashboard.disks': {defaultSize: {w: 8, h: 4}, constraints: {min_width: 4, min_height: 3, max_width: 16, max_height: 15}},
    'builtin.dashboard.vibe': {defaultSize: {w: 8, h: 9}, constraints: {min_width: 6, min_height: 6, max_width: 16, max_height: 15}},
    'builtin.dashboard.player': {defaultSize: {w: 8, h: 6}, constraints: {min_width: 6, min_height: 4, max_width: 16, max_height: 15}},
    'builtin.dashboard.github': {defaultSize: {w: 8, h: 6}, constraints: {min_width: 6, min_height: 4, max_width: 16, max_height: 15}},
});

const workspaceState = {
    grid: {columns: 16, rows: 15},
    catalog: [],
    workspaces: [],
    selectedId: null,
    baseline: null,
    draft: null,
    conflict: false,
    available: true,
    onWorkspacesChange: null,
    interaction: null,
};
let eventsBound = false;
let collectionRequestSequence = 0;
let workspaceRequestSequence = 0;

function clone(value) {
    return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function asArray(value) {
    if (Array.isArray(value)) return value;
    if (!value || typeof value !== 'object') return [];
    return Object.entries(value).map(([id, item]) => ({id, ...(item || {})}));
}

function workspaceId(value) {
    return String(value?.id ?? value?.workspace_id ?? '');
}

function workspaceName(value) {
    return String(value?.name || value?.title || workspaceId(value) || '未命名工作区');
}

function normalizeCatalogItem(item) {
    const type = String(item?.type || item?.id || item?.widget_type || '');
    const fallback = BUILTIN_LAYOUT_DEFAULTS[type] || {defaultSize: {w: 4, h: 3}, constraints: {}};
    const defaultSize = item?.default_size || item?.defaultSize || item?.default_layout || item?.layout || fallback.defaultSize;
    const constraints = item?.constraints || item?.size_constraints || fallback.constraints;
    return {
        ...item,
        type,
        title: String(item?.title || item?.name || type),
        description: String(item?.description || ''),
        owner: String(item?.owner || ''),
        singleInstance: !!(item?.single_instance ?? item?.singleInstance),
        defaultSize: {
            w: Number(defaultSize.w ?? defaultSize.width ?? item?.default_w ?? fallback.defaultSize.w),
            h: Number(defaultSize.h ?? defaultSize.height ?? item?.default_h ?? fallback.defaultSize.h),
        },
        constraints,
    };
}

function notifyWorkspaceList() {
    if (!workspaceState.onWorkspacesChange) return;
    workspaceState.onWorkspacesChange(workspaceState.workspaces.map((workspace) => ({
        id: workspaceId(workspace),
        name: workspaceName(workspace),
        required: !!workspace.required,
    })).filter((workspace) => workspace.id));
}

function setMessage(text, kind = '') {
    const node = $('#workspaceMessage');
    if (!node) return;
    node.className = `workspace-message ${kind}`;
    node.textContent = text;
}

function setConflict(conflict) {
    workspaceState.conflict = !!conflict;
    const badge = $('#workspaceConflictBadge');
    if (badge) badge.hidden = !workspaceState.conflict;
}

function updateControls() {
    const hasDraft = !!workspaceState.draft;
    const saving = state.workspaceSaving;
    const required = !!workspaceState.draft?.required;
    const save = $('#workspaceSaveButton');
    const discard = $('#workspaceDiscardButton');
    const duplicate = $('#workspaceDuplicateButton');
    const remove = $('#workspaceDeleteButton');
    const name = $('#workspaceName');
    if (save) save.disabled = !hasDraft || !state.workspaceDirty || saving || !workspaceState.available;
    if (discard) discard.disabled = !hasDraft || !state.workspaceDirty || saving;
    if (duplicate) duplicate.disabled = !hasDraft || saving || !workspaceState.available;
    if (remove) {
        remove.disabled = !hasDraft || required || saving || !workspaceState.available;
        remove.title = required ? '必需工作区不能删除' : '删除当前工作区';
    }
    if (name) name.disabled = !hasDraft || saving;
}

function markDirty(dirty = true) {
    setWorkspaceDirty(dirty);
    updateControls();
    renderWorkspaceList();
}

function catalogConstraints() {
    return Object.fromEntries(workspaceState.catalog.map((item) => [item.type, item.constraints]));
}

function normalizedWidget(widget) {
    return {...clone(widget), ...normalizeRect(widget)};
}

function normalizeWorkspace(payload) {
    const source = payload?.workspace || payload?.data || payload || {};
    const grid = normalizeGrid(source.grid || workspaceState.grid);
    return {
        ...clone(source),
        id: workspaceId(source),
        name: workspaceName(source),
        revision: Number(source.revision ?? source.version ?? 0),
        grid,
        widgets: asArray(source.widgets).map(normalizedWidget),
    };
}

function serializeWidget(widget) {
    const rect = normalizeRect(widget);
    const definition = workspaceState.catalog.find((item) => item.type === widget.type);
    return {
        id: widget.id,
        type: widget.type,
        owner: widget.owner || definition?.owner || null,
        slot: widget.slot || 'main',
        layout: {x: rect.x, y: rect.y, width: rect.w, height: rect.h},
        constraints: clone(widget.constraints || definition?.constraints || {}),
    };
}

function workspacePayload() {
    return {
        revision: workspaceState.draft.revision,
        name: workspaceState.draft.name.trim(),
        grid: normalizeGrid(workspaceState.draft.grid),
        widgets: workspaceState.draft.widgets.map(serializeWidget),
    };
}

function renderWorkspaceList() {
    const container = $('#workspaceList');
    if (!container) return;
    if (!workspaceState.available) {
        container.innerHTML = '<div class="empty-row">工作区 API 尚未接入</div>';
        return;
    }
    if (!workspaceState.workspaces.length) {
        container.innerHTML = '<div class="empty-row">暂无工作区</div>';
        return;
    }
    container.innerHTML = workspaceState.workspaces.map((workspace) => {
        const id = workspaceId(workspace);
        const active = id === workspaceState.selectedId;
        const dirty = active && state.workspaceDirty ? '<span class="workspace-list-dirty">未保存</span>' : '';
        const required = workspace.required ? '<span class="workspace-list-required">内置</span>' : '';
        return `<button type="button" class="workspace-list-item${active ? ' is-active' : ''}" data-workspace-id="${escHtml(id)}" ${state.workspaceSaving ? 'disabled' : ''}>`
            + `<span><strong>${escHtml(workspaceName(workspace))}</strong><small>${escHtml(id)}</small></span>${dirty || required}</button>`;
    }).join('');
}

function widgetTitle(widget) {
    const definition = workspaceState.catalog.find((item) => item.type === widget.type);
    return definition?.title || widget.title || widget.type || widget.id;
}

function placeCard(card, rect, grid) {
    card.style.left = `calc(${rect.x / grid.columns * 100}% + 3px)`;
    card.style.top = `calc(${rect.y / grid.rows * 100}% + 3px)`;
    card.style.width = `calc(${rect.w / grid.columns * 100}% - 6px)`;
    card.style.height = `calc(${rect.h / grid.rows * 100}% - 6px)`;
}

function renderGrid() {
    const preview = $('#workspaceGridPreview');
    if (!preview) return;
    preview.replaceChildren();
    if (!workspaceState.draft) {
        preview.innerHTML = '<div class="workspace-grid-empty">选择工作区后编辑布局</div>';
        return;
    }
    const grid = normalizeGrid(workspaceState.draft.grid);
    preview.style.setProperty('--workspace-columns', grid.columns);
    preview.style.setProperty('--workspace-rows', grid.rows);
    workspaceState.draft.widgets.forEach((widget) => {
        const card = document.createElement('article');
        const unavailable = widget.available === false || !workspaceState.catalog.some((item) => item.type === widget.type);
        card.className = `workspace-widget-card${unavailable ? ' is-unavailable' : ''}`;
        card.dataset.widgetId = widget.id;
        card.tabIndex = 0;
        card.innerHTML = `<div class="workspace-widget-head"><strong>${escHtml(widgetTitle(widget))}</strong>`
            + `<button type="button" class="workspace-widget-remove" data-remove-widget="${escHtml(widget.id)}" title="移除组件" ${state.workspaceSaving ? 'disabled' : ''}>×</button></div>`
            + `<span class="workspace-widget-type">${escHtml(widget.type)}</span>`
            + (unavailable ? `<span class="workspace-widget-unavailable">不可用 · ${escHtml(widget.unavailable_reason || '扩展缺失')}</span>` : '')
            + '<span class="workspace-resize-handle" title="拖动缩放"></span>';
        placeCard(card, normalizeRect(widget), grid);
        preview.appendChild(card);
    });
    if (!workspaceState.draft.widgets.length) {
        preview.innerHTML = '<div class="workspace-grid-empty">从右侧组件目录添加组件</div>';
    }
}

function renderCatalog() {
    const container = $('#workspaceCatalog');
    if (!container) return;
    if (!workspaceState.catalog.length) {
        container.innerHTML = '<div class="empty-row">暂无可用组件</div>';
        return;
    }
    const widgets = workspaceState.draft?.widgets || [];
    container.innerHTML = workspaceState.catalog.map((item) => {
        const alreadyUsed = item.singleInstance && widgets.some((widget) => widget.type === item.type);
        return `<div class="workspace-catalog-item"><div><strong>${escHtml(item.title)}</strong>`
            + `<small>${escHtml(item.description || item.type)}</small></div>`
            + `<button type="button" class="small-btn" data-add-widget="${escHtml(item.type)}" ${alreadyUsed || !workspaceState.draft || state.workspaceSaving ? 'disabled' : ''}>添加</button></div>`;
    }).join('');
}

function renderEditor() {
    const draft = workspaceState.draft;
    const name = $('#workspaceName');
    const revision = $('#workspaceRevisionStatus');
    if (name) name.value = draft?.name || '';
    if (revision) {
        revision.textContent = draft ? `revision ${draft.revision} · ${draft.grid.columns} × ${draft.grid.rows}` : '未选择工作区';
    }
    renderWorkspaceList();
    renderGrid();
    renderCatalog();
    updateControls();
}

function collectionParts(payload) {
    return {
        workspaces: asArray(payload?.workspaces),
        grid: normalizeGrid(payload?.grid),
        catalog: asArray(payload?.widget_catalog || payload?.widgets).map(normalizeCatalogItem).filter((item) => item.type),
    };
}

async function loadWorkspace(id) {
    const requestSequence = ++workspaceRequestSequence;
    if (!id) {
        workspaceState.selectedId = null;
        workspaceState.baseline = null;
        workspaceState.draft = null;
        markDirty(false);
        renderEditor();
        return;
    }
    setMessage('正在读取工作区…');
    try {
        const payload = await requestJson(`/api/settings/workspaces/${encodeURIComponent(id)}`);
        if (requestSequence !== workspaceRequestSequence) return;
        const workspace = normalizeWorkspace(payload);
        if (!workspace.id) workspace.id = id;
        workspaceState.selectedId = workspace.id;
        workspaceState.baseline = clone(workspace);
        workspaceState.draft = clone(workspace);
        setConflict(false);
        markDirty(false);
        renderEditor();
        setMessage('拖动组件调整位置，拖动右下角调整大小。');
    } catch (error) {
        if (requestSequence !== workspaceRequestSequence) return;
        setMessage(`读取工作区失败：${error.message}`, 'error');
    }
}

export async function loadWorkspaces(preferredId = null) {
    const requestSequence = ++collectionRequestSequence;
    try {
        const payload = await requestJson('/api/settings/workspaces');
        if (requestSequence !== collectionRequestSequence) return;
        const parts = collectionParts(payload);
        workspaceState.available = true;
        workspaceState.grid = parts.grid;
        workspaceState.catalog = parts.catalog;
        workspaceState.workspaces = parts.workspaces;
        notifyWorkspaceList();
        const selected = preferredId && parts.workspaces.some((item) => workspaceId(item) === preferredId)
            ? preferredId
            : (workspaceState.selectedId && parts.workspaces.some((item) => workspaceId(item) === workspaceState.selectedId)
                ? workspaceState.selectedId
                : workspaceId(parts.workspaces[0]));
        renderWorkspaceList();
        await loadWorkspace(selected);
    } catch (error) {
        if (requestSequence !== collectionRequestSequence) return;
        workspaceState.available = false;
        workspaceState.workspaces = [];
        workspaceState.catalog = [];
        workspaceState.selectedId = null;
        workspaceState.baseline = null;
        workspaceState.draft = null;
        notifyWorkspaceList();
        setWorkspaceDirty(false);
        renderEditor();
        const unavailable = error.status === 404 || error.status === 405;
        setMessage(unavailable ? '工作区后端 API 尚未接入；现有配置编辑不受影响。' : `读取工作区失败：${error.message}`, unavailable ? '' : 'error');
    }
}

async function selectWorkspace(id) {
    if (state.workspaceSaving || !id || id === workspaceState.selectedId) return;
    if (state.workspaceDirty && !window.confirm('当前工作区有未保存修改，放弃后切换吗？')) {
        renderWorkspaceList();
        return;
    }
    await loadWorkspace(id);
}

async function createWorkspace() {
    if (state.workspaceSaving) return;
    const name = window.prompt('新工作区名称', '新工作区');
    if (name === null || !name.trim()) return;
    try {
        const result = await requestJson('/api/settings/workspaces', {method: 'POST', body: {name: name.trim()}});
        const created = normalizeWorkspace(result);
        await loadWorkspaces(created.id || workspaceId(result));
        setMessage('工作区已创建。', 'success');
    } catch (error) {
        setMessage(`新建失败：${error.message}`, 'error');
    }
}

async function duplicateWorkspace() {
    if (state.workspaceSaving) return;
    const id = workspaceState.selectedId;
    if (!id) return;
    try {
        const result = await requestJson(`/api/settings/workspaces/${encodeURIComponent(id)}/duplicate`, {method: 'POST'});
        const duplicated = normalizeWorkspace(result);
        await loadWorkspaces(duplicated.id || workspaceId(result));
        setMessage('工作区副本已创建。', 'success');
    } catch (error) {
        setMessage(`复制失败：${error.message}`, 'error');
    }
}

async function deleteWorkspace() {
    if (state.workspaceSaving) return;
    const draft = workspaceState.draft;
    if (!draft || draft.required) return;
    if (!window.confirm(`确定删除“${draft.name}”吗？此操作无法撤销。`)) return;
    try {
        await requestJson(
            `/api/settings/workspaces/${encodeURIComponent(draft.id)}?revision=${encodeURIComponent(draft.revision)}`,
            {method: 'DELETE'},
        );
        workspaceState.selectedId = null;
        setWorkspaceDirty(false);
        await loadWorkspaces();
        setMessage('工作区已删除。', 'success');
    } catch (error) {
        setMessage(`删除失败：${error.message}`, 'error');
    }
}

function uniqueWidgetId(type) {
    const base = type.split('.').pop().replace(/[^a-z0-9_-]/gi, '-') || 'widget';
    const used = new Set(workspaceState.draft.widgets.map((widget) => String(widget.id)));
    let suffix = 1;
    while (used.has(`${base}-${suffix}`)) suffix += 1;
    return `${base}-${suffix}`;
}

function addWidget(type) {
    if (state.workspaceSaving) return;
    const draft = workspaceState.draft;
    const definition = workspaceState.catalog.find((item) => item.type === type);
    if (!draft || !definition) return;
    if (definition.singleInstance && draft.widgets.some((widget) => widget.type === type)) return;
    const rect = firstFit(draft.widgets, definition.defaultSize, draft.grid, definition.constraints);
    if (!rect) {
        setMessage('当前 16 × 15 网格没有足够空间放置该组件。', 'error');
        return;
    }
    const defaults = clone(definition.default_widget || definition.defaultWidget || {});
    draft.widgets.push({
        ...defaults,
        id: uniqueWidgetId(type),
        type,
        owner: definition.owner || null,
        available: true,
        slot: defaults.slot || 'main',
        constraints: clone(definition.constraints),
        ...rect,
    });
    markDirty(true);
    renderGrid();
    renderCatalog();
    setMessage('组件已添加，保存后生效。');
}

function removeWidget(id) {
    if (state.workspaceSaving) return;
    const draft = workspaceState.draft;
    if (!draft) return;
    const next = draft.widgets.filter((widget) => String(widget.id) !== String(id));
    if (next.length === draft.widgets.length) return;
    draft.widgets = next;
    markDirty(true);
    renderGrid();
    renderCatalog();
    setMessage('组件已移除，保存后生效。');
}

function discardWorkspace() {
    if (!workspaceState.baseline) return;
    workspaceState.draft = clone(workspaceState.baseline);
    setConflict(false);
    markDirty(false);
    renderEditor();
    setMessage('已放弃工作区草稿。');
}

async function saveWorkspace() {
    const draft = workspaceState.draft;
    if (!draft || state.workspaceSaving) return;
    const savedId = draft.id;
    workspaceRequestSequence += 1;
    draft.name = draft.name.trim();
    if (!draft.name) {
        setMessage('工作区名称不能为空。', 'error');
        $('#workspaceName')?.focus();
        return;
    }
    const result = validate(draft.widgets, draft.grid, catalogConstraints());
    if (!result.valid) {
        setMessage(`布局无效：${result.errors.join('；')}`, 'error');
        return;
    }
    state.workspaceSaving = true;
    updateControls();
    renderWorkspaceList();
    renderCatalog();
    renderGrid();
    setMessage('正在保存工作区…');
    try {
        const payload = await requestJson(`/api/settings/workspaces/${encodeURIComponent(savedId)}`, {
            method: 'PUT',
            body: workspacePayload(),
        });
        if (workspaceState.selectedId !== savedId) return;
        const saved = normalizeWorkspace(payload);
        if (saved.id && saved.widgets.length) {
            workspaceState.baseline = clone(saved);
            workspaceState.draft = clone(saved);
            workspaceState.selectedId = saved.id;
            setWorkspaceDirty(false);
            setConflict(false);
            await loadWorkspaces(saved.id);
        } else {
            setWorkspaceDirty(false);
            setConflict(false);
            await loadWorkspace(savedId);
            await loadWorkspaces(savedId);
        }
        setMessage('工作区布局已保存。', 'success');
    } catch (error) {
        if (error.status === 409) {
            setConflict(true);
            setWorkspaceDirty(true);
            const currentRevision = error.payload?.current_revision ?? error.payload?.error?.current_revision;
            const revision = $('#workspaceRevisionStatus');
            if (revision && currentRevision !== undefined) {
                revision.textContent = `草稿 revision ${draft.revision} · 服务器 revision ${currentRevision}`;
            }
            setMessage(`保存冲突（409）：服务器 revision 已变化${currentRevision === undefined ? '' : `为 ${currentRevision}`}，草稿已保留。`, 'error');
        } else {
            setMessage(`保存失败：${error.message}`, 'error');
        }
    } finally {
        state.workspaceSaving = false;
        updateControls();
        renderWorkspaceList();
        renderCatalog();
        renderGrid();
    }
}

function beginPointerInteraction(event, card, mode) {
    if (state.workspaceSaving || !workspaceState.draft || event.button !== 0) return;
    const widget = workspaceState.draft.widgets.find((item) => String(item.id) === card.dataset.widgetId);
    if (!widget) return;
    event.preventDefault();
    const preview = $('#workspaceGridPreview');
    const bounds = preview.getBoundingClientRect();
    workspaceState.interaction = {
        pointerId: event.pointerId,
        card,
        mode,
        widget,
        original: normalizeRect(widget),
        candidate: normalizeRect(widget),
        startX: event.clientX,
        startY: event.clientY,
        bounds,
    };
    card.classList.add('is-interacting');
    card.setPointerCapture?.(event.pointerId);
}

function movePointerInteraction(event) {
    const interaction = workspaceState.interaction;
    if (!interaction || interaction.pointerId !== event.pointerId) return;
    const grid = normalizeGrid(workspaceState.draft.grid);
    const dx = Math.round((event.clientX - interaction.startX) / interaction.bounds.width * grid.columns);
    const dy = Math.round((event.clientY - interaction.startY) / interaction.bounds.height * grid.rows);
    const definition = workspaceState.catalog.find((item) => item.type === interaction.widget.type);
    const definitionConstraints = definition?.constraints || {};
    const constraints = Object.keys(definitionConstraints).length ? definitionConstraints : (interaction.widget.constraints || {});
    const candidate = interaction.mode === 'resize'
        ? {...interaction.original, w: interaction.original.w + dx, h: interaction.original.h + dy}
        : {...interaction.original, x: interaction.original.x + dx, y: interaction.original.y + dy};
    interaction.candidate = constrain(candidate, grid, constraints);
    interaction.moved = Object.keys(interaction.original).some((key) => interaction.original[key] !== interaction.candidate[key]);
    const valid = isPlacementValid(interaction.candidate, workspaceState.draft.widgets, grid, {
        ignoreId: interaction.widget.id,
        constraints,
    });
    interaction.valid = valid;
    interaction.card.classList.toggle('is-invalid', !valid);
    placeCard(interaction.card, interaction.candidate, grid);
}

function endPointerInteraction(event) {
    const interaction = workspaceState.interaction;
    if (!interaction || interaction.pointerId !== event.pointerId) return;
    interaction.card.releasePointerCapture?.(event.pointerId);
    interaction.card.classList.remove('is-interacting');
    const cancelled = event.type === 'pointercancel';
    if (!cancelled && interaction.moved && interaction.valid !== false) {
        Object.assign(interaction.widget, interaction.candidate);
        markDirty(true);
        setMessage('布局已修改，保存后生效。');
    } else if (!cancelled && interaction.valid === false) {
        setMessage('目标位置与其他组件重叠，已恢复原布局。', 'error');
    }
    workspaceState.interaction = null;
    renderGrid();
}

function bindEvents() {
    if (eventsBound) return;
    eventsBound = true;
    $('#workspaceNewButton')?.addEventListener('click', createWorkspace);
    $('#workspaceDuplicateButton')?.addEventListener('click', duplicateWorkspace);
    $('#workspaceDeleteButton')?.addEventListener('click', deleteWorkspace);
    $('#workspaceSaveButton')?.addEventListener('click', saveWorkspace);
    $('#workspaceDiscardButton')?.addEventListener('click', discardWorkspace);
    $('#workspaceName')?.addEventListener('input', (event) => {
        if (!workspaceState.draft) return;
        workspaceState.draft.name = event.target.value;
        markDirty(true);
    });
    $('#workspaceList')?.addEventListener('click', (event) => {
        const button = event.target.closest?.('[data-workspace-id]');
        if (button) selectWorkspace(button.dataset.workspaceId);
    });
    $('#workspaceCatalog')?.addEventListener('click', (event) => {
        const button = event.target.closest?.('[data-add-widget]');
        if (button) addWidget(button.dataset.addWidget);
    });
    const preview = $('#workspaceGridPreview');
    preview?.addEventListener('click', (event) => {
        const button = event.target.closest?.('[data-remove-widget]');
        if (button) removeWidget(button.dataset.removeWidget);
    });
    preview?.addEventListener('pointerdown', (event) => {
        if (event.target.closest?.('button')) return;
        const card = event.target.closest?.('.workspace-widget-card');
        if (!card) return;
        const mode = event.target.closest?.('.workspace-resize-handle') ? 'resize' : 'move';
        beginPointerInteraction(event, card, mode);
    });
    preview?.addEventListener('pointermove', movePointerInteraction);
    preview?.addEventListener('pointerup', endPointerInteraction);
    preview?.addEventListener('pointercancel', endPointerInteraction);
}

export function initWorkspaces(options = {}) {
    workspaceState.onWorkspacesChange = typeof options.onWorkspacesChange === 'function'
        ? options.onWorkspacesChange
        : null;
    bindEvents();
    renderEditor();
    return loadWorkspaces();
}
