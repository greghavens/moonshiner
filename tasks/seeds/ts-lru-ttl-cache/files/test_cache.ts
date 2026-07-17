import { test } from 'node:test';
import assert from 'node:assert/strict';
import { LruCache } from './cache.ts';

type Evicted = [unknown, unknown, string];

function mk(opts: { maxSize?: number; defaultTtlMs?: number } = {}) {
  let now = 0;
  const evicted: Evicted[] = [];
  const cache = new LruCache({
    maxSize: opts.maxSize ?? 3,
    defaultTtlMs: opts.defaultTtlMs,
    clock: () => now,
    onEvict: (key: unknown, value: unknown, reason: string) => {
      evicted.push([key, value, reason]);
    },
  });
  return { cache, evicted, tick: (ms: number) => { now += ms; } };
}

test('set/get round-trips and missing keys read undefined', () => {
  const { cache } = mk();
  cache.set('a', 1);
  cache.set('b', { city: 'Oslo' });
  assert.equal(cache.get('a'), 1);
  assert.deepEqual(cache.get('b'), { city: 'Oslo' });
  assert.equal(cache.get('zzz'), undefined);
  assert.equal(cache.size(), 2);
});

test('maxSize must be a positive integer', () => {
  assert.throws(() => new LruCache({ maxSize: 0 }), RangeError);
  assert.throws(() => new LruCache({ maxSize: -2 }), RangeError);
  assert.throws(() => new LruCache({ maxSize: 2.5 }), RangeError);
});

test('exceeding capacity evicts the least-recently-used entry', () => {
  const { cache, evicted } = mk({ maxSize: 3 });
  cache.set('a', 1);
  cache.set('b', 2);
  cache.set('c', 3);
  cache.set('d', 4);
  assert.equal(cache.get('a'), undefined);
  assert.equal(cache.get('b'), 2);
  assert.equal(cache.get('d'), 4);
  assert.deepEqual(evicted, [['a', 1, 'capacity']]);
  assert.equal(cache.size(), 3);
});

test('get refreshes recency so someone else gets evicted', () => {
  const { cache, evicted } = mk({ maxSize: 3 });
  cache.set('a', 1);
  cache.set('b', 2);
  cache.set('c', 3);
  cache.get('a'); // a is now most recent; b is LRU
  cache.set('d', 4);
  assert.equal(cache.get('a'), 1);
  assert.equal(cache.get('b'), undefined);
  assert.deepEqual(evicted, [['b', 2, 'capacity']]);
});

test('peek and has do not refresh recency', () => {
  const { cache } = mk({ maxSize: 2 });
  cache.set('a', 1);
  cache.set('b', 2);
  assert.equal(cache.peek('a'), 1);
  assert.equal(cache.has('a'), true);
  cache.set('c', 3); // a stays LRU despite peek/has
  assert.equal(cache.get('a'), undefined);
  assert.equal(cache.get('b'), 2);
});

test('overwriting a key refreshes its recency', () => {
  const { cache } = mk({ maxSize: 2 });
  cache.set('a', 1);
  cache.set('b', 2);
  cache.set('a', 10); // b is now LRU
  cache.set('c', 3);
  assert.equal(cache.get('a'), 10);
  assert.equal(cache.get('b'), undefined);
});

test('keys lists live keys most-recently-used first', () => {
  const { cache } = mk({ maxSize: 5 });
  cache.set('a', 1);
  cache.set('b', 2);
  cache.set('c', 3);
  assert.deepEqual(cache.keys(), ['c', 'b', 'a']);
  cache.get('a');
  assert.deepEqual(cache.keys(), ['a', 'c', 'b']);
});

test('delete removes the entry without an eviction callback', () => {
  const { cache, evicted } = mk();
  cache.set('a', 1);
  assert.equal(cache.delete('a'), true);
  assert.equal(cache.delete('a'), false);
  assert.equal(cache.has('a'), false);
  assert.deepEqual(evicted, []);
});

test('an entry expires exactly at write time + ttlMs', () => {
  const { cache, tick } = mk();
  cache.set('token', 'abc', { ttlMs: 100 });
  tick(99);
  assert.equal(cache.get('token'), 'abc');
  tick(1);
  assert.equal(cache.get('token'), undefined);
  assert.equal(cache.peek('token'), undefined);
  assert.equal(cache.has('token'), false);
});

test('expired entries vanish from keys and size without any explicit sweep', () => {
  const { cache, tick } = mk();
  cache.set('a', 1, { ttlMs: 50 });
  cache.set('b', 2);
  tick(50);
  assert.deepEqual(cache.keys(), ['b']);
  assert.equal(cache.size(), 1);
});

test('defaultTtlMs applies when set gives no ttl, per-entry ttl overrides it', () => {
  const { cache, tick } = mk({ defaultTtlMs: 100 });
  cache.set('short', 1, { ttlMs: 10 });
  cache.set('normal', 2);
  tick(10);
  assert.equal(cache.get('short'), undefined);
  assert.equal(cache.get('normal'), 2);
  tick(90);
  assert.equal(cache.get('normal'), undefined);
});

test('entries without any ttl never expire', () => {
  const { cache, tick } = mk();
  cache.set('pinned', 'v');
  tick(10_000_000_000);
  assert.equal(cache.get('pinned'), 'v');
});

test('non-positive ttlMs is rejected', () => {
  const { cache } = mk();
  assert.throws(() => cache.set('k', 1, { ttlMs: 0 }), RangeError);
  assert.throws(() => cache.set('k', 1, { ttlMs: -1 }), RangeError);
  assert.equal(cache.has('k'), false);
});

test('overwriting resets the ttl from the current clock', () => {
  const { cache, tick } = mk();
  cache.set('s', 1, { ttlMs: 100 });
  tick(80);
  cache.set('s', 2, { ttlMs: 100 }); // new expiry at t=180
  tick(99); // t=179
  assert.equal(cache.get('s'), 2);
  tick(1); // t=180
  assert.equal(cache.get('s'), undefined);
});

test('get does not extend an entry\'s ttl', () => {
  const { cache, tick } = mk();
  cache.set('s', 1, { ttlMs: 100 });
  tick(90);
  assert.equal(cache.get('s'), 1);
  tick(10); // t=100: reading at t=90 must not have pushed expiry out
  assert.equal(cache.get('s'), undefined);
});

test('at capacity, an expired entry is evicted before any live LRU victim', () => {
  const { cache, evicted, tick } = mk({ maxSize: 3 });
  cache.set('old', 1); // LRU, but alive
  cache.set('tmp', 2, { ttlMs: 10 });
  cache.set('c', 3);
  tick(10); // tmp expired
  cache.set('d', 4);
  assert.equal(cache.get('old'), 1, 'live LRU entry must survive when an expired one exists');
  assert.equal(cache.get('tmp'), undefined);
  assert.equal(cache.get('d'), 4);
  assert.deepEqual(evicted, [['tmp', 2, 'expired']]);
});

test('capacity eviction reports the evicted key, value and reason', () => {
  const { cache, evicted } = mk({ maxSize: 1 });
  cache.set('first', 'v1');
  cache.set('second', 'v2');
  assert.deepEqual(evicted, [['first', 'v1', 'capacity']]);
});

test('overwriting an existing key at capacity evicts nothing', () => {
  const { cache, evicted } = mk({ maxSize: 2 });
  cache.set('a', 1);
  cache.set('b', 2);
  cache.set('a', 100);
  assert.deepEqual(evicted, []);
  assert.equal(cache.size(), 2);
});
