import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../static/settings/modules/grid-layout.js', import.meta.url), 'utf8');
const layout = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const grid = {columns: 16, rows: 15};

test('clamp and constrain enforce grid and widget limits', () => {
    assert.equal(layout.clamp(12, 0, 8), 8);
    assert.deepEqual(
        layout.constrain(
            {x: 15, y: -2, w: 1, h: 20},
            grid,
            {min_width: 2, min_height: 3, max_width: 8, max_height: 9},
        ),
        {x: 14, y: 0, w: 2, h: 9},
    );
});

test('collision uses rectangle overlap and permits touching edges', () => {
    assert.equal(layout.collision({x: 0, y: 0, w: 4, h: 4}, {x: 3, y: 2, w: 2, h: 2}), true);
    assert.equal(layout.collision({x: 0, y: 0, w: 4, h: 4}, {x: 4, y: 0, w: 2, h: 2}), false);
});

test('validate rejects overlaps, duplicates, bounds and constraints', () => {
    const valid = layout.validate([
        {id: 'one', type: 'a', x: 0, y: 0, w: 4, h: 4},
        {id: 'two', type: 'b', layout: {x: 4, y: 0, width: 3, height: 3}},
    ], grid);
    assert.equal(valid.valid, true);

    const invalid = layout.validate([
        {id: 'same', type: 'a', x: 0, y: 0, w: 3, h: 3},
        {id: 'same', type: 'b', x: 6, y: 0, w: 3, h: 3},
        {id: 'overlap', type: 'b', x: 2, y: 2, w: 3, h: 3},
        {id: 'outside', type: 'c', x: 15, y: 14, w: 2, h: 2},
        {id: 'small', type: 'd', x: 10, y: 0, w: 1, h: 1, constraints: {min_width: 2, min_height: 2}},
    ], grid);
    assert.equal(invalid.valid, false);
    assert.match(invalid.errors.join('\n'), /重复/);
    assert.match(invalid.errors.join('\n'), /重叠/);
    assert.match(invalid.errors.join('\n'), /超出网格或尺寸约束/);
});

test('firstFit scans rows without moving existing widgets', () => {
    const items = [
        {id: 'one', x: 0, y: 0, w: 8, h: 3},
        {id: 'two', x: 8, y: 0, w: 8, h: 3},
        {id: 'three', x: 0, y: 3, w: 4, h: 3},
    ];
    const snapshot = structuredClone(items);
    assert.deepEqual(layout.firstFit(items, {w: 4, h: 3}, grid), {x: 4, y: 3, w: 4, h: 3});
    assert.deepEqual(items, snapshot);
    assert.equal(layout.firstFit([{id: 'all', x: 0, y: 0, w: 16, h: 15}], {w: 1, h: 1}, grid), null);
});
