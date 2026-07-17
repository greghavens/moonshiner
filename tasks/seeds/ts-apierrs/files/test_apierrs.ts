// Tests for the provider-error taxonomy and the importer built on it.
// Run: node --test test_apierrs.ts
//
// The mock provider is a local node:http server fed a script of responses,
// served one per request in order (the final step repeats if the importer
// keeps calling). Time is injected everywhere: sleeps are recorded and the
// fake clock advanced — nothing here actually waits.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';

import { classify, retryAfterMs, Importer } from './apierrs.ts';

type Step = { status: number; headers?: Record<string, string>; body?: unknown };
type Hit = { path: string; body: unknown };

async function mockProvider(steps: Step[]) {
  const hits: Hit[] = [];
  const server = createServer((req, res) => {
    let data = '';
    req.on('data', (chunk) => {
      data += chunk;
    });
    req.on('end', () => {
      hits.push({ path: req.url ?? '', body: data === '' ? null : JSON.parse(data) });
      const step = steps[Math.min(hits.length - 1, steps.length - 1)];
      res.writeHead(step.status, { 'content-type': 'application/json', ...(step.headers ?? {}) });
      res.end(JSON.stringify(step.body ?? {}));
    });
  });
  await new Promise<void>((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve());
  });
  const { port } = server.address() as AddressInfo;
  return {
    url: `http://127.0.0.1:${port}`,
    hits,
    close: () =>
      new Promise<void>((resolve) => {
        server.closeAllConnections();
        server.close(() => resolve());
      }),
  };
}

const T0 = Date.parse('2026-02-03T10:00:00.000Z');

function fakeTime(startMs: number) {
  let t = startMs;
  const sleeps: number[] = [];
  return {
    now: () => t,
    sleep: (ms: number) => {
      sleeps.push(ms);
      t += ms;
      return Promise.resolve();
    },
    sleeps,
  };
}

const vErr = { error: { code: 'validation_failed', message: 'email is not a valid address' } };
const qErr = { error: { code: 'rate_limited', message: 'request quota exceeded' } };

// ---------------------------------------------------------------- taxonomy

test('classify pins the provider taxonomy', () => {
  // The request itself is the problem: resending changes nothing.
  for (const status of [400, 401, 403, 404, 409]) {
    assert.equal(classify(status, null).kind, 'permanent', `status ${status}`);
  }
  // The condition clears on its own: worth retrying.
  for (const status of [408, 500, 502, 503, 504]) {
    assert.equal(classify(status, null).kind, 'transient', `status ${status}`);
  }
  // An invalid record stays invalid no matter how often we resend it.
  assert.deepEqual(classify(422, vErr), {
    kind: 'permanent',
    status: 422,
    code: 'validation_failed',
  });
  // Throttling is the textbook retryable condition.
  assert.deepEqual(classify(429, qErr), {
    kind: 'transient',
    status: 429,
    code: 'rate_limited',
  });
  // Undocumented statuses: 5xx retryable, everything else not.
  assert.equal(classify(599, null).kind, 'transient');
  assert.equal(classify(418, null).kind, 'permanent');
  assert.equal(classify(400, 'not-an-object').code, '');
});

test('retryAfterMs understands both provider flavors', () => {
  assert.equal(retryAfterMs('2', T0), 2000);
  assert.equal(retryAfterMs(' 15 ', T0), 15000);
  assert.equal(retryAfterMs('Tue, 03 Feb 2026 10:00:05 GMT', T0), 5000);
  assert.equal(retryAfterMs('Tue, 03 Feb 2026 09:59:00 GMT', T0), 0); // already past
  assert.equal(retryAfterMs(null, T0), null);
  assert.equal(retryAfterMs('soon', T0), null);
});

// ---------------------------------------------------------------- importer

test('a clean record imports on the first request', async () => {
  const api = await mockProvider([{ status: 201, body: { id: 'rec-1' } }]);
  const time = fakeTime(T0);
  try {
    const importer = new Importer(api.url, { now: time.now, sleep: time.sleep });
    const outcome = await importer.importRecord({ name: 'Kim Reyes', email: 'kim@example.com' });
    assert.deepEqual(outcome, { ok: true, id: 'rec-1', attempts: 1 });
    assert.equal(api.hits.length, 1);
    assert.equal(api.hits[0].path, '/records');
    assert.deepEqual(api.hits[0].body, { name: 'Kim Reyes', email: 'kim@example.com' });
    assert.deepEqual(time.sleeps, []);
  } finally {
    await api.close();
  }
});

test('an invalid record is rejected once — no retry, no waiting', async () => {
  const api = await mockProvider([{ status: 422, body: vErr }]);
  const time = fakeTime(T0);
  try {
    const importer = new Importer(api.url, { now: time.now, sleep: time.sleep });
    const outcome = await importer.importRecord({ name: 'No Email' });
    assert.deepEqual(outcome, {
      ok: false,
      kind: 'permanent',
      status: 422,
      code: 'validation_failed',
      attempts: 1,
    });
    assert.equal(api.hits.length, 1, 'an invalid record must hit the API exactly once');
    assert.deepEqual(time.sleeps, [], 'nothing to wait for — the record will still be invalid');
  } finally {
    await api.close();
  }
});

