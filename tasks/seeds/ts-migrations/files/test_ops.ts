import { test } from 'node:test';
import assert from 'node:assert/strict';
import { applyOp } from './ops.ts';
import { plan } from './planner.ts';

function snapshot(v: unknown) {
  return JSON.parse(JSON.stringify(v));
}

test('set writes a value, creating intermediate objects', () => {
  const doc = { user: { name: 'ada' } };
  const before = snapshot(doc);
  const next = applyOp(doc, { set: { path: 'user.plan.tier', value: 'pro' } });
  assert.deepEqual(next, { user: { name: 'ada', plan: { tier: 'pro' } } });
  assert.deepEqual(doc, before, 'input document must not be mutated');
});

test('set overwrites an existing value', () => {
  const next = applyOp({ mode: 'light' }, { set: { path: 'mode', value: 'dark' } });
  assert.deepEqual(next, { mode: 'dark' });
});

test('set refuses to tunnel through a non-object', () => {
  assert.throws(() => applyOp({ count: 5 }, { set: { path: 'count.max', value: 1 } }), /not an object/);
});

test('unset removes the leaf and keeps its parents', () => {
  const doc = { a: { b: 1, c: 2 } };
  const next = applyOp(doc, { unset: { path: 'a.b' } });
  assert.deepEqual(next, { a: { c: 2 } });
  assert.deepEqual(doc, { a: { b: 1, c: 2 } });
});

test('unset demands the full path exists', () => {
  assert.throws(() => applyOp({ a: { b: 1 } }, { unset: { path: 'a.zzz' } }), /no such path/);
  assert.throws(() => applyOp({}, { unset: { path: 'ghost.leaf' } }), /no such path/);
});

test('rename moves a value between dotted paths', () => {
  const next = applyOp({ profile: { plan: 'free' } }, { rename: { from: 'profile.plan', to: 'profile.tier' } });
  assert.deepEqual(next, { profile: { tier: 'free' } });
});

test('rename can move into a brand-new parent', () => {
  const next = applyOp({ a: { b: 7 } }, { rename: { from: 'a.b', to: 'c.d' } });
  assert.deepEqual(next, { a: {}, c: { d: 7 } });
});

test('rename checks presence, not truthiness', () => {
  const next = applyOp({ flag: false }, { rename: { from: 'flag', to: 'ok' } });
  assert.deepEqual(next, { ok: false });
});

test('rename errors: missing source, occupied target', () => {
  assert.throws(() => applyOp({}, { rename: { from: 'nope', to: 'x' } }), /no such path/);
  assert.throws(() => applyOp({ a: 1, b: 2 }, { rename: { from: 'a', to: 'b' } }), /already exists/);
});

test('unrecognized ops are rejected', () => {
  assert.throws(() => applyOp({}, { frobnicate: { path: 'x' } } as never), /unknown op/);
});

test('plan walks up ascending', () => {
  assert.deepEqual(plan(0, 3, [1, 2, 3]), [
    { version: 1, direction: 'up' },
    { version: 2, direction: 'up' },
    { version: 3, direction: 'up' },
  ]);
});

test('plan walks down descending, stopping above the target', () => {
  assert.deepEqual(plan(3, 1, [1, 2, 3]), [
    { version: 3, direction: 'down' },
    { version: 2, direction: 'down' },
  ]);
});

test('plan handles gaps and unsorted version lists', () => {
  assert.deepEqual(plan(1, 9, [9, 1, 5]), [
    { version: 5, direction: 'up' },
    { version: 9, direction: 'up' },
  ]);
  assert.deepEqual(plan(9, 0, [9, 1, 5]), [
    { version: 9, direction: 'down' },
    { version: 5, direction: 'down' },
    { version: 1, direction: 'down' },
  ]);
});

test('plan of a no-op move is empty', () => {
  assert.deepEqual(plan(2, 2, [1, 2, 3]), []);
  assert.deepEqual(plan(0, 0, []), []);
});

test('plan rejects current or target outside the version set', () => {
  assert.throws(() => plan(0, 4, [1, 2, 3]), RangeError);
  assert.throws(() => plan(0, 4, [1, 2, 3]), /unknown version/);
  assert.throws(() => plan(7, 0, [1, 2, 3]), /unknown version/);
});

test('plan rejects broken version lists', () => {
  assert.throws(() => plan(0, 1, [1, 2, 2]), /duplicate version/);
  assert.throws(() => plan(0, 1, [0, 1]), RangeError);
  assert.throws(() => plan(0, 1, [1.5]), RangeError);
});
