import test from 'node:test';
import assert from 'node:assert/strict';

import {loadRuntimeExtensions} from '../static/modules/dashboard/workspace/extension-loader.js';
import {createComponentRegistry} from '../static/modules/dashboard/workspace/registry.js';

const EXTENSION_ID = 'com.example.widget';
const WIDGET_TYPE = 'com.example.widget.card';

function catalog(moduleUrl = `/runtime/extensions/${EXTENSION_ID}/assets/index.js`) {
    return {
        api_version: 1,
        extensions: [{
            id: EXTENSION_ID,
            version: '1.0.0',
            module_url: moduleUrl,
            widget_types: [WIDGET_TYPE],
        }],
    };
}

test('runtime extension loader registers core and same-origin extension atomically', async () => {
    const registry = createComponentRegistry();
    const summary = await loadRuntimeExtensions({
        registry,
        fetchCatalog: async () => catalog(),
        importModule: async () => ({
            registerCuckooExtension(api) {
                api.registerWidget(WIDGET_TYPE, {
                    create: () => ({mount() {}, onData() {}, destroy() {}}),
                    singleInstance: true,
                });
            },
        }),
    });

    assert.deepEqual(summary.failed, []);
    assert.ok(summary.loaded.includes('cuckoo.core.dashboard'));
    assert.ok(summary.loaded.includes(EXTENSION_ID));
    assert.equal(registry.ownerOf(WIDGET_TYPE), EXTENSION_ID);
    assert.equal(registry.get(WIDGET_TYPE).singleInstance, true);
    const legacyInstance = registry.get(WIDGET_TYPE).create();
    assert.equal(typeof legacyInstance.update, 'function');
    assert.equal(typeof legacyInstance.resize, 'function');
});

test('failed or cross-origin extensions never leave partial registrations', async () => {
    const registry = createComponentRegistry();
    const summary = await loadRuntimeExtensions({
        registry,
        fetchCatalog: async () => catalog(),
        importModule: async () => ({
            registerCuckooExtension(api) {
                api.registerWidget(WIDGET_TYPE, {create() { return {}; }});
                api.registerWidget('com.example.widget.undeclared', {create() { return {}; }});
            },
        }),
    });

    assert.equal(summary.failed.length, 1);
    assert.equal(registry.has(WIDGET_TYPE), false);
    assert.match(registry.unavailable(WIDGET_TYPE), /undeclared/);

    const crossOriginRegistry = createComponentRegistry();
    const crossOrigin = await loadRuntimeExtensions({
        registry: crossOriginRegistry,
        fetchCatalog: async () => catalog('https://evil.example/plugin.js'),
        importModule: async () => {
            throw new Error('cross-origin module should not be imported');
        },
    });
    assert.equal(crossOrigin.failed.length, 1);
    assert.match(crossOrigin.failed[0].reason, /same-origin/);
    assert.equal(crossOriginRegistry.has(WIDGET_TYPE), false);
});

test('catalog failure keeps the core package available', async () => {
    const registry = createComponentRegistry();
    const summary = await loadRuntimeExtensions({
        registry,
        fetchCatalog: async () => {
            throw new Error('offline');
        },
    });

    assert.equal(summary.catalogError, 'offline');
    assert.equal(registry.has('builtin.dashboard.system-info'), true);
    assert.equal(registry.has(WIDGET_TYPE), false);
});
