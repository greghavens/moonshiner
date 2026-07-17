// Acceptance tests for the rate-limit-aware request scheduler.
//
// Every scenario runs against one or more scripted mock hosts on
// 127.0.0.1 ephemeral ports. Responses are scripted per call, requests
// are recorded in arrival order, and all waiting goes through an
// injectable clock + sleeper — nothing here sleeps for real and nothing
// leaves the loopback interface.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as http from 'node:http';
import type { AddressInfo } from 'node:net';
import { once } from 'node:events';
import { RateGate } from './rategate.ts';

interface StepSpec {
  status?: number;
  headers?: Record<string, string>;
  body?: string;
  hold?: boolean; // do not respond until release() is called
}

interface Hit {
  path: string;
  method: string;
  body: string;
}

interface Mock {
  url: string; // http://127.0.0.1:<port>
  hits: Hit[];
  waitForHits: (n: number) => Promise<void>;
  release: () => void; // respond to the oldest held request
  close: () => Promise<void>;
}

function mockHost(steps: StepSpec[]): Promise<Mock> {
  const hits: Hit[] = [];
  const held: Array<() => void> = [];
  const waiters: Array<{ n: number; resolve: () => void }> = [];

  const server = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on('data', (c: Buffer) => chunks.push(c));
    req.on('end', () => {
      const index = hits.length;
      hits.push({
        path: req.url ?? '',
        method: req.method ?? '',
        body: Buffer.concat(chunks).toString('utf8'),
      });
      const step: StepSpec = index < steps.length
        ? steps[index]
        : { status: 599, body: '{"error":"script exhausted: unexpected extra request"}' };
      for (const w of [...waiters]) {
        if (hits.length >= w.n) {
          w.resolve();
          waiters.splice(waiters.indexOf(w), 1);
        }
      }
      const respond = () => {
        res.writeHead(step.status ?? 200, {
          'content-type': 'application/json',
          ...(step.headers ?? {}),
        });
        res.end(step.body ?? '{}');
      };
      if (step.hold) held.push(respond);
      else respond();
    });
  });

  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address() as AddressInfo;
      resolve({
        url: `http://127.0.0.1:${port}`,
        hits,
        waitForHits: (n: number) =>
          new Promise<void>((res2, rej2) => {
            if (hits.length >= n) {
              res2();
              return;
            }
            const timer = setTimeout(
              () => rej2(new Error(`still waiting for request #${n}; saw ${hits.length}`)),
              10_000,
            );
            waiters.push({ n, resolve: () => { clearTimeout(timer); res2(); } });
          }),
        release: () => {
          const r = held.shift();
          if (r) r();
        },
        close: async () => {
          for (const r of held.splice(0)) r();
          server.closeAllConnections();
          server.close();
          await once(server, 'close');
        },
      });
    });
  });
}

const T0 = Date.parse('2026-01-01T00:00:00.000Z');

function fakeTime(startMs: number = T0) {
  let t = startMs;
  const sleeps: number[] = [];
  return {
    now: () => t,
    sleep: async (ms: number) => {
      sleeps.push(ms);
      t += ms; // waiting advances the injected clock
    },
    sleeps,
  };
}

// A short real-time grace window: long enough for a misbehaving scheduler
// to reveal an extra dispatch over loopback, never awaited by a correct one.
function settle(ms: number = 50): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Bounded poll for a condition that a correct implementation reaches quickly.
async function pollUntil(cond: () => boolean, what: string): Promise<void> {
  const deadline = Date.now() + 10_000;
  while (!cond()) {
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);
    await settle(5);
  }
}

test('a plain 200 passes straight through: status, body, headers, request init', async () => {
  const mock = await mockHost([
    { status: 200, headers: { 'x-request-id': 'req-77' }, body: '{"ok":true}' },
  ]);
  const time = fakeTime();
  try {
    const gate = new RateGate({ now: time.now, sleep: time.sleep });
    const res = await gate.request(`${mock.url}/v1/orders`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: '{"sku":"A-100","qty":2}',
    });
    assert.equal(res.status, 200);
    assert.equal(res.body, '{"ok":true}');
    assert.equal(res.headers['x-request-id'], 'req-77');
    assert.deepEqual(mock.hits, [
      { path: '/v1/orders', method: 'POST', body: '{"sku":"A-100","qty":2}' },
    ]);
    assert.deepEqual(time.sleeps, []);
  } finally {
    await mock.close();
  }
});

