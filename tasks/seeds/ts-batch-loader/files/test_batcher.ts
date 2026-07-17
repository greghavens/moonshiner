import { test } from 'node:test';
import assert from 'node:assert/strict';
import { BatchLoader } from './batcher.ts';

// -- helpers -----------------------------------------------------------------

function recordingBatchFn<K>(lookup: (key: K) => unknown) {
  const batches: K[][] = [];
  const batchFn = async (keys: K[]) => {
    batches.push([...keys]);
    return keys.map((k) => lookup(k));
  };
  return { batches, batchFn };
}

async function tick(turns = 10): Promise<void> {
  for (let i = 0; i < turns; i++) await Promise.resolve();
}

/** A schedule hook the test controls by hand. */
function manualSchedule() {
  const queued: Array<() => void> = [];
  return {
    queued,
    schedule: (flush: () => void) => {
      queued.push(flush);
    },
    runAll() {
      while (queued.length > 0) queued.shift()!();
    },
  };
}

// -- batching windows --------------------------------------------------------

test('same-tick loads coalesce into a single batchFn call', async () => {
  const { batches, batchFn } = recordingBatchFn((id: number) => `user-${id}`);
  const loader = new BatchLoader(batchFn);
  const [a, b, c] = await Promise.all([loader.load(1), loader.load(2), loader.load(3)]);
  assert.deepEqual([a, b, c], ['user-1', 'user-2', 'user-3']);
  assert.deepEqual(batches, [[1, 2, 3]]);
});

test('keys are batched in first-request order', async () => {
  const { batches, batchFn } = recordingBatchFn((k: string) => k.toUpperCase());
  const loader = new BatchLoader(batchFn);
  await Promise.all([loader.load('b'), loader.load('a'), loader.load('c')]);
  assert.deepEqual(batches, [['b', 'a', 'c']]);
});

test('each caller receives the result matching its own key', async () => {
  const { batchFn } = recordingBatchFn((id: number) => ({ id, name: `n${id}` }));
  const loader = new BatchLoader(batchFn);
  const [second, first] = await Promise.all([loader.load(2), loader.load(1)]);
  assert.deepEqual(second, { id: 2, name: 'n2' });
  assert.deepEqual(first, { id: 1, name: 'n1' });
});

test('loads issued in a later tick form a new batch, with no caching in between', async () => {
  const { batches, batchFn } = recordingBatchFn((id: number) => `v${id}`);
  const loader = new BatchLoader(batchFn);
  await Promise.all([loader.load(1), loader.load(2)]);
  await Promise.all([loader.load(2), loader.load(3)]); // key 2 again: refetched
  assert.deepEqual(batches, [[1, 2], [2, 3]]);
});

test('nothing dispatches until the injected schedule fires the flush', async () => {
  const { batches, batchFn } = recordingBatchFn((k: string) => k);
  const sched = manualSchedule();
  const loader = new BatchLoader(batchFn, { schedule: sched.schedule });
  const p1 = loader.load('x');
  const p2 = loader.load('y');
  assert.equal(sched.queued.length, 1, 'one window, one scheduled flush');
  assert.deepEqual(batches, []);
  sched.runAll();
  assert.deepEqual(await Promise.all([p1, p2]), ['x', 'y']);
  assert.deepEqual(batches, [['x', 'y']]);
});

// -- duplicate keys ----------------------------------------------------------

test('duplicate keys in one window collapse to a single entry', async () => {
  const { batches, batchFn } = recordingBatchFn((id: number) => ({ id }));
  const loader = new BatchLoader(batchFn);
  const [r1, r2] = await Promise.all([loader.load(7), loader.load(7)]);
  assert.deepEqual(batches, [[7]]);
  assert.equal(r1, r2, 'both callers share the very same result object');
});

// -- max batch size ----------------------------------------------------------

test('hitting maxBatchSize dispatches the full batch at once, overflow starts a new window', async () => {
  const { batches, batchFn } = recordingBatchFn((id: number) => id * 10);
  const sched = manualSchedule();
  const loader = new BatchLoader(batchFn, { maxBatchSize: 2, schedule: sched.schedule });
  const p1 = loader.load(1);
  const p2 = loader.load(2); // fills the batch: dispatches without waiting
  const p3 = loader.load(3); // new window
  await tick();
  assert.deepEqual(batches, [[1, 2]], 'full batch went out before any flush was run');
  sched.runAll(); // fires both windows' flushes; the first must not re-dispatch
  await tick();
  assert.deepEqual(batches, [[1, 2], [3]]);
  assert.deepEqual(await Promise.all([p1, p2, p3]), [10, 20, 30]);
});

test('maxBatchSize must be a positive integer', () => {
  const ok = async (keys: number[]) => keys;
  assert.throws(() => new BatchLoader(ok, { maxBatchSize: 0 }), TypeError);
  assert.throws(() => new BatchLoader(ok, { maxBatchSize: 2.5 }), TypeError);
});

// -- error handling ----------------------------------------------------------

test('an Error element rejects only the matching key', async () => {
  const missing = new Error('row 2 not found');
  const loader = new BatchLoader(async (ids: number[]) =>
    ids.map((id) => (id === 2 ? missing : `row-${id}`)),
  );
  const results = await Promise.all([
    loader.load(1).catch((e: unknown) => e),
    loader.load(2).catch((e: unknown) => e),
    loader.load(3).catch((e: unknown) => e),
  ]);
  assert.deepEqual(results, ['row-1', missing, 'row-3']);
});

test('a rejected batchFn rejects every key in the batch with that reason', async () => {
  const outage = new Error('replica lag');
  const loader = new BatchLoader(async (_ids: number[]) => {
    throw outage;
  });
  const [e1, e2] = await Promise.all([
    loader.load(1).catch((e: unknown) => e),
    loader.load(2).catch((e: unknown) => e),
  ]);
  assert.equal(e1, outage);
  assert.equal(e2, outage);
});

test('a result array of the wrong length rejects the whole batch loudly', async () => {
  const loader = new BatchLoader(async (ids: number[]) => ids.slice(1)); // one short
  const [e1, e2] = await Promise.all([
    loader.load(1).catch((e: unknown) => e),
    loader.load(2).catch((e: unknown) => e),
  ]);
  for (const e of [e1, e2]) {
    assert.ok(e instanceof Error);
    assert.match((e as Error).message, /2/); // mentions the expected count
  }
});

test('a failed batch is not cached: the next window retries the keys', async () => {
  let attempt = 0;
  const loader = new BatchLoader(async (ids: number[]) => {
    attempt++;
    if (attempt === 1) throw new Error('first window fails');
    return ids.map((id) => `ok-${id}`);
  });
  await assert.rejects(loader.load(5), /first window fails/);
  assert.equal(await loader.load(5), 'ok-5');
});
