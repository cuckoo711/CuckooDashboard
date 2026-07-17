const DENSITIES = Object.freeze({
    compact: 0.82,
    normal: 1,
    spacious: 1.22,
});

function finite(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
}

function positive(value, fallback) {
    const number = finite(value, fallback);
    return number > 0 ? number : fallback;
}

export function normalizeCalibration(calibration = {}) {
    const target = calibration.target || calibration.viewport || {};
    const offset = calibration.offset || {};
    const fit = (calibration.fit_mode ?? calibration.fit) === 'fill' ? 'fill' : 'contain';
    const density = Object.hasOwn(DENSITIES, calibration.density) ? calibration.density : 'normal';
    return {
        width: positive(
            calibration.reference_width ?? calibration.width ?? calibration.target_width ?? target.width,
            1920,
        ),
        height: positive(
            calibration.reference_height ?? calibration.height ?? calibration.target_height ?? target.height,
            1080,
        ),
        targetCellWidth: positive(calibration.target_cell_width ?? calibration.targetCellWidth, 120),
        targetCellHeight: positive(calibration.target_cell_height ?? calibration.targetCellHeight, 72),
        fit,
        density,
        offset: {
            x: finite(calibration.offset_x ?? offset.x, 0),
            y: finite(calibration.offset_y ?? offset.y, 0),
        },
    };
}

function rectSize(contentRect = {}) {
    const width = positive(contentRect.width ?? contentRect.right - contentRect.left, 0);
    const height = positive(contentRect.height ?? contentRect.bottom - contentRect.top, 0);
    return {width, height};
}

export function measureViewport(contentRect, grid = {}, calibration = {}) {
    const columns = Math.max(1, Math.round(finite(grid.columns ?? grid.cols, 1)));
    const rows = Math.max(1, Math.round(finite(grid.rows, 1)));
    const normalized = normalizeCalibration(calibration);
    const container = rectSize(contentRect);
    const targetRatio = normalized.width / normalized.height;
    const containerRatio = container.width && container.height ? container.width / container.height : targetRatio;
    let surfaceWidth = container.width;
    let surfaceHeight = container.height;
    if (normalized.fit === 'contain' && container.width > 0 && container.height > 0) {
        if (containerRatio > targetRatio) surfaceWidth = container.height * targetRatio;
        else surfaceHeight = container.width / targetRatio;
    }
    const gutter = {
        x: Math.max(0, (container.width - surfaceWidth) / 2),
        y: Math.max(0, (container.height - surfaceHeight) / 2),
    };
    const offset = {...normalized.offset};
    const cell = {
        width: surfaceWidth / columns,
        height: surfaceHeight / rows,
        min: Math.min(surfaceWidth / columns, surfaceHeight / rows),
    };
    const densityScale = DENSITIES[normalized.density];
    const effectiveDensity = normalized.density === 'normal'
        ? (cell.min && cell.min < 72 ? 'compact' : (cell.min > 150 ? 'spacious' : 'normal'))
        : normalized.density;
    return {
        fit: normalized.fit,
        density: effectiveDensity,
        requestedDensity: normalized.density,
        container: {width: container.width, height: container.height},
        surface: {width: surfaceWidth, height: surfaceHeight},
        cell: {...cell, scale: densityScale},
        gutter,
        offset,
        grid: {columns, rows},
        calibration: normalized,
        diagnostic: {
            aspectRatio: container.width && container.height ? containerRatio : targetRatio,
            targetAspectRatio: targetRatio,
            pixelArea: surfaceWidth * surfaceHeight,
            isEmpty: !(container.width && container.height),
        },
    };
}

function setCssVariables(host, result) {
    if (!host || !result) return;
    const surface = host.querySelector?.(':scope > .workspace-surface');
    const targets = [host, surface].filter(Boolean);
    targets.forEach((target) => {
        if (!target.style || typeof target.style.setProperty !== 'function') return;
        target.style.setProperty('--workspace-container-width', `${result.container.width}px`);
        target.style.setProperty('--workspace-container-height', `${result.container.height}px`);
        target.style.setProperty('--workspace-surface-width', `${result.surface.width}px`);
        target.style.setProperty('--workspace-surface-height', `${result.surface.height}px`);
        target.style.setProperty('--workspace-cell-width', `${result.cell.width}px`);
        target.style.setProperty('--workspace-cell-height', `${result.cell.height}px`);
        target.style.setProperty('--workspace-offset-x', `${result.offset.x}px`);
        target.style.setProperty('--workspace-offset-y', `${result.offset.y}px`);
        target.dataset.workspaceFit = result.fit;
        target.dataset.workspaceDensity = result.density;
    });
}

export function createViewportCalibration({host, grid, calibration, onChange} = {}) {
    if (!host) throw new TypeError('viewport calibration requires host');
    let currentGrid = grid || {columns: 1, rows: 1};
    let currentCalibration = calibration || {};
    let frame = 0;
    let destroyed = false;
    let latest = null;
    const measure = () => {
        frame = 0;
        if (destroyed) return;
        const rect = typeof host.getBoundingClientRect === 'function'
            ? host.getBoundingClientRect()
            : {width: host.clientWidth || 0, height: host.clientHeight || 0};
        latest = measureViewport(rect, currentGrid, currentCalibration);
        setCssVariables(host, latest);
        onChange?.(latest);
    };
    const schedule = () => {
        if (destroyed || frame) return;
        const raf = host.ownerDocument?.defaultView?.requestAnimationFrame || globalThis.requestAnimationFrame;
        frame = typeof raf === 'function' ? raf(measure) : setTimeout(measure, 0);
    };
    const observer = typeof ResizeObserver === 'function'
        ? new ResizeObserver(schedule)
        : null;
    observer?.observe(host);
    const api = {
        get value() { return latest; },
        update(nextGrid = currentGrid, nextCalibration = currentCalibration) {
            currentGrid = nextGrid || currentGrid;
            currentCalibration = nextCalibration || currentCalibration;
            schedule();
            return latest;
        },
        refresh() { schedule(); return latest; },
        destroy() {
            destroyed = true;
            observer?.disconnect();
            if (frame) {
                const cancel = host.ownerDocument?.defaultView?.cancelAnimationFrame || globalThis.cancelAnimationFrame;
                if (typeof cancel === 'function') cancel(frame);
                else clearTimeout(frame);
            }
            frame = 0;
        },
    };
    schedule();
    return api;
}
