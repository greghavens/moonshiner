import { test } from 'node:test';
import assert from 'node:assert/strict';
import { diff, apply } from './diff.ts';

test('deep-equal inputs produce an empty diff', () => {
  const a = { user: { name: 'ada', tags: ['x', 'y'] }, n: 1 };
  const b = { user: { name: 'ada', tags: ['x', 'y'] }, n: 1 };
  assert.deepEqual(diff(a, b), []);
});

test('a replaced leaf becomes one change op with from and to', () => {
  assert.deepEqual(diff({ theme: 'light' }, { theme: 'dark' }), [
    { op: 'change', path: ['theme'], from: 'light', to: 'dark' },
  ]);
});

test('a key only in after becomes an add op carrying the value', () => {
  assert.deepEqual(diff({ a: 1 }, { a: 1, b: 2 }), [
    { op: 'add', path: ['b'], value: 2 },
  ]);
});

test('a key only in before becomes a remove op', () => {
  assert.deepEqual(diff({ a: 1, b: 2 }, { a: 1 }), [
    { op: 'remove', path: ['b'] },
  ]);
});

test('changes nested in objects carry the full path', () => {
  assert.deepEqual(diff({ user: { name: 'ada' } }, { user: { name: 'bo' } }), [
    { op: 'change', path: ['user', 'name'], from: 'ada', to: 'bo' },
  ]);
});

test('object keys are visited in sorted order over the union of keys', () => {
  assert.deepEqual(diff({ a: 1, c: 3 }, { b: 2, c: 4 }), [
    { op: 'remove', path: ['a'] },
    { op: 'add', path: ['b'], value: 2 },
    { op: 'change', path: ['c'], from: 3, to: 4 },
  ]);
});

test('an added subtree is a single add op, not one per leaf', () => {
  assert.deepEqual(diff({}, { user: { name: 'ada', prefs: { beta: true } } }), [
    { op: 'add', path: ['user'], value: { name: 'ada', prefs: { beta: true } } },
  ]);
});

test('a removed subtree is a single remove op', () => {
  assert.deepEqual(diff({ cache: { ttl: 30, size: 100 } }, {}), [
    { op: 'remove', path: ['cache'] },
  ]);
});

test('array elements diff by index with numeric path segments', () => {
  assert.deepEqual(diff({ xs: [1, 2, 3] }, { xs: [1, 9, 3] }), [
    { op: 'change', path: ['xs', 1], from: 2, to: 9 },
  ]);
});

test('a grown array emits adds for trailing indices in ascending order', () => {
  assert.deepEqual(diff({ xs: ['a'] }, { xs: ['a', 'b', 'c'] }), [
    { op: 'add', path: ['xs', 1], value: 'b' },
    { op: 'add', path: ['xs', 2], value: 'c' },
  ]);
});

test('a shrunk array emits removes for trailing indices in descending order', () => {
  assert.deepEqual(diff({ xs: [1, 2, 3, 4] }, { xs: [1, 2] }), [
    { op: 'remove', path: ['xs', 3] },
    { op: 'remove', path: ['xs', 2] },
  ]);
});

test('a kind change (object vs leaf, array vs object) is one change op', () => {
  assert.deepEqual(diff({ v: { a: 1 } }, { v: 5 }), [
    { op: 'change', path: ['v'], from: { a: 1 }, to: 5 },
  ]);
  assert.deepEqual(diff({ v: [1, 2] }, { v: { 0: 1, 1: 2 } }), [
    { op: 'change', path: ['v'], from: [1, 2], to: { 0: 1, 1: 2 } },
  ]);
});

test('null is a leaf, not an object to recurse into', () => {
  assert.deepEqual(diff({ v: null }, { v: { a: 1 } }), [
    { op: 'change', path: ['v'], from: null, to: { a: 1 } },
  ]);
});

test('NaN compared to NaN is not a change', () => {
  assert.deepEqual(diff({ ratio: NaN }, { ratio: NaN }), []);
});

test('apply reconstructs after from before plus the diff', () => {
  const before = {
    profile: { name: 'ada', links: ['a.io', 'b.io', 'c.io'] },
    flags: { beta: true, legacy: true },
    version: 3,
  };
  const after = {
    profile: { name: 'ada lovelace', links: ['a.io'] },
    flags: { beta: false },
    version: 3,
    theme: { mode: 'dark' },
  };
  assert.deepEqual(apply(before, diff(before, after)), after);
});

test('apply never mutates the base value', () => {
  const before = { user: { name: 'ada' }, xs: [1, 2, 3] };
  const snapshot = JSON.parse(JSON.stringify(before));
  const after = { user: { name: 'bo' }, xs: [1] };
  apply(before, diff(before, after));
  assert.deepEqual(before, snapshot);
});

test('diffing between empty containers of different kinds still reports a change', () => {
  assert.deepEqual(diff({ v: [] }, { v: {} }), [
    { op: 'change', path: ['v'], from: [], to: {} },
  ]);
  assert.deepEqual(diff({ v: [] }, { v: [] }), []);
});
