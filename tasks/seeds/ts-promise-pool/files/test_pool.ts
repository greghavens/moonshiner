import { test } from 'node:test';
import assert from 'node:assert/strict';
import { runPool, runPoolSettled } from './pool.ts';

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

/** Drain enough microtask turns that any settled-promise chains have run. */
async function tick(turns = 10): Promise<void> {
  for (let i = 0; i < turns; i++) await Promise.resolve();
}

// -- runPool -----------------------------------------------------------------

test('results come back in input order, not completion order', async () => {
  const ds = [deferred<string>(), deferred<string>(), deferred<string>()];
  const out = runPool(ds.map((d) => () => d.promise), 3);
  ds[2].resolve('c');
  ds[0].resolve('a');
  ds[1].resolve('b');
  assert.deepEqual(await out, ['a', 'b', 'c']);
});

test('never runs more than limit tasks at once', async () => {
  const ds = Array.from({ length: 6 }, () => deferred<number>());
  let active = 0;
  let peak = 0;
  const tasks = ds.map((d, i) => async () => {
    active++;
    peak = Math.max(peak, active);
    const v = await d.promise;
    active--;
    return v;
  });
  const out = runPool(tasks, 2);
  await tick();
  assert.equal(active, 2);
  for (let i = 0; i < ds.length; i++) {
    ds[i].resolve(i);
    await tick();
  }
  assert.deepEqual(await out, [0, 1, 2, 3, 4, 5]);
  assert.equal(peak, 2);
});

test('a task is not invoked until a slot frees up', async () => {
  const ds = [deferred<number>(), deferred<number>(), deferred<number>()];
  const started: number[] = [];
  const tasks = ds.map((d, i) => () => {
    started.push(i);
    return d.promise;
  });
  const out = runPool(tasks, 2);
  await tick();
  assert.deepEqual(started, [0, 1]);
  ds[1].resolve(11);
  await tick();
  assert.deepEqual(started, [0, 1, 2]);
  ds[0].resolve(10);
  ds[2].resolve(12);
  assert.deepEqual(await out, [10, 11, 12]);
});

test('limit 1 runs strictly sequentially', async () => {
  const events: string[] = [];
  const tasks = [0, 1, 2].map((i) => async () => {
    events.push(`start:${i}`);
    await tick(3);
    events.push(`end:${i}`);
    return i;
  });
  assert.deepEqual(await runPool(tasks, 1), [0, 1, 2]);
  assert.deepEqual(events, [
    'start:0', 'end:0',
    'start:1', 'end:1',
    'start:2', 'end:2',
  ]);
});

test('empty task list resolves to an empty array', async () => {
  assert.deepEqual(await runPool([], 4), []);
});

test('limit larger than the task count is fine', async () => {
  const tasks = [1, 2, 3].map((n) => async () => n * 10);
  assert.deepEqual(await runPool(tasks, 100), [10, 20, 30]);
});

test('rejects with the first failure', async () => {
  const boom = new Error('task 1 exploded');
  const tasks = [
    async () => 'ok',
    async () => { throw boom; },
    async () => 'also ok',
  ];
  await assert.rejects(runPool(tasks, 3), boom);
});

test('after a failure, queued tasks are never started', async () => {
  const ds = [deferred<string>(), deferred<string>()];
  const started: number[] = [];
  const tasks = [
    () => { started.push(0); return ds[0].promise; },
    () => { started.push(1); return ds[1].promise; },
    async () => { started.push(2); return 'never'; },
    async () => { started.push(3); return 'never'; },
  ];
  const out = runPool(tasks, 2);
  await tick();
  ds[0].reject(new Error('down'));
  await assert.rejects(out, /down/);
  ds[1].resolve('late');
  await tick();
  assert.deepEqual(started, [0, 1]);
});

test('non-integer or non-positive limits throw a TypeError synchronously', () => {
  const tasks = [async () => 1];
  assert.throws(() => runPool(tasks, 0), TypeError);
  assert.throws(() => runPool(tasks, -2), TypeError);
  assert.throws(() => runPool(tasks, 1.5), TypeError);
  assert.throws(() => runPoolSettled(tasks, 0), TypeError);
});

// -- runPoolSettled ----------------------------------------------------------

test('settled mode reports every outcome in input order and never rejects', async () => {
  const boom = new Error('flaky');
  const tasks = [
    async () => 'first',
    async () => { throw boom; },
    async () => 'third',
  ];
  const out = await runPoolSettled(tasks, 2);
  assert.deepEqual(out, [
    { status: 'fulfilled', value: 'first' },
    { status: 'rejected', reason: boom },
    { status: 'fulfilled', value: 'third' },
  ]);
});

test('settled mode keeps running remaining tasks after a failure', async () => {
  const started: number[] = [];
  const tasks = [
    async () => { started.push(0); throw new Error('early'); },
    async () => { started.push(1); return 'b'; },
    async () => { started.push(2); return 'c'; },
  ];
  const out = await runPoolSettled(tasks, 1);
  assert.deepEqual(started, [0, 1, 2]);
  assert.equal(out[0].status, 'rejected');
  assert.equal(out[1].status, 'fulfilled');
  assert.equal(out[2].status, 'fulfilled');
});

test('settled mode also respects the concurrency limit', async () => {
  let active = 0;
  let peak = 0;
  const tasks = Array.from({ length: 5 }, (_, i) => async () => {
    active++;
    peak = Math.max(peak, active);
    await tick(3);
    active--;
    return i;
  });
  await runPoolSettled(tasks, 2);
  assert.equal(peak, 2);
});
