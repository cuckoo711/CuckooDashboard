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
        columns: Math.max(1, integer(grid.columns ?? grid.cols, DEFAULT_GRID.columns)),
        rows: Math.max(1, integer(grid.rows, DEFAULT_GRID.rows)),
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
