const MIN_GRID = 4;
const MAX_GRID = 48;
const DEFAULT_GRID = Object.freeze({columns: 16, rows: 15});

function integer(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) ? Math.round(number) : fallback;
}

export function clamp(value, minimum, maximum) {
    return Math.min(Math.max(value, minimum), maximum);
}

export function normalizeGrid(grid = {}) {
    return {
        columns: clamp(integer(grid.columns ?? grid.cols, DEFAULT_GRID.columns), MIN_GRID, MAX_GRID),
        rows: clamp(integer(grid.rows, DEFAULT_GRID.rows), MIN_GRID, MAX_GRID),
    };
}

export function normalizeRect(value = {}) {
    const hasDirectRect = ['x', 'y', 'w', 'h', 'width', 'height'].some((key) => value?.[key] !== undefined);
    const source = hasDirectRect ? value : (value.layout || value.position || value.grid || value);
    return {
        x: integer(source.x, 0),
        y: integer(source.y, 0),
        w: Math.max(1, integer(source.w ?? source.width, 1)),
        h: Math.max(1, integer(source.h ?? source.height, 1)),
    };
}

export function collision(first, second) {
    const a = normalizeRect(first);
    const b = normalizeRect(second);
    return a.x < b.x + b.w
        && a.x + a.w > b.x
        && a.y < b.y + b.h
        && a.y + a.h > b.y;
}

function constraintValue(constraints, names, fallback) {
    for (const name of names) {
        if (constraints?.[name] !== undefined) return integer(constraints[name], fallback);
    }
    return fallback;
}

export function constrain(rect, grid = DEFAULT_GRID, constraints = {}) {
    const bounds = normalizeGrid(grid);
    const source = normalizeRect(rect);
    const minW = clamp(constraintValue(constraints, ['minW', 'min_w', 'minWidth', 'min_width'], 1), 1, bounds.columns);
    const minH = clamp(constraintValue(constraints, ['minH', 'min_h', 'minHeight', 'min_height'], 1), 1, bounds.rows);
    const maxW = clamp(constraintValue(constraints, ['maxW', 'max_w', 'maxWidth', 'max_width'], bounds.columns), minW, bounds.columns);
    const maxH = clamp(constraintValue(constraints, ['maxH', 'max_h', 'maxHeight', 'max_height'], bounds.rows), minH, bounds.rows);
    const w = clamp(source.w, minW, maxW);
    const h = clamp(source.h, minH, maxH);
    return {
        x: clamp(source.x, 0, bounds.columns - w),
        y: clamp(source.y, 0, bounds.rows - h),
        w,
        h,
    };
}

export function findCollisions(rect, items = [], ignoreId = null) {
    return items.filter((item) => {
        if (ignoreId !== null && String(item.id) === String(ignoreId)) return false;
        return collision(rect, item);
    });
}

export function isPlacementValid(rect, items = [], grid = DEFAULT_GRID, options = {}) {
    const bounds = normalizeGrid(grid);
    const raw = normalizeRect(rect);
    const constrained = constrain(raw, bounds, options.constraints || {});
    if (raw.x !== constrained.x || raw.y !== constrained.y || raw.w !== constrained.w || raw.h !== constrained.h) {
        return false;
    }
    return findCollisions(raw, items, options.ignoreId ?? rect?.id ?? null).length === 0;
}

export function validate(items = [], grid = DEFAULT_GRID, constraintsByType = {}) {
    const errors = [];
    const ids = new Set();
    items.forEach((item, index) => {
        const label = item?.id || `widgets[${index}]`;
        if (!item || typeof item !== 'object') {
            errors.push(`${label} 不是有效组件`);
            return;
        }
        if (!item.id) errors.push(`${label} 缺少 id`);
        else if (ids.has(String(item.id))) errors.push(`组件 id 重复：${item.id}`);
        else ids.add(String(item.id));
        const constraints = {...(item.constraints || {}), ...(constraintsByType[item.type] || {})};
        if (!isPlacementValid(item, items.slice(0, index), grid, {constraints})) {
            const bounded = constrain(item, grid, constraints);
            const raw = normalizeRect(item);
            const outside = raw.x !== bounded.x || raw.y !== bounded.y || raw.w !== bounded.w || raw.h !== bounded.h;
            errors.push(outside ? `${label} 超出网格或尺寸约束` : `${label} 与其他组件重叠`);
        }
    });
    return {valid: errors.length === 0, errors};
}

