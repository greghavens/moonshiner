import { test } from 'node:test';
import assert from 'node:assert/strict';
import { KVStore } from './store.ts';
import { find } from './query.ts';

function mk(startAt: number = 0) {
  let now = startAt;
  const store = new KVStore({ clock: () => now });
  return {
    store,
    tick: (ms: number) => { now += ms; },
  };
}

test('set/get round-trips values and missing keys read as undefined', () => {
  const { store } = mk();
  store.set('greeting', 'hello');
  store.set('answer', 42);
  store.set('user:1', { name: 'ada' });
  assert.equal(store.get('greeting'), 'hello');
  assert.equal(store.get('answer'), 42);
  assert.deepEqual(store.get('user:1'), { name: 'ada' });
  assert.equal(store.get('nope'), undefined);
  assert.equal(store.has('greeting'), true);
  assert.equal(store.has('nope'), false);
});

test('overwriting a key replaces the stored value', () => {
  const { store } = mk();
  store.set('mode', 'light');
  store.set('mode', 'dark');
  assert.equal(store.get('mode'), 'dark');
  assert.deepEqual(store.keys(), ['mode']);
});

test('delete removes a key and reports whether a live key was removed', () => {
  const { store } = mk();
  store.set('k', 1);
  assert.equal(store.delete('k'), true);
  assert.equal(store.get('k'), undefined);
  assert.equal(store.has('k'), false);
  assert.equal(store.delete('k'), false);
  assert.equal(store.delete('never-there'), false);
});

test('keys() lists live keys in sorted order', () => {
  const { store } = mk();
  store.set('banana', 1);
  store.set('apple', 2);
  store.set('cherry', 3);
  assert.deepEqual(store.keys(), ['apple', 'banana', 'cherry']);
});

test('a key expires exactly when now reaches write time + ttlMs', () => {
  const { store, tick } = mk();
  store.set('session', 'tok-91', { ttlMs: 100 });
  tick(99);
  assert.equal(store.get('session'), 'tok-91');
  assert.equal(store.has('session'), true);
  tick(1); // now = 100 = write time + ttl
  assert.equal(store.get('session'), undefined);
  assert.equal(store.has('session'), false);
  assert.deepEqual(store.keys(), []);
  assert.equal(store.delete('session'), false);
});

test('keys written without ttl never expire', () => {
  const { store, tick } = mk();
  store.set('pinned', 'forever');
  tick(1_000_000_000_000);
  assert.equal(store.get('pinned'), 'forever');
});

test('rewriting a key restarts its ttl from the time of the write', () => {
  const { store, tick } = mk();
  store.set('s', 1, { ttlMs: 100 });
  tick(60);
  store.set('s', 2, { ttlMs: 100 }); // new expiry: t=160
  tick(70); // t=130
  assert.equal(store.get('s'), 2);
  tick(30); // t=160
  assert.equal(store.get('s'), undefined);
});

test('rewriting without ttl clears the previous expiry', () => {
  const { store, tick } = mk();
  store.set('s', 1, { ttlMs: 100 });
  tick(50);
  store.set('s', 2);
  tick(10_000);
  assert.equal(store.get('s'), 2);
});

test('non-positive ttlMs is rejected with a RangeError', () => {
  const { store } = mk();
  assert.throws(() => store.set('k', 1, { ttlMs: 0 }), RangeError);
  assert.throws(() => store.set('k', 1, { ttlMs: -5 }), RangeError);
  assert.equal(store.has('k'), false);
});

test('rollback discards writes made inside the transaction', () => {
  const { store } = mk();
  store.set('color', 'red');
  store.begin();
  store.set('color', 'blue');
  store.set('brand', 'acme');
  assert.equal(store.get('color'), 'blue'); // read-your-writes
  assert.equal(store.get('brand'), 'acme');
  store.rollback();
  assert.equal(store.get('color'), 'red');
  assert.equal(store.get('brand'), undefined);
  assert.deepEqual(store.keys(), ['color']);
});

test('commit publishes writes made inside the transaction', () => {
  const { store } = mk();
  store.begin();
  store.set('a', 1);
  store.commit();
  assert.equal(store.get('a'), 1);
  assert.equal(store.depth(), 0);
});

test('deletes roll back and commit like writes do', () => {
  const { store } = mk();
  store.set('a', 1);
  store.begin();
  assert.equal(store.delete('a'), true);
  assert.equal(store.get('a'), undefined);
  assert.equal(store.has('a'), false);
  assert.deepEqual(store.keys(), []);
  store.rollback();
  assert.equal(store.get('a'), 1);

  store.begin();
  store.delete('a');
  store.commit();
  assert.equal(store.get('a'), undefined);
  assert.deepEqual(store.keys(), []);
});

test('transactions nest: rollback unwinds only the innermost level', () => {
  const { store } = mk();
  store.set('n', 1);
  store.begin();
  store.set('n', 2);
  store.begin();
  store.set('n', 3);
  assert.equal(store.get('n'), 3);
  store.rollback();
  assert.equal(store.get('n'), 2);
  store.commit();
  assert.equal(store.get('n'), 2);
  assert.equal(store.depth(), 0);
});

