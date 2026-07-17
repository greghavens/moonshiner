import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PreviewLoader } from './preview_loader.ts';
import type { Fetcher } from './preview_loader.ts';

const unhandledReasons: unknown[] = [];
process.on('unhandledRejection', (reason) => {
  unhandledReasons.push(reason);
});

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

async function tick(turns = 12): Promise<void> {
  for (let i = 0; i < turns; i++) await Promise.resolve();
}

async function macrotasks(rounds = 4): Promise<void> {
  for (let i = 0; i < rounds; i++) await new Promise((r) => setImmediate(r));
}

// A fetch stand-in that settles when the test says so and, like real fetch,
// rejects as soon as the signal aborts.
function cancellableFetch(gate: Deferred<string>, calls: { count: number }): Fetcher {
  return (signal: AbortSignal) => {
    calls.count += 1;
    return new Promise<string>((res, rej) => {
      if (signal.aborted) {
        rej(new Error('request aborted'));
        return;
      }
      signal.addEventListener('abort', () => rej(new Error('request aborted')), { once: true });
      gate.promise.then(res, rej);
    });
  };
}

// A fetch stand-in that ignores the signal entirely (a transport that cannot
// cancel); it settles only when the gate does.
function stubbornFetch(gate: Deferred<string>, calls: { count: number }): Fetcher {
  return () => {
    calls.count += 1;
    return gate.promise;
  };
}

test('a clean load ends loaded with exactly two transitions', async () => {
  const gate = deferred<string>();
  const calls = { count: 0 };
  const loader = new PreviewLoader();
  const p = loader.load(cancellableFetch(gate, calls), new AbortController().signal);
  await tick();
  gate.resolve('inline-png:v1');
  const snap = await p;
  assert.deepEqual(snap, { state: 'loaded', data: 'inline-png:v1', error: null });
  assert.deepEqual(loader.transitions, ['loading', 'loaded']);
  assert.equal(calls.count, 1);
});

test('an already-aborted signal never starts the fetch', async () => {
  const gate = deferred<string>();
  const calls = { count: 0 };
  const controller = new AbortController();
  controller.abort();
  const loader = new PreviewLoader();
  const p = loader.load(cancellableFetch(gate, calls), controller.signal);
  await tick();
  assert.equal(calls.count, 0, 'the fetcher ran despite a pre-aborted signal');
  const snap = await p;
  assert.deepEqual(snap, { state: 'aborted', data: null, error: 'aborted' });
  assert.deepEqual(loader.transitions, ['aborted']);
});

test('aborting mid-flight lands on aborted, not failed', async () => {
  const gate = deferred<string>();
  const calls = { count: 0 };
  const controller = new AbortController();
  const loader = new PreviewLoader();
  const p = loader.load(cancellableFetch(gate, calls), controller.signal);
  await tick();
  assert.equal(loader.state, 'loading');
  controller.abort();
  const snap = await p;
  assert.deepEqual(snap, { state: 'aborted', data: null, error: 'aborted' });
  assert.deepEqual(loader.transitions, ['loading', 'aborted'], 'expected exactly one terminal transition');
});

test('a late result from a non-cancellable transport cannot revive an aborted load', async () => {
  const gate = deferred<string>();
  const calls = { count: 0 };
  const controller = new AbortController();
  const loader = new PreviewLoader();
  const p = loader.load(stubbornFetch(gate, calls), controller.signal);
  await tick();
  controller.abort();
  await tick();
  assert.equal(loader.state, 'aborted');
  gate.resolve('late-body');
  const snap = await p;
  assert.deepEqual(snap, { state: 'aborted', data: null, error: 'aborted' });
  assert.deepEqual(loader.transitions, ['loading', 'aborted']);
});

test('an abort arriving after the fetch resolved must not clobber the result', async () => {
  const gate = deferred<string>();
  const calls = { count: 0 };
  const controller = new AbortController();
  const loader = new PreviewLoader();
  const p = loader.load(cancellableFetch(gate, calls), controller.signal);
  await tick();
  gate.resolve('thumb:page-1');
  const snap = await p;
  assert.equal(snap.state, 'loaded');
  controller.abort();
  await tick();
  assert.deepEqual(loader.snapshot(), { state: 'loaded', data: 'thumb:page-1', error: null });
  assert.deepEqual(loader.transitions, ['loading', 'loaded'], 'a settled load transitioned again');
});

test('a fetch failure reports failed once and stays failed through a later abort', async () => {
  const gate = deferred<string>();
  const calls = { count: 0 };
  const controller = new AbortController();
  const loader = new PreviewLoader();
  const p = loader.load(stubbornFetch(gate, calls), controller.signal);
  await tick();
  gate.reject(new Error('backend 500'));
  const snap = await p;
  assert.deepEqual(snap, { state: 'failed', data: null, error: 'backend 500' });
  controller.abort();
  await tick();
  assert.deepEqual(loader.snapshot(), { state: 'failed', data: null, error: 'backend 500' });
  assert.deepEqual(loader.transitions, ['loading', 'failed']);
});

test('no unhandled rejections escape any load', async () => {
  await macrotasks();
  assert.deepEqual(unhandledReasons, []);
});