test('same-host requests dispatch FIFO, one in flight at a time by default', async () => {
  const mock = await mockHost([
    { hold: true, body: '{"n":1}' },
    { hold: true, body: '{"n":2}' },
    { hold: true, body: '{"n":3}' },
  ]);
  const time = fakeTime();
  try {
    const gate = new RateGate({ now: time.now, sleep: time.sleep });
    const completions: string[] = [];
    const p1 = gate.request(`${mock.url}/r1`).then((r) => { completions.push('/r1'); return r; });
    const p2 = gate.request(`${mock.url}/r2`).then((r) => { completions.push('/r2'); return r; });
    const p3 = gate.request(`${mock.url}/r3`).then((r) => { completions.push('/r3'); return r; });

    await mock.waitForHits(1);
    await settle();
    assert.deepEqual(mock.hits.map((h) => h.path), ['/r1'],
      'r2 must not be dispatched while r1 is still in flight');

    mock.release();
    await mock.waitForHits(2);
    await settle();
    assert.deepEqual(mock.hits.map((h) => h.path), ['/r1', '/r2']);

    mock.release();
    await mock.waitForHits(3);
    mock.release();
    const [r1, r2, r3] = await Promise.all([p1, p2, p3]);
    assert.deepEqual([r1.body, r2.body, r3.body], ['{"n":1}', '{"n":2}', '{"n":3}']);
    assert.deepEqual(completions, ['/r1', '/r2', '/r3'], 'completion follows enqueue order');
    assert.deepEqual(time.sleeps, []);
  } finally {
    await mock.close();
  }
});

test('429 with delta-seconds Retry-After: wait exactly that long, then retry', async () => {
  const mock = await mockHost([
    { status: 429, headers: { 'retry-after': '3' }, body: '{"error":"slow down"}' },
    { status: 200, body: '{"ok":true}' },
  ]);
  const time = fakeTime();
  try {
    const gate = new RateGate({ now: time.now, sleep: time.sleep });
    const res = await gate.request(`${mock.url}/v1/items`);
    assert.equal(res.status, 200);
    assert.equal(mock.hits.length, 2);
    assert.deepEqual(time.sleeps, [3000], 'one sleeper call for exactly 3000ms');
  } finally {
    await mock.close();
  }
});

test('429 with HTTP-date Retry-After: wait until that instant on the injected clock', async () => {
  const mock = await mockHost([
    { status: 429, headers: { 'retry-after': 'Thu, 01 Jan 2026 00:00:10 GMT' } },
    { status: 200, body: '{"ok":true}' },
  ]);
  const time = fakeTime(T0); // clock reads 2026-01-01T00:00:00Z
  try {
    const gate = new RateGate({ now: time.now, sleep: time.sleep });
    const res = await gate.request(`${mock.url}/v1/items`);
    assert.equal(res.status, 200);
    assert.equal(mock.hits.length, 2);
    assert.deepEqual(time.sleeps, [10_000]);
  } finally {
    await mock.close();
  }
});

test('429 with an HTTP-date already in the past: retry immediately, sleeper never called', async () => {
  const mock = await mockHost([
    { status: 429, headers: { 'retry-after': 'Wed, 31 Dec 2025 23:59:55 GMT' } },
    { status: 200, body: '{"ok":true}' },
  ]);
  const time = fakeTime(T0);
  try {
    const gate = new RateGate({ now: time.now, sleep: time.sleep });
    const res = await gate.request(`${mock.url}/v1/items`);
    assert.equal(res.status, 200);
    assert.equal(mock.hits.length, 2);
    assert.deepEqual(time.sleeps, [], 'a non-positive wait must skip the sleeper entirely');
  } finally {
    await mock.close();
  }
});

test('429 with a missing or unparseable Retry-After falls back to defaultRetryMs', async () => {
  const mock = await mockHost([
    { status: 429 }, // no Retry-After at all
    { status: 429, headers: { 'retry-after': 'soon' } }, // not a number, not a date
    { status: 200, body: '{"ok":true}' },
  ]);
  const time = fakeTime();
  try {
    const gate = new RateGate({ now: time.now, sleep: time.sleep, defaultRetryMs: 750 });
    const res = await gate.request(`${mock.url}/v1/items`);
    assert.equal(res.status, 200);
    assert.equal(mock.hits.length, 3);
    assert.deepEqual(time.sleeps, [750, 750]);
  } finally {
    await mock.close();
  }
});

