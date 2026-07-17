import { test } from 'node:test';
import assert from 'node:assert/strict';
import { FlightCache } from './flightcache.ts';

// -- helpers -----------------------------------------------------------------

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (v: T) => void;
  reject: (e: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function countingLoader<T>(value: T) {
  let calls = 0;
  return {
    get calls() {
      return calls;
    },
    load: async () => {
      calls++;
      return value;
    },
  };
}

async function tick(turns = 10): Promise<void> {
  for (let i = 0; i < turns; i++) await Promise.resolve();
}

// -- single flight -----------------------------------------------------------

test('a cold get calls the loader and returns its value', async () => {
  const cache = new FlightCache<string>();
  const loader = countingLoader('profile:ada');
  assert.equal(await cache.get('user:1', loader.load), 'profile:ada');
  assert.equal(loader.calls, 1);
});

test('concurrent gets for one key share a single flight', async () => {
  const cache = new FlightCache<string>();
  const d = deferred<string>();
  let calls = 0;
  const load = () => {
    calls++;
    return d.promise;
  };
  const p1 = cache.get('user:1', load);
  const p2 = cache.get('user:1', load);
  const p3 = cache.get('user:1', load);
  await tick();
  assert.equal(calls, 1);
  d.resolve('shared');
  assert.deepEqual(await Promise.all([p1, p2, p3]), ['shared', 'shared', 'shared']);
  assert.equal(calls, 1);
});

test('while a flight is pending, later callers loaders are never invoked', async () => {
  const cache = new FlightCache<string>();
  const d = deferred<string>();
  let secondCalled = false;
  const p1 = cache.get('cfg', () => d.promise);
  const p2 = cache.get('cfg', async () => {
    secondCalled = true;
    return 'other';
  });
  d.resolve('first wins');
  assert.equal(await p1, 'first wins');
  assert.equal(await p2, 'first wins');
  assert.equal(secondCalled, false);
});

test('after a successful flight the value is served from cache', async () => {
  const cache = new FlightCache<string>();
  const loader = countingLoader('v1');
  await cache.get('k', loader.load);
  await cache.get('k', loader.load);
  await cache.get('k', loader.load);
  assert.equal(loader.calls, 1);
});

test('different keys load independently and concurrently', async () => {
  const cache = new FlightCache<string>();
  const da = deferred<string>();
  const db = deferred<string>();
  let callsA = 0;
  let callsB = 0;
  const pa = cache.get('a', () => {
    callsA++;
    return da.promise;
  });
  const pb = cache.get('b', () => {
    callsB++;
    return db.promise;
  });
  await tick();
  assert.equal(callsA, 1);
  assert.equal(callsB, 1);
  db.resolve('B');
  da.resolve('A');
  assert.equal(await pa, 'A');
  assert.equal(await pb, 'B');
});

// -- error eviction ----------------------------------------------------------

test('a failed flight rejects every waiter with the same error', async () => {
  const cache = new FlightCache<string>();
  const d = deferred<string>();
  const p1 = cache.get('k', () => d.promise);
  const p2 = cache.get('k', () => d.promise);
  const boom = new Error('upstream 503');
  d.reject(boom);
  const [e1, e2] = await Promise.all([p1.catch((e: unknown) => e), p2.catch((e: unknown) => e)]);
  assert.equal(e1, boom);
  assert.equal(e2, boom);
});

test('errors are not cached: the next get retries the loader', async () => {
  const cache = new FlightCache<string>();
  let calls = 0;
  const load = async () => {
    calls++;
    if (calls === 1) throw new Error('cold start');
    return 'recovered';
  };
  await assert.rejects(cache.get('k', load), /cold start/);
  assert.equal(await cache.get('k', load), 'recovered');
  assert.equal(calls, 2);
});

// -- TTL ---------------------------------------------------------------------

test('values are served from cache while fresh and reloaded once the TTL passes', async () => {
  let t = 1000;
  const cache = new FlightCache<string>({ ttlMs: 500, now: () => t });
  let calls = 0;
  const load = async () => {
    calls++;
    return `load#${calls}`;
  };
  assert.equal(await cache.get('k', load), 'load#1');
  t = 1499; // 499ms later: still fresh
  assert.equal(await cache.get('k', load), 'load#1');
  t = 1500; // exactly ttl: stale
  assert.equal(await cache.get('k', load), 'load#2');
  assert.equal(calls, 2);
});

test('freshness is measured from when the value was stored, not first requested', async () => {
  let t = 0;
  const cache = new FlightCache<string>({ ttlMs: 100, now: () => t });
  const d = deferred<string>();
  const p = cache.get('k', () => d.promise); // requested at t=0
  t = 90;
  d.resolve('slow value'); // stored at t=90
  assert.equal(await p, 'slow value');
  t = 150; // 60ms after store: still fresh even though 150ms after request
  const loader = countingLoader('should not be needed');
  assert.equal(await cache.get('k', loader.load), 'slow value');
  assert.equal(loader.calls, 0);
  t = 190; // 100ms after store: stale
  assert.equal(await cache.get('k', loader.load), 'should not be needed');
  assert.equal(loader.calls, 1);
});

test('without a ttl, values are cached indefinitely', async () => {
  let t = 0;
  const cache = new FlightCache<string>({ now: () => t });
  const loader = countingLoader('forever');
  await cache.get('k', loader.load);
  t = Number.MAX_SAFE_INTEGER;
  await cache.get('k', loader.load);
  assert.equal(loader.calls, 1);
});

// -- invalidation ------------------------------------------------------------

test('invalidate(key) forces the next get to reload', async () => {
  const cache = new FlightCache<string>();
  let calls = 0;
  const load = async () => {
    calls++;
    return `v${calls}`;
  };
  assert.equal(await cache.get('k', load), 'v1');
  cache.invalidate('k');
  assert.equal(await cache.get('k', load), 'v2');
});

test('invalidate only touches its own key', async () => {
  const cache = new FlightCache<string>();
  const la = countingLoader('A');
  const lb = countingLoader('B');
  await cache.get('a', la.load);
  await cache.get('b', lb.load);
  cache.invalidate('a');
  await cache.get('a', la.load);
  await cache.get('b', lb.load);
  assert.equal(la.calls, 2);
  assert.equal(lb.calls, 1);
});

test('invalidating during a flight still delivers to waiters but does not cache the result', async () => {
  const cache = new FlightCache<string>();
  const d = deferred<string>();
  let calls = 0;
  const p = cache.get('k', () => {
    calls++;
    return d.promise;
  });
  await tick();
  cache.invalidate('k'); // e.g. a write landed while we were reading
  d.resolve('stale read');
  assert.equal(await p, 'stale read'); // the caller who asked still gets an answer
  const loader = countingLoader('fresh read');
  assert.equal(await cache.get('k', loader.load), 'fresh read');
  assert.equal(loader.calls, 1);
  assert.equal(calls, 1);
});

test('clear() wipes every key', async () => {
  const cache = new FlightCache<string>();
  const la = countingLoader('A');
  const lb = countingLoader('B');
  await cache.get('a', la.load);
  await cache.get('b', lb.load);
  cache.clear();
  await cache.get('a', la.load);
  await cache.get('b', lb.load);
  assert.equal(la.calls, 2);
  assert.equal(lb.calls, 2);
});
