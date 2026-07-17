// Acceptance tests for the bulk-upload client.
//
// The client chunks a list of items into batches of at most N, POSTs each
// batch to the server, parses per-item 207-style success/failure responses,
// and retries only the failed items with capped exponential backoff.  The
// final ItemReport list is ordered by the original submission index.
//
// Every scenario runs against a scripted mock server on an ephemeral port.
// All backoff waits go through an injectable sleeper — nothing here actually
// sleeps.
//
// Run: node --test test_bulkship.ts

import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as http from 'node:http';
import type { AddressInfo } from 'node:net';
import { once } from 'node:events';
import { BulkClient } from './bulkship.ts';

// ---------------------------------------------------------------------------
// Mock server
// ---------------------------------------------------------------------------

interface BatchResponse {
  results: Array<{ id: string; ok: boolean; error?: string }>;
}

interface StepSpec {
  // status to respond with (default 200)
  status?: number;
  // full batch response body
  body: BatchResponse | string; // string for raw/error cases
}

interface RequestRecord {
  body: string;
  parsed: unknown;
}

interface MockServer {
  url: string;
  hits: RequestRecord[];
  waitForHits(n: number): Promise<void>;
  close(): Promise<void>;
}

function mockServer(steps: StepSpec[]): Promise<MockServer> {
  const hits: RequestRecord[] = [];
  const waiters: Array<{ n: number; resolve: () => void }> = [];

  const server = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on('data', (c: Buffer) => chunks.push(c));
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      let parsed: unknown;
      try { parsed = JSON.parse(raw); } catch { parsed = null; }
      hits.push({ body: raw, parsed });

      for (const w of [...waiters]) {
        if (hits.length >= w.n) {
          w.resolve();
          waiters.splice(waiters.indexOf(w), 1);
        }
      }

      const idx = hits.length - 1;
      const step: StepSpec = idx < steps.length
        ? steps[idx]
        : { status: 599, body: '{"error":"script exhausted"}' as unknown as BatchResponse };

      const status = step.status ?? 200;
      const bodyStr = typeof step.body === 'string'
        ? step.body
        : JSON.stringify(step.body);
      const payload = Buffer.from(bodyStr, 'utf8');
      res.writeHead(status, {
        'Content-Type': 'application/json',
        'Content-Length': String(payload.length),
      });
      res.end(payload);
    });
  });

  server.listen(0, '127.0.0.1');

  return once(server, 'listening').then(() => {
    const { port } = server.address() as AddressInfo;
    return {
      url: `http://127.0.0.1:${port}`,
      hits,
      waitForHits(n: number) {
        if (hits.length >= n) return Promise.resolve();
        return new Promise<void>((resolve) => waiters.push({ n, resolve }));
      },
      close(): Promise<void> {
        return new Promise<void>((resolve, reject) => {
          server.close((err) => (err ? reject(err) : resolve()));
        });
      },
    };
  });
}

// ---------------------------------------------------------------------------
// Injectable time / sleeper
// ---------------------------------------------------------------------------