test('a throttled request waits out Retry-After (delta-seconds) and succeeds', async () => {
  const api = await mockProvider([
    { status: 429, headers: { 'Retry-After': '2' }, body: qErr },
    { status: 201, body: { id: 'rec-2' } },
  ]);
  const time = fakeTime(T0);
  try {
    const importer = new Importer(api.url, { now: time.now, sleep: time.sleep });
    const outcome = await importer.importRecord({ name: 'Ada' });
    assert.deepEqual(outcome, { ok: true, id: 'rec-2', attempts: 2 });
    assert.deepEqual(time.sleeps, [2000]);
    assert.equal(api.hits.length, 2);
  } finally {
    await api.close();
  }
});

test('Retry-After as an HTTP-date is honored against the injected clock', async () => {
  const api = await mockProvider([
    { status: 429, headers: { 'Retry-After': 'Tue, 03 Feb 2026 10:00:05 GMT' }, body: qErr },
    { status: 201, body: { id: 'rec-3' } },
  ]);
  const time = fakeTime(T0);
  try {
    const importer = new Importer(api.url, { now: time.now, sleep: time.sleep });
    const outcome = await importer.importRecord({ name: 'Grace' });
    assert.deepEqual(outcome, { ok: true, id: 'rec-3', attempts: 2 });
    assert.deepEqual(time.sleeps, [5000]);
    assert.equal(api.hits.length, 2);
  } finally {
    await api.close();
  }
});

test('sustained throttling pauses the default between tries, then gives up as transient', async () => {
  const api = await mockProvider([{ status: 429, body: qErr }]); // never a Retry-After
  const time = fakeTime(T0);
  try {
    const importer = new Importer(api.url, {
      now: time.now,
      sleep: time.sleep,
      maxRetries: 2,
      defaultRetryMs: 300,
    });
    const outcome = await importer.importRecord({ name: 'Bea' });
    assert.deepEqual(outcome, {
      ok: false,
      kind: 'transient',
      status: 429,
      code: 'rate_limited',
      attempts: 3,
    });
    assert.equal(api.hits.length, 3);
    assert.deepEqual(time.sleeps, [300, 300]);
  } finally {
    await api.close();
  }
});

test('a provider fault is retried', async () => {
  const api = await mockProvider([
    { status: 500, body: { error: { code: 'internal', message: 'upstream hiccup' } } },
    { status: 201, body: { id: 'rec-4' } },
  ]);
  const time = fakeTime(T0);
  try {
    const importer = new Importer(api.url, {
      now: time.now,
      sleep: time.sleep,
      defaultRetryMs: 250,
    });
    const outcome = await importer.importRecord({ name: 'Lin' });
    assert.deepEqual(outcome, { ok: true, id: 'rec-4', attempts: 2 });
    assert.deepEqual(time.sleeps, [250]);
    assert.equal(api.hits.length, 2);
  } finally {
    await api.close();
  }
});

test('a malformed request fails fast', async () => {
  const api = await mockProvider([
    { status: 400, body: { error: { code: 'bad_request', message: 'body is not valid JSON' } } },
  ]);
  const time = fakeTime(T0);
  try {
    const importer = new Importer(api.url, { now: time.now, sleep: time.sleep });
    const outcome = await importer.importRecord({ name: 'Mal' });
    assert.deepEqual(outcome, {
      ok: false,
      kind: 'permanent',
      status: 400,
      code: 'bad_request',
      attempts: 1,
    });
    assert.equal(api.hits.length, 1);
    assert.deepEqual(time.sleeps, []);
  } finally {
    await api.close();
  }
});

test('a batch keeps going: the throttled record is the one resent, the invalid one is not', async () => {
  const api = await mockProvider([
    { status: 201, body: { id: 'rec-1' } },
    { status: 422, body: vErr },
    { status: 429, headers: { 'Retry-After': '1' }, body: qErr },
    { status: 201, body: { id: 'rec-3' } },
  ]);
  const time = fakeTime(T0);
  try {
    const importer = new Importer(api.url, {
      now: time.now,
      sleep: time.sleep,
      defaultRetryMs: 750,
    });
    const result = await importer.importBatch([
      { name: 'alpha' },
      { name: 'bravo' },
      { name: 'charlie' },
    ]);
    assert.deepEqual(result, { imported: 2, rejected: 1 });
    assert.deepEqual(
      api.hits.map((hit) => (hit.body as { name?: string }).name),
      ['alpha', 'bravo', 'charlie', 'charlie'],
      'the invalid record goes over the wire once; the throttled one gets the second try',
    );
    assert.deepEqual(time.sleeps, [1000]);
  } finally {
    await api.close();
  }
});
