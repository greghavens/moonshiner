import { test } from 'node:test';
import assert from 'node:assert/strict';
import { flushReadings } from './flush.ts';
import type { Reading } from './flush.ts';

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

async function macrotasks(rounds = 5): Promise<void> {
  for (let i = 0; i < rounds; i++) await new Promise((r) => setImmediate(r));
}

function rd(id: string, value: number): Reading {
  return { id, value };
}

class FakeStore {
  gates = new Map<string, Deferred<{ id: string }>>();
  started: string[] = [];
  inFlight = 0;
  maxInFlight = 0;

  write(reading: Reading): Promise<{ id: string }> {
    this.started.push(reading.id);
    this.inFlight++;
    this.maxInFlight = Math.max(this.maxInFlight, this.inFlight);
    const gate = deferred<{ id: string }>();
    this.gates.set(reading.id, gate);
    return gate.promise.finally(() => {
      this.inFlight--;
    });
  }

  finish(id: string): void {
    this.gates.get(id)!.resolve({ id });
  }

  fail(id: string, message: string): void {
    this.gates.get(id)!.reject(new Error(message));
  }
}

test('the report is only returned after every write has settled', async () => {
  const store = new FakeStore();
  let settled = false;
  const p = flushReadings(store, [rd('r1', 1), rd('r2', 2), rd('r3', 3)], { concurrency: 3 }).then(
    (report) => {
      settled = true;
      return report;
    },
  );
  await tick();
  assert.equal(settled, false, 'flush resolved before any write finished');
  store.finish('r1');
  store.finish('r2');
  await tick();
  assert.equal(settled, false, 'flush resolved while a write was still pending');
  store.finish('r3');
  const report = await p;
  assert.deepEqual(report, { ok: ['r1', 'r2', 'r3'], failed: [] });
});

test('ok ids come back in input order even when writes finish backwards', async () => {
  const store = new FakeStore();
  const p = flushReadings(store, [rd('a', 1), rd('b', 2), rd('c', 3), rd('d', 4)], {
    concurrency: 4,
  });
  await tick();
  store.finish('d');
  store.finish('c');
  store.finish('b');
  store.finish('a');
  const report = await p;
  assert.deepEqual(report.ok, ['a', 'b', 'c', 'd']);
});

test('failed writes are collected in the report, not thrown and not dropped', async () => {
  const store = new FakeStore();
  const readings = [rd('r1', 1), rd('r2', 2), rd('r3', 3), rd('r4', 4), rd('r5', 5)];
  const p = flushReadings(store, readings, { concurrency: 5 });
  await tick();
  store.finish('r1');
  store.fail('r2', 'disk full');
  store.finish('r3');
  store.fail('r4', 'quota exceeded');
  store.finish('r5');
  const report = await p;
  assert.deepEqual(report, {
    ok: ['r1', 'r3', 'r5'],
    failed: [
      { id: 'r2', reason: 'disk full' },
      { id: 'r4', reason: 'quota exceeded' },
    ],
  });
});

test('the concurrency cap holds while writes are pending', async () => {
  const store = new FakeStore();
  const readings = [rd('r1', 1), rd('r2', 2), rd('r3', 3), rd('r4', 4), rd('r5', 5)];
  const p = flushReadings(store, readings, { concurrency: 2 });
  await tick();
  assert.deepEqual(store.started, ['r1', 'r2'], 'more writes started than the cap allows');
  store.finish('r1');
  await tick();
  assert.deepEqual(store.started, ['r1', 'r2', 'r3']);
  store.finish('r2');
  store.finish('r3');
  await tick();
  store.finish('r4');
  store.finish('r5');
  const report = await p;
  assert.equal(store.maxInFlight, 2);
  assert.deepEqual(report.ok, ['r1', 'r2', 'r3', 'r4', 'r5']);
});

test('an empty batch resolves with an empty report', async () => {
  const store = new FakeStore();
  assert.deepEqual(await flushReadings(store, []), { ok: [], failed: [] });
});

test('no unhandled rejections escape a flush', async () => {
  await macrotasks();
  assert.deepEqual(unhandledReasons, []);
});
