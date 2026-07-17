function sourceId(source) {
    if (typeof source === 'string') return source.trim();
    if (!source || typeof source !== 'object') return '';
    return String(source.id || source.source || source.name || '').trim();
}

export function widgetChannels(widget = {}) {
    const allowed = new Set();
    const sourceValues = (value) => (Array.isArray(value) ? value : Object.values(value || {}));
    const sources = [
        ...sourceValues(widget.sources),
        ...sourceValues(widget.data_sources),
    ];
    sources.map(sourceId).filter(Boolean).forEach((channel) => allowed.add(channel));
    (widget.channels || []).forEach((channel) => {
        if (typeof channel === 'string' && channel.trim()) allowed.add(channel.trim());
    });
    return [...allowed];
}

function requireIdentifier(value, label) {
    if (typeof value !== 'string' || !value.trim()) {
        throw new TypeError(`${label} must be a non-empty string`);
    }
    return value.trim();
}

function encoded(value) {
    return encodeURIComponent(String(value)).replaceAll('%', '_');
}

export function stableSubscriptionId(workspaceId, instanceId, channel, ordinal = 0) {
    return `widget:${encoded(workspaceId)}:${encoded(instanceId)}:${encoded(channel)}:${ordinal}`;
}

function widgetInterval(widget, channel, options = {}) {
    const explicit = options.deliveryIntervalMs ?? options.delivery_interval_ms;
    if (explicit !== undefined) return explicit;
    const policy = widget.refresh_policy || widget.refreshPolicy || {};
    const channelPolicy = policy.channels?.[channel] || policy.sources?.[channel] || {};
    return channelPolicy.deliveryIntervalMs
        ?? channelPolicy.delivery_interval_ms
        ?? policy.deliveryIntervalMs
        ?? policy.delivery_interval_ms;
}

function createLogger(base, workspaceId, instanceId) {
    const target = base || console;
    const prefix = `[widget ${workspaceId}/${instanceId}]`;
    return Object.freeze({
        debug: (...args) => target.debug?.(prefix, ...args),
        info: (...args) => target.info?.(prefix, ...args),
        warn: (...args) => target.warn?.(prefix, ...args),
        error: (...args) => target.error?.(prefix, ...args),
    });
}

export function normalizeWidgetLifecycle(instance, type = 'widget') {
    if (!instance || typeof instance !== 'object') {
        throw new TypeError(`Invalid component lifecycle: ${type}`);
    }
    if (typeof instance.mount !== 'function' || typeof instance.destroy !== 'function') {
        throw new TypeError(`Invalid component lifecycle: ${type}`);
    }
    const update = typeof instance.update === 'function'
        ? instance.update.bind(instance)
        : (typeof instance.onData === 'function'
            ? (data, meta = {}) => instance.onData(data, meta.channel, meta)
            : null);
    if (!update) throw new TypeError(`Invalid component lifecycle: ${type}`);
    const noop = () => {};
    return Object.freeze({
        mount: instance.mount.bind(instance),
        update,
        destroy: instance.destroy.bind(instance),
        resize: typeof instance.resize === 'function' ? instance.resize.bind(instance) : noop,
        configure: typeof instance.configure === 'function' ? instance.configure.bind(instance) : noop,
        pause: typeof instance.pause === 'function' ? instance.pause.bind(instance) : noop,
        resume: typeof instance.resume === 'function' ? instance.resume.bind(instance) : noop,
    });
}

export function createWidgetContext({
    workspaceId: workspaceIdValue,
    instanceId: instanceIdValue,
    container,
    config = {},
    manifest,
    widget,
    slot,
    host,
    bus,
    subscriptionScope,
    subscriptionClient,
    abortController,
    logger = console,
}) {
    const workspaceId = requireIdentifier(workspaceIdValue, 'workspaceId');
    const instanceId = requireIdentifier(instanceIdValue, 'instanceId');
    if (!container || !subscriptionScope || !subscriptionClient || !abortController) {
        throw new TypeError('Widget context requires container, subscriptions, and abort controller');
    }
    const allowed = new Set(widgetChannels(widget));
    const ordinals = new Map();
    let subscriptionCount = 0;

    function subscribe(channelOrDescriptor, handler, options = {}) {
        const descriptor = typeof channelOrDescriptor === 'string'
            ? { channel: channelOrDescriptor }
            : { ...(channelOrDescriptor || {}) };
        const channel = requireIdentifier(descriptor.channel, 'subscription channel');
        if (!allowed.has(channel)) {
            throw new Error(`widget ${instanceId} cannot subscribe to undeclared channel: ${channel}`);
        }
        if (typeof handler !== 'function') throw new TypeError('context.subscribe requires a handler');
        const ordinal = ordinals.get(channel) || 0;
        ordinals.set(channel, ordinal + 1);
        subscriptionCount += 1;
        const unsubscribe = subscriptionScope.subscribe({
            id: descriptor.id || stableSubscriptionId(workspaceId, instanceId, channel, ordinal),
            channel,
            deliveryIntervalMs: descriptor.deliveryIntervalMs
                ?? descriptor.delivery_interval_ms
                ?? widgetInterval(widget, channel, options),
        }, handler);
        abortController.signal.addEventListener('abort', unsubscribe, { once: true });
        return unsubscribe;
    }

    const publish = (channel, data, meta = {}) => subscriptionClient.publish(channel, data, {
        ...meta,
        publisher: { workspaceId, instanceId },
    });
    const context = Object.freeze({
        workspaceId,
        instanceId,
        container,
        root: container,
        config: Object.freeze({ ...(config || {}) }),
        subscribe,
        publish,
        emit: publish,
        logger: createLogger(logger, workspaceId, instanceId),
        abortSignal: abortController.signal,
        host,
        bus,
        manifest,
        widget,
        slot,
    });
    return Object.freeze({
        context,
        subscriptionCount: () => subscriptionCount,
        allowedChannels: Object.freeze([...allowed]),
    });
}