export function recommendGrid(target = {}, density = 'normal') {
    const width = Math.max(1, integer(target.width ?? target.viewportWidth, 1920));
    const height = Math.max(1, integer(target.height ?? target.viewportHeight, 1080));
    const targetCellWidth = Math.max(
        1,
        integer(target.targetCellWidth ?? target.target_cell_width, 120),
    );
    const targetCellHeight = Math.max(
        1,
        integer(target.targetCellHeight ?? target.target_cell_height, 72),
    );
    const densityScale = {compact: 0.82, normal: 1, spacious: 1.2}[density] || 1;
    const columns = Math.round(width / (targetCellWidth * densityScale));
    const rows = Math.round(height / (targetCellHeight * densityScale));
    return {
        columns: clamp(columns, MIN_GRID, MAX_GRID),
        rows: clamp(rows, MIN_GRID, MAX_GRID),
    };
}

function itemConstraints(item, constraintsByType = {}) {
    return {...(item?.constraints || {}), ...(constraintsByType[item?.type] || {})};
}

function constraintPair(constraints = {}) {
    const minW = Math.max(1, constraintValue(constraints, ['minW', 'min_w', 'minWidth', 'min_width'], 1));
    const minH = Math.max(1, constraintValue(constraints, ['minH', 'min_h', 'minHeight', 'min_height'], 1));
    return {minW, minH};
}

function stableItems(items = []) {
    // Largest-first packing significantly improves success on small grids.
    return items.map((item, index) => ({item, index})).sort((a, b) => {
        const ac = itemConstraints(a.item);
        const bc = itemConstraints(b.item);
        const aMin = constraintPair(ac);
        const bMin = constraintPair(bc);
        const aArea = Math.max(normalizeRect(a.item).w * normalizeRect(a.item).h, aMin.minW * aMin.minH);
        const bArea = Math.max(normalizeRect(b.item).w * normalizeRect(b.item).h, bMin.minW * bMin.minH);
        if (bArea !== aArea) return bArea - aArea;
        if (bMin.minH !== aMin.minH) return bMin.minH - aMin.minH;
        if (bMin.minW !== aMin.minW) return bMin.minW - aMin.minW;
        const ar = normalizeRect(a.item);
        const br = normalizeRect(b.item);
        return ar.y - br.y || ar.x - br.x || String(a.item?.id ?? '').localeCompare(String(b.item?.id ?? '')) || a.index - b.index;
    });
}


function attachReflowMeta(items, ok, grid, errors = []) {
    Object.defineProperties(items, {
        ok: {value: ok, enumerable: false},
        grid: {value: grid, enumerable: false},
        errors: {value: errors, enumerable: false},
    });
    return items;
}

