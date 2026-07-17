import { test } from 'node:test';
import assert from 'node:assert/strict';
import { memoizeAsync } from './memo.ts';

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

async function tick(turns = 10): Promise<void> {
  for (let i = 0; i < turns; i++) await Promise.resolve();
}

// -- basic caching -----------------------------------------------------------

test('resolved values are cached per argument', async () => {
  let calls = 0;
  const fetchUser = memoizeAsync(async (id: number) => {
    calls++;
    return `user-${id}`;
  });
  assert.equal(await fetchUser(1), 'user-1');
  assert.equal(await fetchUser(1), 'user-1');
  assert.equal(await fetchUser(2), 'user-2');
  assert.equal(calls, 2);
});

test('string, boolean and undefined keys are all cached by value', async () => {
  let calls = 0;
  const m = memoizeAsync(async (k: unknown) => {
    calls++;
    return String(k);
  });
  await m('a');
  await m('a');
  await m(true);
  await m(true);
  await m(undefined);
  await m(undefined);
  assert.equal(calls, 3);
});

test('NaN is a stable cache key', async () => {
  let calls = 0;
  const m = memoizeAsync(async (_n: number) => ++calls);
  assert.equal(await m(NaN), 1);
  assert.equal(await m(NaN), 1);
  assert.equal(calls, 1);
});

test('object arguments are keyed by identity, not by shape', async () => {
  let calls = 0;
  const m = memoizeAsync(async (q: { term: string }) => {
    calls++;
    return `results for ${q.term}`;
  });
  const q1 = { term: 'espresso' };
  const q2 = { term: 'espresso' }; // deep-equal but a different object
  await m(q1);
  await m(q1);
  await m(q2);
  assert.equal(calls, 2);
});

// -- in-flight dedup ---------------------------------------------------------

test('concurrent calls with the same key share one invocation', async () => {
  const d = deferred<string>();
  let calls = 0;
  const m = memoizeAsync((_k: string) => {
    calls++;
    return d.promise;
  });
  const p1 = m('cfg');
  const p2 = m('cfg');
  await tick();
  assert.equal(calls, 1);
  d.resolve('loaded');
  assert.equal(await p1, 'loaded');
  assert.equal(await p2, 'loaded');
  assert.equal(calls, 1);
});

test('concurrent calls with different keys do not share', async () => {
  const gates = new Map<string, Deferred<string>>();
  const m = memoizeAsync((k: string) => {
    const d = deferred<string>();
    gates.set(k, d);
    return d.promise;
  });
  const pa = m('a');
  const pb = m('b');
  await tick();
  assert.equal(gates.size, 2);
  gates.get('b')!.resolve('B');
  gates.get('a')!.resolve('A');
  assert.equal(await pa, 'A');
  assert.equal(await pb, 'B');
});

// -- error handling ----------------------------------------------------------

test('rejections are delivered to every in-flight caller', async () => {
  const d = deferred<string>();
  const m = memoizeAsync((_k: string) => d.promise);
  const p1 = m('k').catch((e: unknown) => e);
  const p2 = m('k').catch((e: unknown) => e);
  const boom = new Error('backend down');
  d.reject(boom);
  assert.equal(await p1, boom);
  assert.equal(await p2, boom);
});

test('rejections are not cached: the next call retries', async () => {
  let calls = 0;
  const m = memoizeAsync(async (_k: string) => {
    calls++;
    if (calls === 1) throw new Error('flaky');
    return 'second try';
  });
  await assert.rejects(m('k'), /flaky/);
  assert.equal(await m('k'), 'second try');
  assert.equal(await m('k'), 'second try');
  assert.equal(calls, 2);
});

// -- custom key function -----------------------------------------------------

test('a custom key function lets distinct objects share an entry', async () => {
  let calls = 0;
  const m = memoizeAsync(
    async (user: { id: number; name: string }) => {
      calls++;
      return `perm-set for #${user.id}`;
    },
    { key: (user) => user.id },
  );
  assert.equal(await m({ id: 7, name: 'ada' }), 'perm-set for #7');
  assert.equal(await m({ id: 7, name: 'grace' }), 'perm-set for #7');
  assert.equal(await m({ id: 8, name: 'alan' }), 'perm-set for #8');
  assert.equal(calls, 2);
});

// -- invalidation ------------------------------------------------------------

test('delete(arg) evicts one entry and reports whether it existed', async () => {
  let calls = 0;
  const m = memoizeAsync(async (k: string) => `${k}#${++calls}`);
  assert.equal(await m('a'), 'a#1');
  assert.equal(await m('b'), 'b#2');
  assert.equal(m.delete('a'), true);
  assert.equal(m.delete('missing'), false);
  assert.equal(await m('a'), 'a#3'); // reloaded
  assert.equal(await m('b'), 'b#2'); // untouched
});

test('delete works for object keys too', async () => {
  let calls = 0;
  const m = memoizeAsync(async (_q: object) => ++calls);
  const q = { scope: 'all' };
  assert.equal(await m(q), 1);
  assert.equal(m.delete(q), true);
  assert.equal(await m(q), 2);
});

test('clear() empties the whole cache', async () => {
  let calls = 0;
  const m = memoizeAsync(async (_k: string) => ++calls);
  await m('a');
  await m('b');
  m.clear();
  await m('a');
  await m('b');
  assert.equal(calls, 4);
});

test('a flight that was cleared mid-air must not poison the fresh entry', async () => {
  const first = deferred<string>();
  const second = deferred<string>();
  let calls = 0;
  const m = memoizeAsync((_k: string) => {
    calls++;
    return calls === 1 ? first.promise : second.promise;
  });
  const stale = m('cfg'); // flight #1
  await tick();
  m.clear(); // e.g. settings changed while the load was running
  const fresh = m('cfg'); // flight #2, must call fn again
  await tick();
  assert.equal(calls, 2);
  first.resolve('stale value'); // late arrival from the old world
  await tick();
  second.resolve('fresh value');
  assert.equal(await stale, 'stale value'); // old callers keep their answer
  assert.equal(await fresh, 'fresh value');
  assert.equal(await m('cfg'), 'fresh value'); // cache holds the fresh one
  assert.equal(calls, 2);
});
