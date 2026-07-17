// Acceptance tests for the webhook-subscription lifecycle manager.
//
// The manager tracks local webhook subscriptions.  For each subscription it:
//   - registers with the hub (POST /subscriptions → {id, ttl_ms})
//   - schedules a renewal at 80% of ttl_ms
//   - on renewal, PATCHes the subscription (PATCH /subscriptions/:id → {id, ttl_ms})
//   - on reconcile, compares local state to GET /subscriptions, then
//       re-registers any that the hub lost and deregisters any the hub
//       holds that we don't know about (DELETE /subscriptions/:id)
//   - on shutdown, DELETEs every known subscription (revoke-all)
//
// All scheduling goes through an injectable Scheduler so tests advance
// time without real sleeps.  The mock hub runs on 127.0.0.1 ephemeral port.
//
// Run: node --test test_webhooksub.ts

import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as http from 'node:http';
import type { AddressInfo } from 'node:net';
import { once } from 'node:events';
import { WebhookManager } from './webhooksub.ts';

// ---------------------------------------------------------------------------
// Shared types
// ---------------------------------------------------------------------------

interface SubRecord {
  id: string;
  ttl_ms: number;
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// Mock hub
// ---------------------------------------------------------------------------

interface HubOptions {
  // Initial set of subscriptions known to the hub (for reconcile tests)
  initialSubs?: SubRecord[];
  // Scripted per-call responses per route key "METHOD /path-prefix"
  // Each entry is consumed in order for that key.
  scripts?: Record<string, Array<{ status?: number; body: unknown }>>;
}

interface HubRequest {
  method: string;
  path: string;
  body: string;
  parsed: unknown;
}

interface Hub {
  url: string;
  requests: HubRequest[];
  subs: Map<string, SubRecord>; // server-side sub store (id → record)
  close(): Promise<void>;
}

function makeHub(opts: HubOptions = {}): Promise<Hub> {
  const subs = new Map<string, SubRecord>(
    (opts.initialSubs ?? []).map((s) => [s.id, s]),
  );
  const scripts: Record<string, Array<{ status?: number; body: unknown }>> =
    opts.scripts ?? {};
  const requests: HubRequest[] = [];
  let nextId = 1;

  const server = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on('data', (c: Buffer) => chunks.push(c));
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      let parsed: unknown = null;
      try { parsed = JSON.parse(raw); } catch { /* ignore */ }
      requests.push({ method: req.method ?? '', path: req.url ?? '', body: raw, parsed });

      const method = req.method ?? '';
      const urlPath = req.url ?? '';

      function respond(status: number, body: unknown) {
        const payload = Buffer.from(JSON.stringify(body), 'utf8');
        res.writeHead(status, {
          'Content-Type': 'application/json',
          'Content-Length': String(payload.length),
        });
        res.end(payload);
      }

      // Check scripts first
      for (const key of Object.keys(scripts)) {
        const [sm, sp] = key.split(' ');
        if (sm === method && urlPath.startsWith(sp)) {
          const arr = scripts[key];
          if (arr.length > 0) {
            const step = arr.shift()!;
            respond(step.status ?? 200, step.body);
            return;
          }
        }
      }

      // Default routing
      if (method === 'POST' && urlPath === '/subscriptions') {
        const body = parsed as { callback_url?: string; events?: string[]; ttl_ms?: number } | null;
        const id = `sub-${nextId++}`;
        const ttl_ms = (body as { ttl_ms?: number })?.ttl_ms ?? 60_000;
        const rec: SubRecord = { id, ttl_ms, ...(body as object ?? {}) };
        subs.set(id, rec);
        respond(201, rec);
        return;
      }

      if (method === 'PATCH' && urlPath.startsWith('/subscriptions/')) {
        const id = urlPath.slice('/subscriptions/'.length);
        if (!subs.has(id)) {
          respond(404, { error: 'not found' });
          return;
        }
        const body = parsed as Partial<SubRecord> | null;
        const existing = subs.get(id)!;
        const updated: SubRecord = { ...existing, ...(body ?? {}), id };
        subs.set(id, updated);
        respond(200, updated);
        return;
      }

      if (method === 'GET' && urlPath === '/subscriptions') {
        respond(200, { subscriptions: [...subs.values()] });
        return;
      }

      if (method === 'DELETE' && urlPath.startsWith('/subscriptions/')) {
        const id = urlPath.slice('/subscriptions/'.length);
        subs.delete(id);
        respond(204, null);
        return;
      }

      respond(404, { error: 'not found' });
    });
  });

  server.listen(0, '127.0.0.1');

  return once(server, 'listening').then(() => {
    const { port } = server.address() as AddressInfo;
    return {
      url: `http://127.0.0.1:${port}`,
      requests,
      subs,
      close(): Promise<void> {
        return new Promise((resolve, reject) =>
          server.close((err) => (err ? reject(err) : resolve())),
        );
      },
    };
  });
}

