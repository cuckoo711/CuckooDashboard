import { dashboardDataBus } from './data-bus.js';

export const LEGACY_CHANNEL_BY_TYPE = Object.freeze({
    dashboard_data: 'dashboard.aggregate',
    github: 'github.contributions',
    media: 'media.playback',
    system: 'system.snapshot',
});

function requireIdentifier(value, label) {
    if (typeof value !== 'string' || !value.trim()) {
        throw new TypeError(`${label} must be a non-empty string`);
    }
    return value.trim();
}

function normalizeDeliveryInterval(value) {
    if (value === undefined || value === null) return undefined;
    const interval = Number(value);
    if (!Number.isFinite(interval) || interval <= 0) {
        throw new TypeError('deliveryIntervalMs must be a positive number');
    }
    return Math.round(interval);
}

function once(callback) {
    let active = true;
    return () => {
        if (!active) return false;
        active = false;
        callback();
        return true;
    };
}

function transportIsOpen(transport) {
    return transport && (transport.readyState === undefined || transport.readyState === 1);
}

function sendTransport(transport, message) {
    if (typeof transport === 'function') {
        transport(message);
        return true;
    }
    if (!transportIsOpen(transport) || typeof transport.send !== 'function') return false;
    transport.send(JSON.stringify(message));
    return true;
}

function wireRecord(record) {
    const output = { id: record.id, channel: record.channel };
    if (record.deliveryIntervalMs !== undefined) output.deliveryIntervalMs = record.deliveryIntervalMs;
    return output;
}

export class SubscriptionClient {
    constructor({ bus = dashboardDataBus, logger = console } = {}) {
        this.bus = bus;
        this.logger = logger;
        this.records = new Map();
        this.scopes = new Map();
        this.transport = null;
        this.directScopeSequence = 0;
        this.busUnsubscribe = bus.subscribeAll((data, channel, meta) => {
            this._routeChannel(channel, data, { ...meta, delivery: meta?.delivery || 'local' });
        });
    }

    createScope(scopeIdValue) {
        const scopeId = requireIdentifier(scopeIdValue, 'scopeId');
        const staged = new Map();
        let state = 'staged';
        const client = this;
        const scope = {
            id: scopeId,
            get size() {
                return staged.size;
            },
            subscribe(descriptor, handler) {
                if (state !== 'staged') throw new Error(`subscription scope is not staged: ${scopeId}`);
                const record = client._createRecord(scopeId, descriptor, handler);
                if (staged.has(record.id)) throw new Error(`duplicate subscription id: ${record.id}`);
                staged.set(record.id, record);
                client._replay(record);
                return once(() => {
                    if (state === 'staged') return staged.delete(record.id);
                    if (state === 'committed') return client._removeRecord(record);
                    return false;
                });
            },
            commit({ replaceScope = null } = {}) {
                if (state !== 'staged') throw new Error(`subscription scope is not staged: ${scopeId}`);
                client._commitScope(scope, staged, replaceScope);
                state = 'committed';
                return scope;
            },
            abort() {
                if (state === 'aborted') return false;
                if (state === 'committed') client._removeScope(scope);
                staged.forEach((record) => { record.active = false; });
                staged.clear();
                state = 'aborted';
                return true;
            },
            dispose() {
                return scope.abort();
            },
            _deactivate() {
                state = 'aborted';
                staged.forEach((record) => { record.active = false; });
                staged.clear();
            },
        };
        return Object.freeze(scope);
    }

    subscribe(descriptor, handler) {
        const scope = this.createScope(`direct:${++this.directScopeSequence}`);
        const unsubscribe = scope.subscribe(descriptor, handler);
        scope.commit();
        return once(() => {
            unsubscribe();
            scope.dispose();
        });
    }

    _createRecord(scopeId, descriptor, handler) {
        if (!descriptor || typeof descriptor !== 'object') {
            throw new TypeError('subscription descriptor must be an object');
        }
        if (typeof handler !== 'function') throw new TypeError('subscription handler must be a function');
        return {
            id: requireIdentifier(descriptor.id, 'subscription id'),
            channel: requireIdentifier(descriptor.channel, 'subscription channel'),
            deliveryIntervalMs: normalizeDeliveryInterval(descriptor.deliveryIntervalMs),
            handler,
            scopeId,
            active: true,
        };
    }

