function sourceId(source) {
    if (typeof source === 'string') return source;
    if (!source || typeof source !== 'object') return '';
    return String(source.id || source.source || source.name || '');
}

function sourceIds(sources) {
    if (Array.isArray(sources)) return sources.map(sourceId).filter(Boolean);
    if (!sources || typeof sources !== 'object') return [];
    return Object.entries(sources).map(([key, value]) => sourceId(value) || key).filter(Boolean);
}

function manifestKey(manifest) {
    return JSON.stringify({
        id: manifest.id,
        version: manifest.version,
        revision: manifest.revision,
        grid: manifest.grid,
        sources: sourceIds(manifest.sources),
        widgets: (manifest.widgets || []).map((widget) => ({
            id: widget.id,
            type: widget.type,
            slot: widget.slot,
            sources: sourceIds(widget.sources),
            channels: widget.channels || [],
            layout: widget.layout,
            constraints: widget.constraints,
        })),
    });
}

function isInteger(value, minimum) {
    return Number.isInteger(value) && value >= minimum;
}

function cleanupMounted(entries) {
    entries.slice().reverse().forEach(({ widget, instance, unsubscribers = [] }) => {
        unsubscribers.forEach((unsubscribe) => {
            try { unsubscribe(); } catch (_error) {}
        });
        try {
            instance.destroy();
        } catch (error) {
            console.error(`[workspace] destroy failed for ${widget.id}:`, error);
        }
    });
}

function overlaps(first, second) {
    return first.x < second.x + second.width
        && first.x + first.width > second.x
        && first.y < second.y + second.height
        && first.y + first.height > second.y;
}

export function collectManifestSources(manifest = {}) {
    const sources = new Set(sourceIds(manifest.sources));
    (manifest.widgets || []).forEach((widget) => {
        sourceIds(widget.sources).forEach((source) => sources.add(source));
    });
    return [...sources];
}

export function collectManifestChannels(manifest = {}) {
    const channels = new Set();
    (manifest.widgets || []).forEach((widget) => {
        (widget.channels || []).forEach((channel) => {
            if (typeof channel === 'string' && channel) channels.add(channel);
        });
    });
    return [...channels];
}

export class WorkspaceHost {
    constructor({ root, registry, bus }) {
        if (!root || !registry || !bus) throw new TypeError('WorkspaceHost requires root, registry, and bus');
        this.root = root;
        this.registry = registry;
        this.bus = bus;
        this.mounted = [];
        this.key = '';
        this.workspaceId = '';
        this.revision = 0;
        this.grid = null;
        this.sources = [];
        this.channels = [];
    }

