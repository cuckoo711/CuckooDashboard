export class DataBus {
    constructor() {
        this.subscriptions = new Map();
    }

    subscribe(source, handler) {
        if (typeof source !== 'string' || !source || typeof handler !== 'function') {
            throw new TypeError('DataBus.subscribe requires a source and handler');
        }
        let handlers = this.subscriptions.get(source);
        if (!handlers) {
            handlers = new Set();
            this.subscriptions.set(source, handlers);
        }
        handlers.add(handler);
        return () => this.unsubscribe(source, handler);
    }

    publish(source, payload) {
        const handlers = this.subscriptions.get(source);
        if (!handlers) return 0;
        const subscribers = [...handlers];
        subscribers.forEach((handler) => {
            try {
                handler(payload, source);
            } catch (error) {
                console.error(`[data-bus] subscriber failed for ${source}:`, error);
            }
        });
        return subscribers.length;
    }

    unsubscribe(source, handler) {
        const handlers = this.subscriptions.get(source);
        if (!handlers) return false;
        const removed = handlers.delete(handler);
        if (!handlers.size) this.subscriptions.delete(source);
        return removed;
    }

    clear() {
        this.subscriptions.clear();
    }
}

export function createDataBus() {
    return new DataBus();
}

export const dashboardDataBus = createDataBus();
