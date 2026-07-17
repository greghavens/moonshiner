import { test } from 'node:test';
import assert from 'node:assert/strict';
import { merge3 } from './merge.ts';

test('identical documents merge to themselves with no conflicts', () => {
  const doc = { server: { port: 8080 }, debug: false };
  const r = merge3(doc, { server: { port: 8080 }, debug: false }, doc);
  assert.deepEqual(r.merged, doc);
  assert.deepEqual(r.conflicts, []);
});

test('a change made only by ours is kept', () => {
  const base = { port: 8080, host: 'localhost' };
  const r = merge3(base, { port: 9090, host: 'localhost' }, { port: 8080, host: 'localhost' });
  assert.deepEqual(r.merged, { port: 9090, host: 'localhost' });
  assert.deepEqual(r.conflicts, []);
});

test('a change made only by theirs is kept', () => {
  const base = { port: 8080, host: 'localhost' };
  const r = merge3(base, { port: 8080, host: 'localhost' }, { port: 8080, host: 'db.internal' });
  assert.deepEqual(r.merged, { port: 8080, host: 'db.internal' });
  assert.deepEqual(r.conflicts, []);
});

test('both sides making the same change is not a conflict', () => {
  const r = merge3({ retries: 3 }, { retries: 5 }, { retries: 5 });
  assert.deepEqual(r.merged, { retries: 5 });
  assert.deepEqual(r.conflicts, []);
});

test('both sides changing the same key differently is an edit-edit conflict, base kept', () => {
  const r = merge3(
    { server: { port: 8080 } },
    { server: { port: 9090 } },
    { server: { port: 3000 } },
  );
  assert.deepEqual(r.merged, { server: { port: 8080 } });
  assert.deepEqual(r.conflicts, [
    { path: 'server.port', kind: 'edit-edit', base: 8080, ours: 9090, theirs: 3000 },
  ]);
});

test('a key deleted on one side and untouched on the other goes away', () => {
  const base = { a: 1, b: 2 };
  const r = merge3(base, { b: 2 }, { a: 1, b: 2 });
  assert.deepEqual(r.merged, { b: 2 });
  assert.deepEqual(r.conflicts, []);
});

test('delete vs edit is a delete-edit conflict with only the surviving sides recorded', () => {
  const r = merge3({ cache: { ttl: 60 } }, { cache: {} }, { cache: { ttl: 300 } });
  assert.deepEqual(r.merged, { cache: { ttl: 60 } });
  assert.deepEqual(r.conflicts, [
    { path: 'cache.ttl', kind: 'delete-edit', base: 60, theirs: 300 },
  ]);
});

test('additions merge; identical additions collapse; divergent additions are add-add conflicts', () => {
  const r = merge3(
    { name: 'svc' },
    { name: 'svc', region: 'us-east-1', replicas: 2 },
    { name: 'svc', region: 'us-east-1', replicas: 4 },
  );
  assert.deepEqual(r.merged, { name: 'svc', region: 'us-east-1' });
  assert.deepEqual(r.conflicts, [
    { path: 'replicas', kind: 'add-add', ours: 2, theirs: 4 },
  ]);
});

test('objects merge structurally: edits in different subtrees both land', () => {
  const base = { db: { host: 'a', pool: { size: 5 } }, log: { level: 'info' } };
  const r = merge3(
    base,
    { db: { host: 'a', pool: { size: 20 } }, log: { level: 'info' } },
    { db: { host: 'a', pool: { size: 5 } }, log: { level: 'debug' } },
  );
  assert.deepEqual(r.merged, {
    db: { host: 'a', pool: { size: 20 } },
    log: { level: 'debug' },
  });
  assert.deepEqual(r.conflicts, []);
});

test('deeply nested conflicts carry the full dotted path', () => {
  const r = merge3(
    { a: { b: { c: 1 } } },
    { a: { b: { c: 2 } } },
    { a: { b: { c: 3 } } },
  );
  assert.equal(r.conflicts[0].path, 'a.b.c');
});

test('a type change against an edit conflicts as a whole value', () => {
  const r = merge3({ timeout: 30 }, { timeout: { connect: 5 } }, { timeout: 60 });
  assert.deepEqual(r.conflicts, [
    { path: 'timeout', kind: 'edit-edit', base: 30, ours: { connect: 5 }, theirs: 60 },
  ]);
  assert.deepEqual(r.merged, { timeout: 30 });
});

test('scalar documents can conflict at the root, path is the empty string', () => {
  const r = merge3(1, 2, 3);
  assert.equal(r.merged, 1);
  assert.deepEqual(r.conflicts, [{ path: '', kind: 'edit-edit', base: 1, ours: 2, theirs: 3 }]);
});

test('null is a value, not a deletion', () => {
  const r = merge3({ proxy: 'none' }, { proxy: null }, { proxy: 'none' });
  assert.deepEqual(r.merged, { proxy: null });
  assert.deepEqual(r.conflicts, []);
  const c = merge3({ proxy: 'none' }, { proxy: null }, { proxy: 'socks5' });
  assert.deepEqual(c.conflicts, [
    { path: 'proxy', kind: 'edit-edit', base: 'none', ours: null, theirs: 'socks5' },
  ]);
});

test('conflicts arrive in traversal order: base keys first, then ours additions, then theirs', () => {
  const r = merge3(
    { x: 1, y: 1 },
    { x: 2, y: 3, added: 'o' },
    { x: 9, y: 4, added: 't' },
  );
  assert.deepEqual(
    r.conflicts.map((c) => c.path),
    ['x', 'y', 'added'],
  );
});

test('the merged document is detached from the inputs', () => {
  const base = { opts: { a: 1 } };
  const ours = { opts: { a: 1 } };
  const theirs = { opts: { a: 1 } };
  const r = merge3(base, ours, theirs);
  (r.merged as { opts: { a: number } }).opts.a = 99;
  assert.equal(base.opts.a, 1);
  assert.equal(ours.opts.a, 1);
  assert.equal(theirs.opts.a, 1);
});