    validate(manifest) {
        const errors = [];
        if (!manifest || typeof manifest !== 'object') return ['manifest must be an object'];
        if (manifest.version !== 2) errors.push('manifest.version must be 2');
        if (typeof manifest.id !== 'string' || !manifest.id.trim()) errors.push('manifest.id must be a non-empty string');
        if (!isInteger(manifest.revision, 1)) errors.push('manifest.revision must be a positive integer');
        if (typeof manifest.name !== 'string' || !manifest.name.trim()) errors.push('manifest.name must be a non-empty string');
        if (typeof manifest.kind !== 'string' || !manifest.kind.trim()) errors.push('manifest.kind must be a non-empty string');
        if (typeof manifest.required !== 'boolean') errors.push('manifest.required must be a boolean');
        if (!Array.isArray(manifest.sources)) errors.push('manifest.sources must be an array');
        else if (manifest.sources.some((source) => !sourceId(source))) errors.push('manifest.sources contains an invalid source');
        const grid = manifest.grid;
        if (!grid || typeof grid !== 'object') errors.push('manifest.grid must be an object');
        const columns = grid?.columns;
        const rows = grid?.rows;
        if (columns !== 16 || rows !== 15) errors.push('manifest.grid must be 16x15');
        if (!Array.isArray(manifest.widgets)) errors.push('manifest.widgets must be an array');
        const ids = new Set();
        const singletonTypes = new Set();
        const placed = [];
        (manifest.widgets || []).forEach((widget, index) => {
            const label = widget?.id || `widgets[${index}]`;
            if (!widget || typeof widget !== 'object') {
                errors.push(`${label} must be an object`);
                return;
            }
            if (typeof widget.id !== 'string' || !widget.id.trim()) errors.push(`${label} is missing id`);
            else if (ids.has(widget.id)) errors.push(`duplicate widget id: ${widget.id}`);
            else ids.add(widget.id);
            if (!widget.type || !this.registry.has(widget.type)) errors.push(`unknown widget type: ${widget.type || '(empty)'}`);
            const registration = widget.type ? this.registry.get(widget.type) : null;
            if (registration?.singleInstance) {
                if (singletonTypes.has(widget.type)) errors.push(`single-instance widget repeated: ${widget.type}`);
                singletonTypes.add(widget.type);
            }
            if (typeof widget.slot !== 'string' || !widget.slot.trim()) errors.push(`${label} is missing slot`);
            if (!Array.isArray(widget.sources)) errors.push(`${label}.sources must be an array`);
            else if (widget.sources.some((source) => !sourceId(source))) errors.push(`${label}.sources contains an invalid source`);
            if (!Array.isArray(widget.channels)) errors.push(`${label}.channels must be an array`);
            else if (widget.channels.some((channel) => typeof channel !== 'string' || !channel)) {
                errors.push(`${label}.channels contains an invalid channel`);
            }

            const layout = widget.layout;
            if (!layout || typeof layout !== 'object') {
                errors.push(`${label}.layout must be an object`);
                return;
            }
            for (const field of ['x', 'y']) {
                if (!isInteger(layout[field], 0)) errors.push(`${label}.layout.${field} must be a non-negative integer`);
            }
            for (const field of ['width', 'height']) {
                if (!isInteger(layout[field], 1)) errors.push(`${label}.layout.${field} must be a positive integer`);
            }
            if (isInteger(columns, 1) && isInteger(layout.x, 0) && isInteger(layout.width, 1)
                && layout.x + layout.width > columns) errors.push(`${label}.layout exceeds grid columns`);
            if (isInteger(rows, 1) && isInteger(layout.y, 0) && isInteger(layout.height, 1)
                && layout.y + layout.height > rows) errors.push(`${label}.layout exceeds grid rows`);

            const constraints = widget.constraints;
            if (!constraints || typeof constraints !== 'object') {
                errors.push(`${label}.constraints must be an object`);
                return;
            }
            for (const field of ['min_width', 'min_height', 'max_width', 'max_height']) {
                if (!isInteger(constraints[field], 1)) errors.push(`${label}.constraints.${field} must be a positive integer`);
            }
            if (isInteger(constraints.min_width, 1) && isInteger(constraints.max_width, 1)
                && constraints.min_width > constraints.max_width) errors.push(`${label}.constraints width range is invalid`);
            if (isInteger(constraints.min_height, 1) && isInteger(constraints.max_height, 1)
                && constraints.min_height > constraints.max_height) errors.push(`${label}.constraints height range is invalid`);
            if (isInteger(columns, 1) && isInteger(constraints.max_width, 1) && constraints.max_width > columns) {
                errors.push(`${label}.constraints.max_width exceeds grid columns`);
            }
            if (isInteger(rows, 1) && isInteger(constraints.max_height, 1) && constraints.max_height > rows) {
                errors.push(`${label}.constraints.max_height exceeds grid rows`);
            }
            if (isInteger(layout.width, 1) && isInteger(constraints.min_width, 1) && layout.width < constraints.min_width) {
                errors.push(`${label}.layout.width is below its constraint`);
            }
            if (isInteger(layout.width, 1) && isInteger(constraints.max_width, 1) && layout.width > constraints.max_width) {
                errors.push(`${label}.layout.width exceeds its constraint`);
            }
            if (isInteger(layout.height, 1) && isInteger(constraints.min_height, 1) && layout.height < constraints.min_height) {
                errors.push(`${label}.layout.height is below its constraint`);
            }
            if (isInteger(layout.height, 1) && isInteger(constraints.max_height, 1) && layout.height > constraints.max_height) {
                errors.push(`${label}.layout.height exceeds its constraint`);
            }
            if (isInteger(layout.x, 0) && isInteger(layout.y, 0)
                && isInteger(layout.width, 1) && isInteger(layout.height, 1)) {
                const collision = placed.find((entry) => overlaps(layout, entry.layout));
                if (collision) errors.push(`${label}.layout overlaps ${collision.id}`);
                placed.push({ id: label, layout });
            }
        });
        return errors;
    }