test('rate-limit retries stop at maxRetries and hand back the final 429', async () => {
  const mock = await mockHost([
    { status: 429, headers: { 'retry-after': '1' }, body: '{"error":"limited"}' },
    { status: 429, headers: { 'retry-after': '1' }, body: '{"error":"limited"}' },
    { status: 429, headers: { 'retry-after': '1' }, body: '{"error":"still limited"}' },
  ]);
  const time = fakeTime();
  try {
    const gate = new RateGate({ now: time.now, sleep: time.sleep, maxRetries: 2 });
    const res = await gate.request(`${mock.url}/v1/items`);
    assert.equal(res.status, 429, 'budget exhausted: the caller gets the last response');
    assert.equal(res.body, '{"error":"still limited"}');
    assert.equal(mock.hits.length, 3, 'initial attempt + exactly 2 retries');
    assert.deepEqual(time.sleeps, [1000, 1000], 'no wait after the final attempt');
  } finally {
    await mock.close();
  }
});

test('a rate-limited request keeps its place at the head of the host queue', async () => {
  const mock = await mockHost([
    { status: 429, headers: { 'retry-after': '2' } }, // /a first try
    { status: 200, body: '{"which":"a"}' },           // /a retry
    { status: 200, body: '{"which":"b"}' },           // /b
  ]);
  const time = fakeTime();
  try {
    const gate = new RateGate({ now: time.now, sleep: time.sleep });
    const pa = gate.request(`${mock.url}/a`);
    const pb = gate.request(`${mock.url}/b`);
    const [ra, rb] = await Promise.all([pa, pb]);
    assert.equal(ra.status, 200);
    assert.equal(rb.status, 200);
    assert.deepEqual(mock.hits.map((h) => h.path), ['/a', '/a', '/b'],
      'the retry must run before anything queued behind it');
    assert.deepEqual(time.sleeps, [2000]);
  } finally {
    await mock.close();
  }
});

test('X-RateLimit-Remaining: 0 pauses the host until the reset before the NEXT dispatch', async () => {
  const resetSec = Math.floor((T0 + 5000) / 1000);
  const mock = await mockHost([
    {
      status: 200,
      headers: { 'x-ratelimit-remaining': '0', 'x-ratelimit-reset': String(resetSec) },
      body: '{"n":1}',
    },
    { status: 200, body: '{"n":2}' },
  ]);
  const time = fakeTime(T0);
  try {
    const gate = new RateGate({ now: time.now, sleep: time.sleep });
    const p1 = gate.request(`${mock.url}/r1`);
    const p2 = gate.request(`${mock.url}/r2`);
    const [r1, r2] = await Promise.all([p1, p2]);
    assert.equal(r1.body, '{"n":1}');
    assert.equal(r2.body, '{"n":2}');
    assert.deepEqual(mock.hits.map((h) => h.path), ['/r1', '/r2']);
    assert.deepEqual(time.sleeps, [5000], 'one pause covering now -> reset');
  } finally {
    await mock.close();
  }
});

test('remaining budget above zero never pauses', async () => {
  const resetSec = Math.floor((T0 + 60_000) / 1000);
  const mock = await mockHost([
    {
      status: 200,
      headers: { 'x-ratelimit-remaining': '7', 'x-ratelimit-reset': String(resetSec) },
      body: '{"n":1}',
    },
    { status: 200, body: '{"n":2}' },
  ]);
  const time = fakeTime(T0);
  try {
    const gate = new RateGate({ now: time.now, sleep: time.sleep });
    await gate.request(`${mock.url}/r1`);
    await gate.request(`${mock.url}/r2`);
    assert.equal(mock.hits.length, 2);
    assert.deepEqual(time.sleeps, []);
  } finally {
    await mock.close();
  }
});

test('a reset instant already behind the clock does not pause and skips the sleeper', async () => {
  const resetSec = Math.floor((T0 - 1000) / 1000);
  const mock = await mockHost([
    {
      status: 200,
      headers: { 'x-ratelimit-remaining': '0', 'x-ratelimit-reset': String(resetSec) },
      body: '{"n":1}',
    },
    { status: 200, body: '{"n":2}' },
  ]);
  const time = fakeTime(T0);
  try {
    const gate = new RateGate({ now: time.now, sleep: time.sleep });
    await gate.request(`${mock.url}/r1`);
    const r2 = await gate.request(`${mock.url}/r2`);
    assert.equal(r2.body, '{"n":2}');
    assert.deepEqual(time.sleeps, []);
  } finally {
    await mock.close();
  }
});