export function reflow(items = [], oldGrid = DEFAULT_GRID, newGrid = DEFAULT_GRID, constraintsByType = {}) {
    const target = normalizeGrid(newGrid);
    const source = items.map((item) => ({...item}));
    const placed = [];
    const result = [];
    for (const entry of stableItems(source)) {
        const item = entry.item;
        const rect = normalizeRect(item);
        const constraints = itemConstraints(item, constraintsByType);
        const {minW: minWidth, minH: minHeight} = constraintPair(constraints);
        if (minWidth > target.columns || minHeight > target.rows) {
            return {
                ok: false,
                items: attachReflowMeta(
                    source,
                    false,
                    target,
                    [`${item.id || '组件'} 的最小尺寸 ${minWidth}×${minHeight} 超出目标网格 ${target.columns}×${target.rows}`],
                ),
                grid: target,
            };
        }
        const desired = constrain(rect, target, constraints);
        if (isPlacementValid(desired, placed, target, {constraints, ignoreId: item.id})) {
            const next = {...item, x: desired.x, y: desired.y, w: desired.w, h: desired.h};
            result.push(next);
            placed.push(next);
            continue;
        }
        // Progressive shrink so denser small screens can still host the layout.
        let fit = null;
        for (let h = desired.h; h >= minHeight && !fit; h -= 1) {
            for (let w = desired.w; w >= minWidth; w -= 1) {
                fit = firstFit(placed, {w, h}, target, constraints);
                if (fit) break;
            }
        }
        if (!fit) {
            return {
                ok: false,
                items: attachReflowMeta(
                    source,
                    false,
                    target,
                    [`无法在 ${target.columns} × ${target.rows} 网格中放置 ${item.id || '组件'}`],
                ),
                grid: target,
            };
        }
        const next = {...item, x: fit.x, y: fit.y, w: fit.w, h: fit.h};
        result.push(next);
        placed.push(next);
    }
    return {ok: true, items: attachReflowMeta(result, true, target), grid: target};
}

export function firstFit(items = [], size = {}, grid = DEFAULT_GRID, constraints = {}) {
    const bounds = normalizeGrid(grid);
    const dimensions = constrain({x: 0, y: 0, ...size}, bounds, constraints);
    for (let y = 0; y <= bounds.rows - dimensions.h; y += 1) {
        for (let x = 0; x <= bounds.columns - dimensions.w; x += 1) {
            const candidate = {...dimensions, x, y};
            if (!findCollisions(candidate, items).length) return candidate;
        }
    }
    return null;
}

export function fitGridForItems(baseGrid = DEFAULT_GRID, items = [], constraintsByType = {}, {
    minColumns = MIN_GRID,
    minRows = MIN_GRID,
    maxColumns = MAX_GRID,
    maxRows = MAX_GRID,
} = {}) {
    // Estimate a lower bound from min footprints, then grow until pack succeeds.
    let minArea = 0;
    let minW = minColumns;
    let minH = minRows;
    for (const item of items) {
        const constraints = itemConstraints(item, constraintsByType);
        const pair = constraintPair(constraints);
        minArea += pair.minW * pair.minH;
        minW = Math.max(minW, pair.minW);
        minH = Math.max(minH, pair.minH);
    }
    let columns = clamp(Math.max(integer(baseGrid.columns, DEFAULT_GRID.columns), minW, Math.ceil(Math.sqrt(minArea * 1.4))), minColumns, maxColumns);
    let rows = clamp(Math.max(integer(baseGrid.rows, DEFAULT_GRID.rows), minH, Math.ceil(minArea / Math.max(1, columns))), minRows, maxRows);

    let best = null;
    for (let attempt = 0; attempt < 120; attempt += 1) {
        const candidate = {columns, rows};
        const packed = reflow(items, baseGrid, candidate, constraintsByType);
        if (packed.ok) {
            const score = columns * rows;
            if (!best || score < best.score) {
                best = {ok: true, grid: candidate, items: packed.items, errors: [], score};
            }
            // Try denser by shrinking the longer axis first.
            if (columns >= rows && columns > minW) columns -= 1;
            else if (rows > minH) rows -= 1;
            else if (columns > minW) columns -= 1;
            else break;
            continue;
        }
        if (columns >= maxColumns && rows >= maxRows) {
            return best || {ok: false, grid: candidate, items: packed.items, errors: packed.errors || []};
        }
        if (columns <= rows && columns < maxColumns) columns += 1;
        else if (rows < maxRows) rows += 1;
        else if (columns < maxColumns) columns += 1;
        else rows = Math.min(maxRows, rows + 1);
    }
    return best || {ok: false, grid: {columns, rows}, items, errors: ['无法为组件推算可容纳网格']};
}

