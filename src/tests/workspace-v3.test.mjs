import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';

async function importStandalone(path) {
    const source = await readFile(new URL(`../static/${path}`, import.meta.url), 'utf8');
    return import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
}

const grid = await importStandalone('settings/modules/grid-layout.js');
const viewport = await importStandalone('modules/dashboard/workspace/viewport-calibration.js');

const rects = (items) => items.map(({id, x, y, w, h}) => ({id, x, y, w, h}));

test('recommendGrid derives logical dimensions from target cell calibration', () => {
    const standard = grid.recommendGrid({width: 1920, height: 1080}, 'normal');
    const portrait = grid.recommendGrid({width: 1080, height: 1920}, 'normal');
    const ultrawide = grid.recommendGrid({width: 3440, height: 1440}, 'normal');
    assert.deepEqual(standard, {columns: 16, rows: 15});
    assert.deepEqual(portrait, {columns: 9, rows: 27});
    assert.deepEqual(ultrawide, {columns: 29, rows: 20});
});

test('reflow is stable, collision-free, and preserves legal rectangles', () => {
    const items = [
        {id: 'z', type: 'card', x: 5, y: 0, w: 2, h: 2},
        {id: 'a', type: 'card', x: 0, y: 0, w: 2, h: 2},
    ];
    const result = grid.reflow(items, {columns: 8, rows: 8}, {columns: 8, rows: 8}, {});
    assert.equal(result.ok, true);
    assert.deepEqual(rects(result.items), rects([
        {id: 'a', type: 'card', x: 0, y: 0, w: 2, h: 2},
        {id: 'z', type: 'card', x: 5, y: 0, w: 2, h: 2},
    ]));
    assert.deepEqual(items[0].x, 5);
});

test('reflow failure returns an atomic unsuccessful result', () => {
    const items = [{id: 'large', type: 'card', x: 0, y: 0, w: 4, h: 4}];
    const result = grid.reflow(items, {columns: 8, rows: 8}, {columns: 4, rows: 4}, {card: {min_width: 5, min_height: 5}});
    assert.equal(result.ok, false);
    assert.deepEqual(rects(result.items), rects(items));
    assert.deepEqual(items[0].x, 0);
});

test('viewport calibration computes contain/fill surfaces and offsets', () => {
    const contain = viewport.measureViewport({width: 1600, height: 900}, {columns: 16, rows: 15}, {
        width: 1920, height: 1080, fit: 'contain', density: 'normal', offset: {x: 4, y: -2},
    });
    assert.equal(contain.surface.width, 1600);
    assert.equal(contain.surface.height, 900);
    assert.equal(contain.offset.x, 4);
    assert.equal(contain.offset.y, -2);
    assert.deepEqual(contain.gutter, {x: 0, y: 0});

    const ultrawide = viewport.measureViewport({width: 3440, height: 1440}, {columns: 16, rows: 15}, {
        reference_width: 1920, reference_height: 1080, fit_mode: 'contain', density: 'normal',
    });
    assert.equal(ultrawide.surface.width, 2560);
    assert.deepEqual(ultrawide.gutter, {x: 440, y: 0});
    assert.deepEqual(ultrawide.offset, {x: 0, y: 0});

    const fill = viewport.measureViewport({width: 1600, height: 900}, {columns: 20, rows: 10}, {
        width: 1920, height: 1080, fit: 'fill', density: 'compact',
    });
    assert.equal(fill.surface.width, 1600);
    assert.equal(fill.surface.height, 900);
    assert.equal(fill.grid.columns, 20);
    assert.equal(fill.density, 'compact');
});

test('static contracts are v3-only and resize-aware', async () => {
    const host = await readFile(new URL('../static/modules/dashboard/workspace/host.js', import.meta.url), 'utf8');
    const manifest = await readFile(new URL('../static/modules/dashboard/workspace/default-manifest.js', import.meta.url), 'utf8');
    const css = await readFile(new URL('../static/dashboard.css', import.meta.url), 'utf8');
    assert.match(host, /manifest\.version !== 3/);
    assert.doesNotMatch(host, /must be 16x15/);
    assert.match(host, /workspace-surface/);
    assert.match(host, /instance\.resize\(result\)/);
    assert.match(manifest, /version: 3/);
    assert.match(manifest, /calibration:/);
    assert.doesNotMatch(css, /grid-template-columns:repeat\(16/);
    assert.doesNotMatch(css, /grid-template-rows:repeat\(15/);
});