    mount(manifest) {
        const errors = this.validate(manifest);
        if (errors.length) throw new Error(`Invalid workspace manifest: ${errors.join('; ')}`);
        const nextKey = manifestKey(manifest);
        if (nextKey === this.key) return this.summary();

        const doc = this.root.ownerDocument || document;
        const stagingRoot = doc.createElement('div');
        const nextMounted = [];
        try {
            manifest.widgets.forEach((widget) => {
                const registration = this.registry.get(widget.type);
                const instance = registration.create();
                if (!instance || typeof instance.mount !== 'function'
                    || typeof instance.onData !== 'function' || typeof instance.destroy !== 'function') {
                    throw new TypeError(`Invalid component lifecycle: ${widget.type}`);
                }
                const context = {
                    root: stagingRoot,
                    host: this,
                    bus: this.bus,
                    manifest,
                    widget,
                    slot: widget.slot,
                    publish: (source, payload) => this.bus.publish(source, payload),
                    subscribe: (source, handler) => this.bus.subscribe(source, handler),
                };
                let element;
                try {
                    element = instance.mount(context);
                } catch (error) {
                    try { instance.destroy(); } catch (_error) {}
                    throw error;
                }
                if (!element || element.nodeType !== 1 || element.parentNode !== stagingRoot) {
                    try { instance.destroy(); } catch (_error) {}
                    throw new TypeError(`Component mount must return its root element: ${widget.type}`);
                }
                element.dataset.workspaceWidgetId = widget.id;
                element.style.gridColumn = `${widget.layout.x + 1} / span ${widget.layout.width}`;
                element.style.gridRow = `${widget.layout.y + 1} / span ${widget.layout.height}`;
                nextMounted.push({ widget, instance, element, unsubscribers: [] });
            });
            nextMounted.forEach((entry) => {
                const subscriptions = [...new Set([
                    ...sourceIds(entry.widget.sources),
                    ...(entry.widget.channels || []),
                ])];
                entry.unsubscribers = subscriptions.map((source) => this.bus.subscribe(
                    source,
                    (payload) => entry.instance.onData(payload, source),
                ));
            });
        } catch (error) {
            cleanupMounted(nextMounted);
            throw error;
        }

        const previousMounted = this.mounted;
        try {
            this.root.replaceChildren(...Array.from(stagingRoot.childNodes));
            this.root.style.gridTemplateColumns = `repeat(${manifest.grid.columns}, minmax(0,1fr))`;
            this.root.style.gridTemplateRows = `repeat(${manifest.grid.rows}, minmax(0,1fr))`;
            this.root.dataset.workspaceId = manifest.id;
            this.root.dataset.workspaceRevision = String(manifest.revision);
        } catch (error) {
            cleanupMounted(nextMounted);
            throw error;
        }

        nextMounted.forEach((entry) => {
            const replaySources = [...new Set([
                ...sourceIds(entry.widget.sources),
                ...(entry.widget.channels || []),
            ])];
            replaySources.forEach((source) => {
                if (!this.bus.hasLatest?.(source)) return;
                try {
                    entry.instance.onData(this.bus.latest(source), source);
                } catch (error) {
                    console.error(`[workspace] replay failed for ${entry.widget.id} (${source}):`, error);
                }
            });
        });

        this.mounted = nextMounted;
        this.key = nextKey;
        this.workspaceId = manifest.id;
        this.revision = manifest.revision;
        this.grid = { ...manifest.grid };
        this.sources = collectManifestSources(manifest);
        this.channels = collectManifestChannels(manifest);
        cleanupMounted(previousMounted);
        return this.summary();
    }

    summary() {
        return {
            workspaceId: this.workspaceId,
            revision: this.revision,
            grid: this.grid ? { ...this.grid } : null,
            widgetIds: this.mounted.map(({ widget }) => widget.id),
            widgetTypes: this.mounted.map(({ widget }) => widget.type),
            sources: [...this.sources],
            channels: [...this.channels],
        };
    }

    destroy() {
        cleanupMounted(this.mounted.splice(0));
        this.root.replaceChildren();
        this.root.style.gridTemplateColumns = '';
        this.root.style.gridTemplateRows = '';
        delete this.root.dataset.workspaceId;
        delete this.root.dataset.workspaceRevision;
        this.key = '';
        this.workspaceId = '';
        this.revision = 0;
        this.grid = null;
        this.sources = [];
        this.channels = [];
    }
}

export function createWorkspaceHost(options) {
    return new WorkspaceHost(options);
}