    _commitScope(scope, staged, replaceScope) {
        const replacedId = replaceScope?.id || null;
        staged.forEach((record) => {
            const existing = this.records.get(record.id);
            if (existing && existing.scopeId !== replacedId) {
                throw new Error(`subscription id already active: ${record.id}`);
            }
        });
        const next = new Map();
        this.records.forEach((record, id) => {
            if (record.scopeId !== replacedId) next.set(id, record);
            else record.active = false;
        });
        staged.forEach((record, id) => next.set(id, record));
        this.records = next;
        if (replaceScope) {
            this.scopes.delete(replaceScope.id);
            replaceScope._deactivate?.();
        }
        this.scopes.set(scope.id, scope);
        this.sendReplace();
    }

    _removeRecord(record) {
        if (!record.active || this.records.get(record.id) !== record) return false;
        record.active = false;
        this.records.delete(record.id);
        this.sendReplace();
        return true;
    }

    _removeScope(scope) {
        let changed = false;
        this.records.forEach((record, id) => {
            if (record.scopeId !== scope.id) return;
            record.active = false;
            this.records.delete(id);
            changed = true;
        });
        this.scopes.delete(scope.id);
        if (changed) this.sendReplace();
        return changed;
    }

    _replay(record) {
        const latest = this.bus.snapshot(record.channel);
        if (!latest) return;
        this._deliver(record, latest.data, {
            ...latest.meta,
            channel: record.channel,
            subscriptionId: record.id,
            replay: true,
        });
    }

    _deliver(record, data, meta = {}) {
        if (!record.active) return false;
        try {
            record.handler(data, Object.freeze({
                ...meta,
                channel: record.channel,
                subscriptionId: record.id,
            }));
        } catch (error) {
            this.logger.error?.(`[subscriptions] handler failed for ${record.id}:`, error);
        }
        return true;
    }

    _routeChannel(channel, data, meta = {}) {
        let delivered = 0;
        this.records.forEach((record) => {
            if (record.channel === channel && this._deliver(record, data, meta)) delivered += 1;
        });
        return delivered;
    }

    publish(channel, data, meta = {}) {
        return this.bus.publish(channel, data, meta);
    }

    routeLegacy(channelValue, data, meta = {}) {
        const channel = requireIdentifier(channelValue, 'legacy channel');
        return this.publish(channel, data, { ...meta, channel, delivery: meta.delivery || 'legacy' });
    }

    routeLegacyEnvelope(message) {
        if (!message || typeof message !== 'object') return 0;
        if (message.type === 'workspace_source') {
            if (!message.source_id) return 0;
            return this.routeLegacy(message.source_id, message.data, { legacyType: message.type });
        }
        const channel = LEGACY_CHANNEL_BY_TYPE[message.type];
        if (!channel) return 0;
        return this.routeLegacy(channel, message.data, { legacyType: message.type });
    }

    routeSnapshot(message) {
        if (!message || message.type !== 'data.snapshot') return false;
        const subscriptionId = requireIdentifier(message.subscriptionId, 'subscriptionId');
        const record = this.records.get(subscriptionId);
        if (!record) return false;
        const channel = message.channel ? requireIdentifier(message.channel, 'snapshot channel') : record.channel;
        if (channel !== record.channel) return false;
        const meta = {
            ...(message.meta || {}),
            v: message.v,
            channel,
            subscriptionId,
            sequence: message.sequence,
            timestamp: message.timestamp,
            delivery: 'subscription',
        };
        this.bus.cache(channel, message.data, meta);
        return this._deliver(record, message.data, meta);
    }

    subscriptions() {
        return [...this.records.values()].map(wireRecord);
    }

    attach(transport, { replay = true } = {}) {
        this.transport = transport || null;
        if (replay) this.sendReplace();
        return once(() => this.detach(transport));
    }

    detach(transport = this.transport) {
        if (transport && this.transport !== transport) return false;
        this.transport = null;
        return true;
    }

    send(message) {
        try {
            return sendTransport(this.transport, message);
        } catch (error) {
            this.logger.error?.('[subscriptions] transport send failed:', error);
            return false;
        }
    }

    sendReplace() {
        return this.send({
            type: 'subscribe',
            replace: true,
            subscriptions: this.subscriptions(),
        });
    }

    clear() {
        this.records.forEach((record) => { record.active = false; });
        this.records.clear();
        this.scopes.forEach((scope) => scope._deactivate?.());
        this.scopes.clear();
        this.sendReplace();
    }

    destroy() {
        this.clear();
        this.detach();
        this.busUnsubscribe?.();
        this.busUnsubscribe = null;
    }
}

export function createSubscriptionClient(options) {
    return new SubscriptionClient(options);
}

export const dashboardSubscriptionClient = createSubscriptionClient({ bus: dashboardDataBus });
