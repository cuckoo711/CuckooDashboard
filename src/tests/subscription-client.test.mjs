import assert from 'node:assert/strict';
import test from 'node:test';

import { DataBus } from '../static/modules/dashboard/workspace/data-bus.js';
import { SubscriptionClient } from '../static/modules/dashboard/workspace/subscription-client.js';

test('subscription client sends replace records and routes snapshots by subscription id', () => {
    const bus = new DataBus();
    const sent = [];
    const client = new SubscriptionClient({ bus, logger: { error() {} } });
    client.attach((message) => sent.push(message));

    const firstSeen = [];
    const secondSeen = [];
    const scope = client.createScope('workspace:main');
    scope.subscribe(
        { id: 'widget:main:first:system:0', channel: 'system.snapshot', deliveryIntervalMs: 1000 },
        (data, meta) => firstSeen.push([data.value, meta.subscriptionId, meta.delivery]),
    );
    scope.subscribe(
        { id: 'widget:main:second:system:0', channel: 'system.snapshot' },
        (data, meta) => secondSeen.push([data.value, meta.subscriptionId, meta.delivery]),
    );
    scope.commit();

    assert.deepEqual(sent.at(-1), {
        type: 'subscribe',
        replace: true,
        subscriptions: [
            { id: 'widget:main:first:system:0', channel: 'system.snapshot', deliveryIntervalMs: 1000 },
            { id: 'widget:main:second:system:0', channel: 'system.snapshot' },
        ],
    });

    assert.equal(client.routeSnapshot({
        v: 1,
        type: 'data.snapshot',
        subscriptionId: 'widget:main:first:system:0',
        channel: 'system.snapshot',
        sequence: 7,
        timestamp: 1234,
        data: { value: 1 },
        meta: { sampled: true },
    }), true);
    assert.deepEqual(firstSeen, [[1, 'widget:main:first:system:0', 'subscription']]);
    assert.deepEqual(secondSeen, []);
    assert.equal(bus.latest('system.snapshot').value, 1);

    client.routeLegacy('system.snapshot', { value: 2 });
    assert.deepEqual(firstSeen.at(-1), [2, 'widget:main:first:system:0', 'legacy']);
    assert.deepEqual(secondSeen.at(-1), [2, 'widget:main:second:system:0', 'legacy']);
    client.destroy();
});

test('scoped replacement replays latest data and unsubscribe is idempotent', () => {
    const bus = new DataBus();
    const sent = [];
    const client = new SubscriptionClient({ bus, logger: { error() {} } });
    client.attach((message) => sent.push(message));
    client.routeLegacy('media.playback', { title: 'before' });

    const seen = [];
    const first = client.createScope('workspace:main');
    const unsubscribe = first.subscribe(
        { id: 'widget:main:player:media:0', channel: 'media.playback' },
        (data, meta) => seen.push([data.title, meta.replay === true]),
    );
    assert.deepEqual(seen, [['before', true]]);
    first.commit();
    assert.equal(unsubscribe(), true);
    assert.equal(unsubscribe(), false);
    assert.deepEqual(client.subscriptions(), []);

    const active = client.createScope('workspace:main');
    active.subscribe(
        { id: 'widget:main:player:media:0', channel: 'media.playback' },
        (data) => seen.push([data.title, false]),
    );
    active.commit({ replaceScope: first });
    assert.deepEqual(client.subscriptions(), [
        { id: 'widget:main:player:media:0', channel: 'media.playback' },
    ]);

    const replacement = client.createScope('workspace:main');
    replacement.subscribe(
        { id: 'widget:main:player:lyric:0', channel: 'media.lyric' },
        () => {},
    );
    replacement.commit({ replaceScope: active });
    assert.deepEqual(client.subscriptions(), [
        { id: 'widget:main:player:lyric:0', channel: 'media.lyric' },
    ]);
    assert.equal(sent.at(-1).replace, true);
    replacement.dispose();
    assert.deepEqual(client.subscriptions(), []);
    client.destroy();
});