function fakeTime() {
  let t = 0;
  const sleeps: number[] = [];
  return {
    get t() { return t; },
    sleeps,
    sleep(ms: number): Promise<void> {
      sleeps.push(ms);
      t += ms;
      return Promise.resolve();
    },
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test('all items succeed — single batch, no retries', async () => {
  const mock = await mockServer([
    {
      body: {
        results: [
          { id: 'item-0', ok: true },
          { id: 'item-1', ok: true },
          { id: 'item-2', ok: true },
        ],
      },
    },
  ]);
  const time = fakeTime();
  const client = new BulkClient({
    url: `${mock.url}/upload`,
    batchSize: 10,
    sleep: (ms) => time.sleep(ms),
  });
  try {
    const items = [
      { id: 'item-0', data: 'a' },
      { id: 'item-1', data: 'b' },
      { id: 'item-2', data: 'c' },
    ];
    const report = await client.upload(items);
    assert.equal(mock.hits.length, 1, 'exactly one batch request');
    assert.deepEqual(
      JSON.parse(mock.hits[0].body),
      { items },
      'batch body must be {items:[...]}',
    );
    assert.equal(report.length, 3);
    assert.deepEqual(report.map((r) => r.ok), [true, true, true]);
    assert.equal(report[0].id, 'item-0');
    assert.deepEqual(time.sleeps, [], 'no sleeps on full success');
  } finally {
    await mock.close();
  }
});

test('chunking: items split into batches of at most batchSize', async () => {
  const mock = await mockServer([
    {
      body: {
        results: [
          { id: 'a', ok: true },
          { id: 'b', ok: true },
        ],
      },
    },
    {
      body: {
        results: [
          { id: 'c', ok: true },
        ],
      },
    },
  ]);
  const time = fakeTime();
  const client = new BulkClient({
    url: `${mock.url}/upload`,
    batchSize: 2,
    sleep: (ms) => time.sleep(ms),
  });
  try {
    const items = [
      { id: 'a', data: 1 },
      { id: 'b', data: 2 },
      { id: 'c', data: 3 },
    ];
    const report = await client.upload(items);
    assert.equal(mock.hits.length, 2, 'two batches for 3 items at size 2');
    const b0 = JSON.parse(mock.hits[0].body) as { items: typeof items };
    const b1 = JSON.parse(mock.hits[1].body) as { items: typeof items };
    assert.equal(b0.items.length, 2);
    assert.equal(b1.items.length, 1);
    assert.equal(report.length, 3);
    // stable ordering: original index order
    assert.deepEqual(
      report.map((r) => r.id),
      ['a', 'b', 'c'],
    );
  } finally {
    await mock.close();
  }
});

test('partial failure: failed items retried, success items not re-sent', async () => {
  const mock = await mockServer([
    // First batch: item-0 ok, item-1 fails, item-2 ok
    {
      body: {
        results: [
          { id: 'item-0', ok: true },
          { id: 'item-1', ok: false, error: 'transient' },
          { id: 'item-2', ok: true },
        ],
      },
    },
    // Retry of item-1 only
    {
      body: {
        results: [
          { id: 'item-1', ok: true },
        ],
      },
    },
  ]);
  const time = fakeTime();
  const client = new BulkClient({
    url: `${mock.url}/upload`,
    batchSize: 10,
    sleep: (ms) => time.sleep(ms),
    initialBackoffMs: 100,
  });
  try {
    const items = [
      { id: 'item-0', v: 1 },
      { id: 'item-1', v: 2 },
      { id: 'item-2', v: 3 },
    ];
    const report = await client.upload(items);
    assert.equal(mock.hits.length, 2, 'initial batch + one retry batch');
    // retry payload must contain only the failed item
    const retryBatch = JSON.parse(mock.hits[1].body) as { items: unknown[] };
    assert.equal(retryBatch.items.length, 1, 'retry sends only the failed item');
    assert.deepEqual(
      (retryBatch.items[0] as { id: string }).id,
      'item-1',
    );
    // final report: all three items, in original order
    assert.equal(report.length, 3);
    assert.deepEqual(report.map((r) => r.id), ['item-0', 'item-1', 'item-2']);
    assert.deepEqual(report.map((r) => r.ok), [true, true, true]);
    // one sleep before the retry
    assert.equal(time.sleeps.length, 1);
    assert.equal(time.sleeps[0], 100);
  } finally {
    await mock.close();
  }
});

test('backoff doubles on each retry attempt, capped at maxBackoffMs', async () => {
  // item-1 fails 3 times before succeeding; check sleep progression
  const mock = await mockServer([
    {
      body: {
        results: [
          { id: 'x', ok: false, error: 'e1' },
        ],
      },
    },
    {
      body: {
        results: [
          { id: 'x', ok: false, error: 'e2' },
        ],
      },
    },
    {
      body: {
        results: [
          { id: 'x', ok: false, error: 'e3' },
        ],
      },
    },
    {
      body: {
        results: [
          { id: 'x', ok: true },
        ],
      },
    },
  ]);
  const time = fakeTime();
  const client = new BulkClient({
    url: `${mock.url}/upload`,
    batchSize: 10,
    sleep: (ms) => time.sleep(ms),
    initialBackoffMs: 50,
    maxBackoffMs: 150,
    maxRetries: 5,
  });
  try {
    const report = await client.upload([{ id: 'x', v: 9 }]);
    assert.equal(report.length, 1);
    assert.equal(report[0].ok, true);
    // sleeps: 50, 100, 150 (capped; would be 200)
    assert.deepEqual(time.sleeps, [50, 100, 150]);
  } finally {
    await mock.close();
  }
});

test('item permanently fails after maxRetries — reported as failed in final output', async () => {
  const mock = await mockServer([
    { body: { results: [{ id: 'bad', ok: false, error: 'disk full' }] } },
    { body: { results: [{ id: 'bad', ok: false, error: 'disk full' }] } },
    { body: { results: [{ id: 'bad', ok: false, error: 'disk full' }] } },
  ]);
  const time = fakeTime();
  const client = new BulkClient({
    url: `${mock.url}/upload`,
    batchSize: 10,
    sleep: (ms) => time.sleep(ms),
    initialBackoffMs: 10,
    maxRetries: 2,
  });
  try {
    const report = await client.upload([{ id: 'bad', v: 0 }]);
    assert.equal(report.length, 1);
    assert.equal(report[0].ok, false);
    assert.equal(typeof report[0].error, 'string', 'error field must be present');
    // 1 initial + 2 retries = 3 requests total
    assert.equal(mock.hits.length, 3);
  } finally {
    await mock.close();
  }
});

test('mixed batch: some items always succeed, some always fail, correct final ordering', async () => {
  // items: [alpha, beta, gamma, delta] — beta and delta always fail
  // batchSize=4 so everything goes in one batch
  const mock = await mockServer([
    {
      body: {
        results: [
          { id: 'alpha', ok: true },
          { id: 'beta', ok: false, error: 'bad-1' },
          { id: 'gamma', ok: true },
          { id: 'delta', ok: false, error: 'bad-2' },
        ],
      },
    },
    // retry of beta+delta — both still fail
    {
      body: {
        results: [
          { id: 'beta', ok: false, error: 'bad-1' },
          { id: 'delta', ok: false, error: 'bad-2' },
        ],
      },
    },
  ]);
  const time = fakeTime();
  const client = new BulkClient({
    url: `${mock.url}/upload`,
    batchSize: 4,
    sleep: (ms) => time.sleep(ms),
    initialBackoffMs: 20,
    maxRetries: 1,
  });
  try {
    const items = [
      { id: 'alpha', n: 1 },
      { id: 'beta',  n: 2 },
      { id: 'gamma', n: 3 },
      { id: 'delta', n: 4 },
    ];
    const report = await client.upload(items);
    // original order preserved
    assert.deepEqual(report.map((r) => r.id), ['alpha', 'beta', 'gamma', 'delta']);
    assert.deepEqual(report.map((r) => r.ok), [true, false, true, false]);
    // retry batch must include only beta and delta
    const retry = JSON.parse(mock.hits[1].body) as { items: Array<{ id: string }> };
    assert.deepEqual(retry.items.map((i) => i.id), ['beta', 'delta']);
  } finally {
    await mock.close();
  }
});

test('server error (non-200 HTTP status) throws — items not silently lost', async () => {
  const mock = await mockServer([
    { status: 503, body: '{"error":"overloaded"}' as unknown as BatchResponse },
  ]);
  const time = fakeTime();
  const client = new BulkClient({
    url: `${mock.url}/upload`,
    batchSize: 10,
    sleep: (ms) => time.sleep(ms),
  });
  try {
    await assert.rejects(
      () => client.upload([{ id: 'x', v: 1 }]),
      (err: unknown) => {
        assert.ok(err instanceof Error, 'must throw an Error');
        assert.ok((err as Error).message.includes('503'), 'message must contain status code');
        return true;
      },
    );
  } finally {
    await mock.close();
  }
});

test('empty upload returns empty report without hitting server', async () => {
  const mock = await mockServer([]);
  const time = fakeTime();
  const client = new BulkClient({
    url: `${mock.url}/upload`,
    batchSize: 10,
    sleep: (ms) => time.sleep(ms),
  });
  try {
    const report = await client.upload([]);
    assert.equal(report.length, 0);
    assert.equal(mock.hits.length, 0, 'no HTTP requests for empty input');
  } finally {
    await mock.close();
  }
});

test('items within a batch are sent in their original submission order', async () => {
  const mock = await mockServer([
    {
      body: {
        results: [
          { id: 'z', ok: true },
          { id: 'a', ok: true },
          { id: 'm', ok: true },
        ],
      },
    },
  ]);
  const time = fakeTime();
  const client = new BulkClient({
    url: `${mock.url}/upload`,
    batchSize: 10,
    sleep: (ms) => time.sleep(ms),
  });
  try {
    const items = [
      { id: 'z', seq: 0 },
      { id: 'a', seq: 1 },
      { id: 'm', seq: 2 },
    ];
    await client.upload(items);
    const sent = JSON.parse(mock.hits[0].body) as { items: typeof items };
    assert.deepEqual(
      sent.items.map((i) => i.id),
      ['z', 'a', 'm'],
      'batch body must preserve submission order',
    );
  } finally {
    await mock.close();
  }
});
