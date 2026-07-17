import { test } from 'node:test';
import assert from 'node:assert/strict';
import { OptimisticStore, serverWins, clientWins } from './optimistic.ts';
import type { Policy } from './optimistic.ts';

type Conflict = { local: unknown; server: unknown; opId: string; opKey: string; opValue: unknown };

function collector() {
  const calls: Conflict[] = [];
  const onConflict = (local: unknown, server: unknown, op: { id: string; key: string; value: unknown }) => {
    calls.push({ local, server, opId: op.id, opKey: op.key, opValue: op.value });
  };
  return { calls, onConflict };
}

// ---------- apply + overlay ----------

test('apply overlays state immediately but leaves confirmed untouched', () => {
  const store = new OptimisticStore({ title: 'draft', likes: 0 });
  const id = store.apply('title', 'v2');
  assert.equal(id, 'op-1');
  assert.deepEqual(store.state(), { title: 'v2', likes: 0 });
  assert.deepEqual(store.confirmed(), { title: 'draft', likes: 0 });
  assert.deepEqual(store.pending(), [{ id: 'op-1', key: 'title', value: 'v2' }]);
});

test('op ids increment per created op', () => {
  const store = new OptimisticStore({});
  assert.equal(store.apply('a', 1), 'op-1');
  assert.equal(store.apply('b', 2), 'op-2');
  assert.equal(store.apply('c', 3), 'op-3');
});

test('base object is copied at construction', () => {
  const base: Record<string, unknown> = { n: 1 };
  const store = new OptimisticStore(base);
  base.n = 999;
  base.added = true;
  assert.deepEqual(store.state(), { n: 1 });
});

test('state() returns a fresh object every call', () => {
  const store = new OptimisticStore({ a: 1 });
  const snap = store.state();
  (snap as Record<string, unknown>).a = 42;
  (snap as Record<string, unknown>).b = 'junk';
  assert.deepEqual(store.state(), { a: 1 });
  const conf = store.confirmed();
  (conf as Record<string, unknown>).a = 77;
  assert.deepEqual(store.confirmed(), { a: 1 });
});

// ---------- coalescing ----------

test('consecutive applies on the same key coalesce into the tail op', () => {
  const store = new OptimisticStore({ title: 'a' });
  const first = store.apply('title', 'ab');
  const second = store.apply('title', 'abc');
  assert.equal(first, 'op-1');
  assert.equal(second, 'op-1'); // same op, updated in place
  assert.deepEqual(store.pending(), [{ id: 'op-1', key: 'title', value: 'abc' }]);
  // the counter did not advance for the coalesced apply
  assert.equal(store.apply('other', 1), 'op-2');
});

test('coalescing only merges with the tail — an intervening key breaks the run', () => {
  const store = new OptimisticStore({});
  store.apply('a', 1);
  store.apply('b', 2);
  const third = store.apply('a', 3);
  assert.equal(third, 'op-3');
  assert.deepEqual(store.pending(), [
    { id: 'op-1', key: 'a', value: 1 },
    { id: 'op-2', key: 'b', value: 2 },
    { id: 'op-3', key: 'a', value: 3 },
  ]);
  assert.deepEqual(store.state(), { a: 3, b: 2 });
});

// ---------- confirm ----------

test('clean confirm commits the value and skips the conflict callback', () => {
  const { calls, onConflict } = collector();
  const store = new OptimisticStore({ likes: 0 }, { onConflict });
  store.apply('likes', 1);
  store.confirm('op-1', 1);
  assert.deepEqual(store.confirmed(), { likes: 1 });
  assert.deepEqual(store.pending(), []);
  assert.equal(calls.length, 0);
});

test('structurally-equal server value is a clean commit (deep equality)', () => {
  const { calls, onConflict } = collector();
  const store = new OptimisticStore({}, { onConflict });
  store.apply('prefs', { theme: 'dark', tags: ['a', 'b'] });
  store.confirm('op-1', { theme: 'dark', tags: ['a', 'b'] }); // different reference
  assert.equal(calls.length, 0);
  assert.deepEqual(store.confirmed(), { prefs: { theme: 'dark', tags: ['a', 'b'] } });
});

test('divergent confirm under serverWins commits the server value', () => {
  const { calls, onConflict } = collector();
  const store = new OptimisticStore({ qty: 1 }, { policy: serverWins, onConflict });
  store.apply('qty', 5);
  store.confirm('op-1', 3);
  assert.deepEqual(store.confirmed(), { qty: 3 });
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], { local: 5, server: 3, opId: 'op-1', opKey: 'qty', opValue: 5 });
});

test('serverWins is the default policy', () => {
  const store = new OptimisticStore({ qty: 1 });
  store.apply('qty', 5);
  store.confirm('op-1', 3);
  assert.deepEqual(store.confirmed(), { qty: 3 });
});

test('divergent confirm under clientWins keeps the local value', () => {
  const store = new OptimisticStore({ qty: 1 }, { policy: clientWins });
  store.apply('qty', 5);
  store.confirm('op-1', 3);
  assert.deepEqual(store.confirmed(), { qty: 5 });
});

test('policy objects expose their names', () => {
  assert.equal(serverWins.name, 'server-wins');
  assert.equal(clientWins.name, 'client-wins');
});