test('an inner commit merges into the parent transaction, not the base', () => {
  const { store } = mk();
  store.begin();
  store.set('b', 2);
  store.begin();
  store.set('c', 3);
  store.commit(); // inner: c now belongs to the outer level
  assert.equal(store.get('c'), 3);
  store.rollback(); // outer: takes b AND c with it
  assert.equal(store.get('b'), undefined);
  assert.equal(store.get('c'), undefined);
  assert.deepEqual(store.keys(), []);
});

test('a delete committed into the parent still rolls back with the parent', () => {
  const { store } = mk();
  store.set('x', 'base');
  store.begin();
  store.begin();
  store.delete('x');
  store.commit(); // delete now pending in the outer level
  assert.equal(store.get('x'), undefined);
  store.rollback(); // outer rollback resurrects x
  assert.equal(store.get('x'), 'base');
});

test('commit or rollback without an open transaction throws', () => {
  const { store } = mk();
  assert.throws(() => store.commit(), /no active transaction/);
  assert.throws(() => store.rollback(), /no active transaction/);
});

test('depth() reports the number of open levels', () => {
  const { store } = mk();
  assert.equal(store.depth(), 0);
  store.begin();
  assert.equal(store.depth(), 1);
  store.begin();
  assert.equal(store.depth(), 2);
  store.rollback();
  assert.equal(store.depth(), 1);
  store.commit();
  assert.equal(store.depth(), 0);
});

test('a ttl written inside a transaction masks the base value once expired', () => {
  const { store, tick } = mk();
  store.set('cache', 'permanent');
  store.begin();
  store.set('cache', 'temp', { ttlMs: 50 });
  tick(50);
  // the transactional write expired: the key is gone, the base value must NOT leak through
  assert.equal(store.get('cache'), undefined);
  assert.equal(store.has('cache'), false);
  store.rollback();
  assert.equal(store.get('cache'), 'permanent');
});

test('committing a ttl entry keeps its original expiry time', () => {
  const { store, tick } = mk();
  store.begin();
  store.set('s', 1, { ttlMs: 100 }); // expires at t=100 regardless of commit time
  tick(30);
  store.commit();
  tick(69); // t=99
  assert.equal(store.get('s'), 1);
  tick(1); // t=100
  assert.equal(store.get('s'), undefined);
});

test('find filters by prefix and returns sorted {key, value} pairs', () => {
  const { store } = mk();
  store.set('user:2', { name: 'bo' });
  store.set('cfg:mode', 'dark');
  store.set('user:1', { name: 'ada' });
  assert.deepEqual(find(store, { prefix: 'user:' }), [
    { key: 'user:1', value: { name: 'ada' } },
    { key: 'user:2', value: { name: 'bo' } },
  ]);
});

test('find applies where predicates over (value, key)', () => {
  const { store } = mk();
  store.set('a', 10);
  store.set('b', 25);
  store.set('c', 30);
  const big = find(store, { where: (v: unknown) => (v as number) >= 25 });
  assert.deepEqual(big, [
    { key: 'b', value: 25 },
    { key: 'c', value: 30 },
  ]);
  const notB = find(store, { where: (_v: unknown, key: string) => key !== 'b' });
  assert.deepEqual(notB.map((e: { key: string; value: unknown }) => e.key), ['a', 'c']);
});

test('find applies limit after filtering and sorting', () => {
  const { store } = mk();
  store.set('q3', 3);
  store.set('q1', 1);
  store.set('q2', 2);
  assert.deepEqual(find(store, { limit: 2 }), [
    { key: 'q1', value: 1 },
    { key: 'q2', value: 2 },
  ]);
  assert.deepEqual(find(store, { prefix: 'q', where: (v: unknown) => (v as number) > 1, limit: 1 }), [
    { key: 'q2', value: 2 },
  ]);
});

test('find never returns expired entries and sees uncommitted transaction state', () => {
  const { store, tick } = mk();
  store.set('user:1', 'ada', { ttlMs: 10 });
  store.set('user:2', 'bo');
  tick(10);
  assert.deepEqual(find(store, { prefix: 'user:' }), [{ key: 'user:2', value: 'bo' }]);
  store.begin();
  store.set('user:3', 'cy');
  store.delete('user:2');
  assert.deepEqual(find(store, { prefix: 'user:' }), [{ key: 'user:3', value: 'cy' }]);
  store.rollback();
  assert.deepEqual(find(store, { prefix: 'user:' }), [{ key: 'user:2', value: 'bo' }]);
});

test('find with no criteria returns every live entry', () => {
  const { store } = mk();
  store.set('one', 1);
  store.set('two', 2);
  assert.deepEqual(find(store, {}), [
    { key: 'one', value: 1 },
    { key: 'two', value: 2 },
  ]);
  assert.deepEqual(find(store), [
    { key: 'one', value: 1 },
    { key: 'two', value: 2 },
  ]);
});
