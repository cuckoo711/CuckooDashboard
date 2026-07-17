function requireChannel(value) {
    if (typeof value !== 'string' || !value.trim()) {
        throw new TypeError('DataBus channel must be a non-empty string');
    }
    return value.trim();
}

function idempotent(callback) {
    let active = true;
    return () => {
        if (!active) return false;
        active = false;
        callback();
        return true;
    };
}

export class DataBus {
    constructor() {
        this.subscriptions = new Map();
        this.observers = new Set();
        this.latestPayloads = new Map();
    }

    subscribe(channelValue, handler, { replayLatest = false, signal } = {}) {
        const channel = requireChannel(channelValue);
        if (typeof handler !== 'function') {
            throw new TypeError('DataBus.subscribe requires a handler');
        }
        if (signal?.aborted) return () => false;
        let handlers = this.subscriptions.get(channel);
        if (!handlers) {
            handlers = new Set();
            this.subscriptions.set(channel, handlers);
        }
        handlers.add(handler);
        const unsubscribe = idempotent(() => this.unsubscribe(channel, handler));
        signal?.addEventListener('abort', unsubscribe, { once: true });
        if (replayLatest && this.hasLatest(channel)) {
            const latest = this.snapshot(channel);
            handler(latest.data, channel, latest.meta);
        }
        return unsubscribe;
    }

    subscribeAll(handler, { signal } = {}) {
        if (typeof handler !== 'function') throw new TypeError('DataBus.subscribeAll requires a handler');
        if (signal?.aborted) return () => false;
        this.observers.add(handler);
        const unsubscribe = idempotent(() => this.observers.delete(handler));
        signal?.addEventListener('abort', unsubscribe, { once: true });
        return unsubscribe;
    }

    cache(channelValue, data, meta = {}) {
        const channel = requireChannel(channelValue);
        const snapshot = Object.freeze({ data, meta: Object.freeze({ ...meta, channel }) });
        this.latestPayloads.set(channel, snapshot);
        return snapshot;
    }

    publish(channelValue, data, meta = {}) {
        const channel = requireChannel(channelValue);
        const snapshot = this.cache(channel, data, meta);
        const handlers = [...(this.subscriptions.get(channel) || [])];
        const observers = [...this.observers];
        handlers.forEach((handler) => {
            try {
                handler(data, channel, snapshot.meta);
            } catch (error) {
                console.error(`[data-bus] subscriber failed for ${channel}:`, error);
            }
        });
        observers.forEach((handler) => {
            try {
                handler(data, channel, snapshot.meta);
            } catch (error) {
                console.error(`[data-bus] observer failed for ${channel}:`, error);
            }
        });
        return handlers.length;
    }

    hasLatest(channel) {
        return this.latestPayloads.has(channel);
    }

    latest(channel) {
        return this.latestPayloads.get(channel)?.data;
    }

    latestMeta(channel) {
        return this.latestPayloads.get(channel)?.meta;
    }

    snapshot(channel) {
        return this.latestPayloads.get(channel) || null;
    }

    unsubscribe(channel, handler) {
        const handlers = this.subscriptions.get(channel);
        if (!handlers) return false;
        const removed = handlers.delete(handler);
        if (!handlers.size) this.subscriptions.delete(channel);
        return removed;
    }

    clear() {
        this.subscriptions.clear();
        this.observers.clear();
        this.latestPayloads.clear();
    }
}

export function createDataBus() {
    return new DataBus();
}

export const dashboardDataBus = createDataBus();