// ---------------------------------------------------------------------------
// Fake scheduler
// ---------------------------------------------------------------------------

type ScheduledTask = { delayMs: number; callback: () => Promise<void> | void };

function fakeScheduler() {
  const pending: ScheduledTask[] = [];
  return {
    pending,
    /**
     * The injectable `schedule` function given to WebhookManager.
     * Captures the task instead of actually setting a timer.
     */
    schedule(delayMs: number, callback: () => Promise<void> | void): void {
      pending.push({ delayMs, callback });
    },
    /** Fire all pending tasks in insertion order, then clear the list. */
    async fireAll(): Promise<void> {
      const tasks = pending.splice(0);
      for (const t of tasks) {
        await t.callback();
      }
    },
    /** Fire the next pending task only. */
    async fireNext(): Promise<void> {
      const task = pending.shift();
      if (task) await task.callback();
    },
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test('register posts to /subscriptions and stores returned id + ttl', async () => {
  const hub = await makeHub();
  const sched = fakeScheduler();
  const mgr = new WebhookManager({ baseUrl: hub.url, schedule: (ms, cb) => sched.schedule(ms, cb) });
  try {
    const sub = await mgr.register({
      callback_url: 'https://app.example/hook',
      events: ['order.created'],
      ttl_ms: 60_000,
    });
    assert.equal(typeof sub.id, 'string', 'register must return an id');
    assert.equal(sub.ttl_ms, 60_000);
    assert.equal(hub.requests.length, 1);
    assert.equal(hub.requests[0].method, 'POST');
    assert.equal(hub.requests[0].path, '/subscriptions');
    const sent = hub.requests[0].parsed as { callback_url: string; events: string[]; ttl_ms: number };
    assert.equal(sent.callback_url, 'https://app.example/hook');
    assert.deepEqual(sent.events, ['order.created']);
    assert.equal(sent.ttl_ms, 60_000);
  } finally {
    await mgr.shutdown();
    await hub.close();
  }
});

test('renewal is scheduled at 80% of ttl_ms', async () => {
  const hub = await makeHub();
  const sched = fakeScheduler();
  const mgr = new WebhookManager({ baseUrl: hub.url, schedule: (ms, cb) => sched.schedule(ms, cb) });
  try {
    await mgr.register({ callback_url: 'https://app.example/hook', events: ['x'], ttl_ms: 100_000 });
    assert.equal(sched.pending.length, 1, 'one renewal task must be scheduled');
    assert.equal(sched.pending[0].delayMs, 80_000, '80% of 100_000 is 80_000');
  } finally {
    await mgr.shutdown();
    await hub.close();
  }
});

test('renewal fires PATCH and reschedules the next renewal', async () => {
  const hub = await makeHub();
  const sched = fakeScheduler();
  const mgr = new WebhookManager({ baseUrl: hub.url, schedule: (ms, cb) => sched.schedule(ms, cb) });
  try {
    const sub = await mgr.register({ callback_url: 'https://app.example/hook', events: ['x'], ttl_ms: 60_000 });
    assert.equal(sched.pending.length, 1);

    // Fire the renewal
    await sched.fireAll();

    // Should have sent PATCH /subscriptions/:id
    const patch = hub.requests.find((r) => r.method === 'PATCH');
    assert.ok(patch, 'PATCH must be sent on renewal');
    assert.equal(patch.path, `/subscriptions/${sub.id}`);

    // Must have rescheduled next renewal
    assert.equal(sched.pending.length, 1, 'next renewal must be re-scheduled');
    // The PATCH response carries the same ttl_ms (60_000), so next renewal delay is 80% = 48_000
    assert.equal(sched.pending[0].delayMs, 48_000);
  } finally {
    await mgr.shutdown();
    await hub.close();
  }
});

test('reconcile re-registers subscriptions the hub lost', async () => {
  const hub = await makeHub();
  const sched = fakeScheduler();
  const mgr = new WebhookManager({ baseUrl: hub.url, schedule: (ms, cb) => sched.schedule(ms, cb) });
  try {
    const sub = await mgr.register({ callback_url: 'https://app.example/hook', events: ['x'], ttl_ms: 60_000 });
    // Simulate hub losing the subscription
    hub.subs.delete(sub.id);
    const reqsBefore = hub.requests.length;

    await mgr.reconcile();

    // Must have called GET /subscriptions
    const getReq = hub.requests.slice(reqsBefore).find((r) => r.method === 'GET' && r.path === '/subscriptions');
    assert.ok(getReq, 'reconcile must GET /subscriptions');

    // Must have re-registered (POST /subscriptions)
    const reReg = hub.requests.slice(reqsBefore).find((r) => r.method === 'POST' && r.path === '/subscriptions');
    assert.ok(reReg, 'must re-register the lost subscription');

    // Hub now has the subscription again
    assert.equal(hub.subs.size, 1, 'hub should have the subscription back');
  } finally {
    await mgr.shutdown();
    await hub.close();
  }
});

test('reconcile deregisters subscriptions the hub has that we do not know about', async () => {
  // Hub already has a "ghost" subscription from a previous process run
  const ghost: SubRecord = { id: 'ghost-1', ttl_ms: 30_000, callback_url: 'https://app.example/hook' };
  const hub = await makeHub({ initialSubs: [ghost] });
  const sched = fakeScheduler();
  const mgr = new WebhookManager({ baseUrl: hub.url, schedule: (ms, cb) => sched.schedule(ms, cb) });
  try {
    // Manager has no local subscriptions — hub has one ghost
    await mgr.reconcile();

    // Must have sent DELETE /subscriptions/ghost-1
    const del = hub.requests.find((r) => r.method === 'DELETE' && r.path === `/subscriptions/${ghost.id}`);
    assert.ok(del, 'must DELETE the unknown remote subscription');
    assert.equal(hub.subs.size, 0, 'hub should have no subscriptions left');
  } finally {
    await mgr.shutdown();
    await hub.close();
  }
});

test('reconcile handles both directions in a single pass', async () => {
  // Hub has a ghost + is missing one we know about
  const ghost: SubRecord = { id: 'ghost-99', ttl_ms: 30_000, callback_url: 'https://app.example/other' };
  const hub = await makeHub({ initialSubs: [ghost] });
  const sched = fakeScheduler();
  const mgr = new WebhookManager({ baseUrl: hub.url, schedule: (ms, cb) => sched.schedule(ms, cb) });
  try {
    const sub = await mgr.register({ callback_url: 'https://app.example/hook', events: ['y'], ttl_ms: 45_000 });
    // Hub gets our register but then we simulate the hub also forgetting it
    hub.subs.delete(sub.id);
    const reqsBefore = hub.requests.length;

    await mgr.reconcile();

    const newReqs = hub.requests.slice(reqsBefore);
    const reReg = newReqs.find((r) => r.method === 'POST');
    const del   = newReqs.find((r) => r.method === 'DELETE' && r.path.includes('ghost-99'));
    assert.ok(reReg, 're-registration must happen');
    assert.ok(del,   'ghost deletion must happen');
  } finally {
    await mgr.shutdown();
    await hub.close();
  }
});

test('shutdown sends DELETE for every known subscription', async () => {
  const hub = await makeHub();
  const sched = fakeScheduler();
  const mgr = new WebhookManager({ baseUrl: hub.url, schedule: (ms, cb) => sched.schedule(ms, cb) });
  try {
    const s1 = await mgr.register({ callback_url: 'https://app.example/h1', events: ['a'], ttl_ms: 60_000 });
    const s2 = await mgr.register({ callback_url: 'https://app.example/h2', events: ['b'], ttl_ms: 60_000 });
    const reqsBefore = hub.requests.length;

    await mgr.shutdown();

    const deletes = hub.requests.slice(reqsBefore).filter((r) => r.method === 'DELETE');
    const deletedIds = deletes.map((r) => r.path.replace('/subscriptions/', '')).sort();
    assert.deepEqual(deletedIds, [s1.id, s2.id].sort(), 'shutdown must DELETE every subscription');
    assert.equal(hub.subs.size, 0, 'hub must have no subscriptions after shutdown');
  } finally {
    await hub.close();
  }
});

test('shutdown is idempotent — second call is a no-op', async () => {
  const hub = await makeHub();
  const sched = fakeScheduler();
  const mgr = new WebhookManager({ baseUrl: hub.url, schedule: (ms, cb) => sched.schedule(ms, cb) });
  try {
    await mgr.register({ callback_url: 'https://app.example/h', events: ['e'], ttl_ms: 10_000 });
    await mgr.shutdown();
    const countAfterFirst = hub.requests.length;
    await mgr.shutdown(); // second shutdown must not throw or send more requests
    assert.equal(hub.requests.length, countAfterFirst, 'no extra requests on second shutdown');
  } finally {
    await hub.close();
  }
});

test('multiple independent subscriptions each get their own renewal schedule', async () => {
  const hub = await makeHub();
  const sched = fakeScheduler();
  const mgr = new WebhookManager({ baseUrl: hub.url, schedule: (ms, cb) => sched.schedule(ms, cb) });
  try {
    await mgr.register({ callback_url: 'https://a.example/h', events: ['a'], ttl_ms: 50_000 });
    await mgr.register({ callback_url: 'https://b.example/h', events: ['b'], ttl_ms: 100_000 });
    assert.equal(sched.pending.length, 2, 'two subscriptions → two renewal tasks');
    const delays = sched.pending.map((t) => t.delayMs).sort((a, b) => a - b);
    assert.deepEqual(delays, [40_000, 80_000], '80% of 50k and 100k');
  } finally {
    await mgr.shutdown();
    await hub.close();
  }
});

test('register failure propagates as a thrown error and no sub is stored', async () => {
  // Hub is scripted to reject the registration
  const hub = await makeHub({
    scripts: {
      'POST /subscriptions': [{ status: 503, body: { error: 'hub overloaded' } }],
    },
  });
  const sched = fakeScheduler();
  const mgr = new WebhookManager({ baseUrl: hub.url, schedule: (ms, cb) => sched.schedule(ms, cb) });
  try {
    await assert.rejects(
      () => mgr.register({ callback_url: 'https://app.example/h', events: ['x'], ttl_ms: 60_000 }),
      (err: unknown) => {
        assert.ok(err instanceof Error);
        assert.ok((err as Error).message.includes('503'), 'error must mention status 503');
        return true;
      },
    );
    // No renewal must be scheduled if registration failed
    assert.equal(sched.pending.length, 0, 'no renewal scheduled after failed register');
    // shutdown must not try to DELETE anything
    await mgr.shutdown();
    const deletes = hub.requests.filter((r) => r.method === 'DELETE');
    assert.equal(deletes.length, 0, 'nothing to revoke on shutdown if register failed');
  } finally {
    await hub.close();
  }
});