test('a custom policy resolve() decides the committed value', () => {
  const maxWins: Policy = {
    name: 'max-wins',
    resolve: (local, server) => Math.max(local as number, server as number),
  };
  const store = new OptimisticStore({ high: 10 }, { policy: maxWins });
  store.apply('high', 12);
  store.confirm('op-1', 15);
  assert.deepEqual(store.confirmed(), { high: 15 });
  store.apply('high', 20);
  store.confirm('op-2', 18);
  assert.deepEqual(store.confirmed(), { high: 20 });
});

test('conflict callback sees the coalesced local value', () => {
  const { calls, onConflict } = collector();
  const store = new OptimisticStore({}, { onConflict });
  store.apply('note', 'v1');
  store.apply('note', 'v2'); // coalesces into op-1
  store.confirm('op-1', 'server-copy');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].local, 'v2');
  assert.equal(calls[0].opValue, 'v2');
});

// ---------- reject / rollback ----------

test('reject without a server value rolls back to the prior confirmed value', () => {
  const { calls, onConflict } = collector();
  const store = new OptimisticStore({ title: 'saved' }, { onConflict });
  store.apply('title', 'doomed');
  store.reject('op-1');
  assert.deepEqual(store.state(), { title: 'saved' });
  assert.deepEqual(store.pending(), []);
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], {
    local: 'doomed', server: undefined, opId: 'op-1', opKey: 'title', opValue: 'doomed',
  });
});

test('reject with a server value adopts it', () => {
  const store = new OptimisticStore({ title: 'saved' });
  store.apply('title', 'doomed');
  store.reject('op-1', 'server-truth');
  assert.deepEqual(store.confirmed(), { title: 'server-truth' });
});

test('reject ignores the policy — clientWins still loses a rejected op', () => {
  const store = new OptimisticStore({ qty: 1 }, { policy: clientWins });
  store.apply('qty', 99);
  store.reject('op-1', 2);
  assert.deepEqual(store.confirmed(), { qty: 2 });
});

test('later pending ops on the same key keep overlaying after an earlier reject', () => {
  const store = new OptimisticStore({ a: 1 });
  store.apply('a', 2);   // op-1
  store.apply('b', 5);   // op-2
  store.apply('a', 9);   // op-3 (not coalesced: op-2 sits between)
  store.reject('op-1');
  assert.deepEqual(store.pending(), [
    { id: 'op-2', key: 'b', value: 5 },
    { id: 'op-3', key: 'a', value: 9 },
  ]);
  assert.deepEqual(store.state(), { a: 9, b: 5 }); // op-3 still overlays a
  assert.deepEqual(store.confirmed(), { a: 1 });
});

// ---------- FIFO discipline + errors ----------

test('confirming an op that is not at the head throws', () => {
  const store = new OptimisticStore({});
  store.apply('a', 1);
  store.apply('b', 2);
  assert.throws(() => store.confirm('op-2', 2), {
    message: 'op "op-2" is not at the head of the queue',
  });
  assert.throws(() => store.reject('op-2'), {
    message: 'op "op-2" is not at the head of the queue',
  });
});

test('unknown op ids throw, including on an empty queue', () => {
  const store = new OptimisticStore({});
  assert.throws(() => store.confirm('op-9', 1), { message: 'unknown op "op-9"' });
  store.apply('a', 1);
  assert.throws(() => store.reject('op-7'), { message: 'unknown op "op-7"' });
  store.confirm('op-1', 1);
  // an already-resolved id is gone from the queue
  assert.throws(() => store.confirm('op-1', 1), { message: 'unknown op "op-1"' });
});

// ---------- an end-to-end editing session ----------

test('walkthrough: queue, coalesce, confirm, conflict, reject', () => {
  const { calls, onConflict } = collector();
  const store = new OptimisticStore(
    { title: 'Q3 plan', body: '', reviewers: 1 },
    { policy: serverWins, onConflict },
  );

  store.apply('body', 'first dr');       // op-1
  store.apply('body', 'first draft');    // coalesces into op-1
  store.apply('title', 'Q3 plan v2');    // op-2
  store.apply('reviewers', 2);           // op-3

  assert.deepEqual(store.state(), { title: 'Q3 plan v2', body: 'first draft', reviewers: 2 });
  assert.deepEqual(store.pending().map((op) => op.id), ['op-1', 'op-2', 'op-3']);

  // server stores the body verbatim: clean commit
  store.confirm('op-1', 'first draft');
  assert.deepEqual(store.confirmed(), { title: 'Q3 plan', body: 'first draft', reviewers: 1 });

  // server normalized the title: conflict resolved server-wins
  store.confirm('op-2', 'Q3 Plan v2');
  assert.deepEqual(store.confirmed(), { title: 'Q3 Plan v2', body: 'first draft', reviewers: 1 });
  assert.deepEqual(store.state(), { title: 'Q3 Plan v2', body: 'first draft', reviewers: 2 });

  // server declines the reviewer bump and reports the actual value
  store.reject('op-3', 1);
  assert.deepEqual(store.state(), { title: 'Q3 Plan v2', body: 'first draft', reviewers: 1 });
  assert.deepEqual(store.pending(), []);

  assert.deepEqual(calls.map((c) => [c.opId, c.local, c.server]), [
    ['op-2', 'Q3 plan v2', 'Q3 Plan v2'],
    ['op-3', 2, 1],
  ]);
});