test('a 429 carrying both Retry-After and X-RateLimit headers sleeps exactly once', async () => {
  const resetSec = Math.floor((T0 + 60_000) / 1000);
  const mock = await mockHost([
    {
      status: 429,
      headers: {
        'retry-after': '4',
        'x-ratelimit-remaining': '0',
        'x-ratelimit-reset': String(resetSec),
      },
    },
    { status: 200, body: '{"ok":true}' },
  ]);
  const time = fakeTime(T0);
  try {
    const gate = new RateGate({ now: time.now, sleep: time.sleep });
    const res = await gate.request(`${mock.url}/v1/items`);
    assert.equal(res.status, 200);
    assert.deepEqual(time.sleeps, [4000],
      'Retry-After governs a 429; the X-RateLimit pair must not add a second pause');
  } finally {
    await mock.close();
  }
});

test('hosts are independent: one host waiting out a 429 never stalls another', async () => {
  const mockA = await mockHost([
    { status: 429, headers: { 'retry-after': '60' } },
    { status: 200, body: '{"host":"a"}' },
  ]);
  const mockB = await mockHost([{ status: 200, body: '{"host":"b"}' }]);
  try {
    let t = T0;
    const sleeps: number[] = [];
    let releaseLongSleep: (() => void) | null = null;
    const longSleepHeld = new Promise<void>((resolve) => {
      releaseLongSleep = () => resolve();
    });
    const sleep = (ms: number): Promise<void> => {
      sleeps.push(ms);
      if (ms >= 60_000) {
        return longSleepHeld.then(() => { t += ms; });
      }
      t += ms;
      return Promise.resolve();
    };
    const gate = new RateGate({ now: () => t, sleep });

    let aDone = false;
    const pa = gate.request(`${mockA.url}/slow`).then((r) => { aDone = true; return r; });
    const rb = await gate.request(`${mockB.url}/fast`);
    assert.equal(rb.body, '{"host":"b"}', 'host B answered while host A was paused');
    await pollUntil(() => sleeps.length === 1, "host A's Retry-After pause");
    assert.equal(aDone, false, 'host A must still be waiting out its Retry-After');
    assert.deepEqual(sleeps, [60_000]);

    releaseLongSleep!();
    const ra = await pa;
    assert.equal(ra.status, 200);
    assert.equal(ra.body, '{"host":"a"}');
  } finally {
    await mockA.close();
    await mockB.close();
  }
});

test('non-429 failures pass through untouched: no retry, no sleep', async () => {
  const mock = await mockHost([
    { status: 500, body: '{"error":"backend"}' },
    { status: 404, body: '{"error":"missing"}' },
  ]);
  const time = fakeTime();
  try {
    const gate = new RateGate({ now: time.now, sleep: time.sleep });
    const r1 = await gate.request(`${mock.url}/boom`);
    const r2 = await gate.request(`${mock.url}/nope`);
    assert.equal(r1.status, 500);
    assert.equal(r2.status, 404);
    assert.equal(mock.hits.length, 2, 'each failure hit the server exactly once');
    assert.deepEqual(time.sleeps, []);
  } finally {
    await mock.close();
  }
});

test('hostConcurrency 2 runs two in flight and queues the third', async () => {
  const mock = await mockHost([
    { hold: true, body: '{"n":1}' },
    { hold: true, body: '{"n":2}' },
    { hold: true, body: '{"n":3}' },
  ]);
  const time = fakeTime();
  try {
    const gate = new RateGate({ now: time.now, sleep: time.sleep, hostConcurrency: 2 });
    const p1 = gate.request(`${mock.url}/r1`);
    const p2 = gate.request(`${mock.url}/r2`);
    const p3 = gate.request(`${mock.url}/r3`);

    await mock.waitForHits(2);
    await settle();
    assert.deepEqual(mock.hits.map((h) => h.path), ['/r1', '/r2'],
      'the third request must wait for a free slot');

    mock.release(); // finish r1 -> slot frees -> r3 dispatches
    await mock.waitForHits(3);
    assert.deepEqual(mock.hits.map((h) => h.path), ['/r1', '/r2', '/r3']);
    mock.release();
    mock.release();
    const [r1, r2, r3] = await Promise.all([p1, p2, p3]);
    assert.deepEqual([r1.body, r2.body, r3.body], ['{"n":1}', '{"n":2}', '{"n":3}']);
  } finally {
    await mock.close();
  }
});
