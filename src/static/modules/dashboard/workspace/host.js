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
        sources: sourceIds(manifest.sources),
        widgets: (manifest.widgets || []).map((widget) => ({
            id: widget.id,
            type: widget.type,
            slot: widget.slot,
            sources: sourceIds(widget.sources),
            channels: widget.channels || [],
        })),
    });
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
        this.sources = [];
        this.channels = [];
    }

    validate(manifest) {
        const errors = [];
        if (!manifest || typeof manifest !== 'object') return ['manifest must be an object'];
        if (!Array.isArray(manifest.widgets)) errors.push('manifest.widgets must be an array');
        const ids = new Set();
        const singletonTypes = new Set();
        (manifest.widgets || []).forEach((widget, index) => {
            const label = widget?.id || `widgets[${index}]`;
            if (!widget || typeof widget !== 'object') {
                errors.push(`${label} must be an object`);
                return;
            }
            if (!widget.id) errors.push(`${label} is missing id`);
            else if (ids.has(widget.id)) errors.push(`duplicate widget id: ${widget.id}`);
            else ids.add(widget.id);
            if (!widget.type || !this.registry.has(widget.type)) errors.push(`unknown widget type: ${widget.type || '(empty)'}`);
            const registration = widget.type ? this.registry.get(widget.type) : null;
            if (registration?.singleInstance) {
                if (singletonTypes.has(widget.type)) errors.push(`single-instance widget repeated: ${widget.type}`);
                singletonTypes.add(widget.type);
            }
            if (!widget.slot) errors.push(`${label} is missing slot`);
            if (!Array.isArray(widget.sources)) errors.push(`${label}.sources must be an array`);
            if (!Array.isArray(widget.channels)) errors.push(`${label}.channels must be an array`);
        });
        return errors;
    }

    mount(manifest) {
        const errors = this.validate(manifest);
        if (errors.length) throw new Error(`Invalid workspace manifest: ${errors.join('; ')}`);
        const nextKey = manifestKey(manifest);
        if (nextKey === this.key && this.mounted.length) return this.summary();
        this.destroy();
        try {
            manifest.widgets.forEach((widget) => {
                const registration = this.registry.get(widget.type);
                const instance = registration.create();
                if (!instance || typeof instance.mount !== 'function'
                    || typeof instance.onData !== 'function' || typeof instance.destroy !== 'function') {
                    throw new TypeError(`Invalid component lifecycle: ${widget.type}`);
                }
                const context = {
                    root: this.root,
                    host: this,
                    bus: this.bus,
                    manifest,
                    widget,
                    slot: widget.slot,
                    publish: (source, payload) => this.bus.publish(source, payload),
                    subscribe: (source, handler) => this.bus.subscribe(source, handler),
                };
                instance.mount(context);
                const unsubscribers = sourceIds(widget.sources).map((source) => this.bus.subscribe(
                    source,
                    (payload) => instance.onData(payload, source),
                ));
                this.mounted.push({ widget, instance, unsubscribers });
            });
            this.key = nextKey;
            this.sources = collectManifestSources(manifest);
            this.channels = collectManifestChannels(manifest);
            return this.summary();
        } catch (error) {
            this.destroy();
            throw error;
        }
    }

    summary() {
        return {
            widgetIds: this.mounted.map(({ widget }) => widget.id),
            sources: [...this.sources],
            channels: [...this.channels],
        };
    }

    destroy() {
        this.mounted.splice(0).reverse().forEach(({ widget, instance, unsubscribers }) => {
            unsubscribers.forEach((unsubscribe) => {
                try { unsubscribe(); } catch (_error) {}
            });
            try {
                instance.destroy();
            } catch (error) {
                console.error(`[workspace] destroy failed for ${widget.id}:`, error);
            }
        });
        this.key = '';
        this.sources = [];
        this.channels = [];
    }
}

export function createWorkspaceHost(options) {
    return new WorkspaceHost(options);
}
