import assert from 'node:assert/strict';
import test from 'node:test';

import {
    createWidgetContext,
    normalizeWidgetLifecycle,
    stableSubscriptionId,
    widgetChannels,
} from '../static/modules/dashboard/workspace/widget-sdk.js';

test('legacy onData lifecycle is normalized to update with channel metadata', () => {
    const calls = [];
    const lifecycle = normalizeWidgetLifecycle({
        mount(context) { return context.container; },
        onData(data, source, meta) { calls.push([data.value, source, meta.sequence]); },
        destroy() { calls.push(['destroy']); },
    }, 'legacy.widget');

    assert.equal(lifecycle.mount({ container: 'root' }), 'root');
    lifecycle.update({ value: 3 }, { channel: 'system.snapshot', sequence: 9 });
    lifecycle.resize();
    lifecycle.configure();
    lifecycle.pause();
    lifecycle.resume();
    lifecycle.destroy();
    assert.deepEqual(calls, [[3, 'system.snapshot', 9], ['destroy']]);
});

test('widget context restricts subscriptions and produces stable ids', () => {
    const records = [];
    const unsubscribed = [];
    const scope = {
        subscribe(descriptor, handler) {
            records.push([descriptor, handler]);
            let active = true;
            return () => {
                if (!active) return false;
                active = false;
                unsubscribed.push(descriptor.id);
                return true;
            };
        },
    };
    const published = [];
    const controller = new AbortController();
    const container = {};
    const widget = {
        id: 'player',
        sources: ['media.playback'],
        channels: ['media.lyric'],
        refresh_policy: { delivery_interval_ms: 250 },
    };
    const state = createWidgetContext({
        workspaceId: 'main',
        instanceId: 'player',
        container,
        config: { compact: true },
        manifest: { id: 'main' },
        widget,
        slot: 'main',
        host: {},
        bus: {},
        subscriptionScope: scope,
        subscriptionClient: { publish: (...args) => published.push(args) },
        abortController: controller,
        logger: { debug() {}, info() {}, warn() {}, error() {} },
    });

    assert.equal(state.context.container, container);
    assert.equal(state.context.root, container);
    assert.deepEqual(widgetChannels(widget), ['media.playback', 'media.lyric']);
    state.context.subscribe('media.playback', () => {});
    state.context.subscribe({ channel: 'media.playback', deliveryIntervalMs: 500 }, () => {});
    assert.deepEqual(records.map(([record]) => record), [
        {
            id: stableSubscriptionId('main', 'player', 'media.playback', 0),
            channel: 'media.playback',
            deliveryIntervalMs: 250,
        },
        {
            id: stableSubscriptionId('main', 'player', 'media.playback', 1),
            channel: 'media.playback',
            deliveryIntervalMs: 500,
        },
    ]);
    assert.throws(
        () => state.context.subscribe('system.snapshot', () => {}),
        /undeclared channel/,
    );
    state.context.emit('widget.event', { ok: true });
    assert.deepEqual(published[0][0], 'widget.event');
    assert.deepEqual(published[0][2].publisher, { workspaceId: 'main', instanceId: 'player' });
    controller.abort();
    assert.equal(unsubscribed.length, 2);
});
