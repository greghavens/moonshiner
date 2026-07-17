import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PushClient } from './push_client.ts';
import type { ClientConfig, ConfigSource, PushResponse, Transport } from './push_client.ts';

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (v: T) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

async function tick(turns = 8): Promise<void> {
  for (let i = 0; i < turns; i++) await Promise.resolve();
}

class FakeSource implements ConfigSource {
  config: ClientConfig;

  constructor(config: ClientConfig) {
    this.config = config;
  }

  current(): ClientConfig {
    return this.config;
  }

  rotate(token: string): void {
    this.config = { ...this.config, token };
  }
}

interface RecordedCall {
  url: string;
  headers: Record<string, string>;
  body: string;
}

function recordingTransport(
  respond: (call: RecordedCall, index: number) => PushResponse,
): { calls: RecordedCall[]; fn: Transport } {
  const calls: RecordedCall[] = [];
  const fn: Transport = async (url, req) => {
    const call = { url, headers: { ...req.headers }, body: req.body };
    calls.push(call);
    return respond(call, calls.length - 1);
  };
  return { calls, fn };
}

const BASE: ClientConfig = {
  endpoint: 'https://metrics.internal',
  token: 'tok-fake-boot',
  orgId: 'org-42',
};

test('a token rotated between pushes is used by the next push', async () => {
  const source = new FakeSource({ ...BASE });
  const { calls, fn } = recordingTransport(() => ({ status: 200, body: '{"ok":true}' }));
  const client = new PushClient(source, fn);

  const first = await client.push('cpu.idle', 91);
  source.rotate('tok-fake-rotated');
  const second = await client.push('cpu.idle', 88);

  assert.deepEqual(first, { status: 200, attempts: 1, body: '{"ok":true}' });
  assert.deepEqual(second, { status: 200, attempts: 1, body: '{"ok":true}' });
  assert.deepEqual(
    calls.map((c) => c.headers.authorization),
    ['Bearer tok-fake-boot', 'Bearer tok-fake-rotated'],
    'the second push reused credentials from before the rotation',
  );
  assert.equal(calls[1].url, 'https://metrics.internal/v1/ingest');
  assert.equal(calls[1].body, '{"series":"cpu.idle","value":88}');
});

test('a 401 retry picks up the token the sidecar rotated in mid-request', async () => {
  const source = new FakeSource({ ...BASE, token: 'tok-fake-old' });
  const { calls, fn } = recordingTransport((call) => {
    if (call.headers.authorization === 'Bearer tok-fake-new') {
      return { status: 200, body: '{"ok":true}' };
    }
    source.rotate('tok-fake-new');
    return { status: 401, body: '{"error":"token expired"}' };
  });
  const client = new PushClient(source, fn);

  const result = await client.push('disk.free', 12);
  assert.deepEqual(result, { status: 200, attempts: 2, body: '{"ok":true}' });
  assert.deepEqual(
    calls.map((c) => c.headers.authorization),
    ['Bearer tok-fake-old', 'Bearer tok-fake-new'],
    'the retry re-sent the expired token instead of re-reading the source',
  );
});

test('concurrent pushes each snapshot the config at their own start', async () => {
  const source = new FakeSource({ ...BASE, token: 'tok-fake-one' });
  const calls: RecordedCall[] = [];
  const gates: Deferred<PushResponse>[] = [];
  const fn: Transport = (url, req) => {
    calls.push({ url, headers: { ...req.headers }, body: req.body });
    const gate = deferred<PushResponse>();
    gates.push(gate);
    return gate.promise;
  };
  const client = new PushClient(source, fn);

  const p1 = client.push('io.read', 1);
  await tick();
  source.rotate('tok-fake-two');
  const p2 = client.push('io.read', 2);
  await tick();
  assert.equal(gates.length, 2);
  gates[0].resolve({ status: 200, body: '{}' });
  gates[1].resolve({ status: 200, body: '{}' });
  await Promise.all([p1, p2]);

  assert.deepEqual(
    calls.map((c) => c.headers.authorization),
    ['Bearer tok-fake-one', 'Bearer tok-fake-two'],
  );
});

test('the retry ceiling is enforced and reported', async () => {
  const source = new FakeSource({ ...BASE });
  const { calls, fn } = recordingTransport(() => ({ status: 503, body: '{"error":"draining"}' }));
  const client = new PushClient(source, fn);
  await assert.rejects(client.push('net.rx', 4), /gave up after 3 attempts.*503/);
  assert.equal(calls.length, 3);

  const wider = recordingTransport(() => ({ status: 503, body: '{}' }));
  const patient = new PushClient(source, wider.fn, { maxAttempts: 5 });
  await assert.rejects(patient.push('net.rx', 4), /gave up after 5 attempts/);
  assert.equal(wider.calls.length, 5);
});

test('a non-retryable status returns immediately without retries', async () => {
  const source = new FakeSource({ ...BASE });
  const { calls, fn } = recordingTransport(() => ({ status: 400, body: '{"error":"bad series"}' }));
  const client = new PushClient(source, fn);
  const result = await client.push('bogus metric', 0);
  assert.deepEqual(result, { status: 400, attempts: 1, body: '{"error":"bad series"}' });
  assert.equal(calls.length, 1);
});

test('extra headers ride along but the caller bag is never mutated', async () => {
  const source = new FakeSource({ ...BASE });
  const { calls, fn } = recordingTransport(() => ({ status: 200, body: '{}' }));
  const client = new PushClient(source, fn);

  const extra = { 'x-trace-id': 'trace-77' };
  await client.push('mem.rss', 512, extra);
  assert.deepEqual(extra, { 'x-trace-id': 'trace-77' }, 'the caller header bag was modified');
  assert.equal(calls[0].headers['x-trace-id'], 'trace-77');
  assert.equal(calls[0].headers.authorization, 'Bearer tok-fake-boot');
  assert.equal(calls[0].headers['x-org-id'], 'org-42');
});

test('extras cannot override credential headers', async () => {
  const source = new FakeSource({ ...BASE });
  const { calls, fn } = recordingTransport(() => ({ status: 200, body: '{}' }));
  const client = new PushClient(source, fn);
  await client.push('mem.rss', 5, { authorization: 'Bearer tok-fake-forged', 'x-org-id': 'org-evil' });
  assert.equal(calls[0].headers.authorization, 'Bearer tok-fake-boot');
  assert.equal(calls[0].headers['x-org-id'], 'org-42');
});

test('the config object handed out by the source is never mutated', async () => {
  const source = new FakeSource({ ...BASE });
  const handed = source.current();
  const { fn } = recordingTransport(() => ({ status: 200, body: '{}' }));
  const client = new PushClient(source, fn);
  await client.push('cpu.sys', 2, { 'x-trace-id': 'trace-1' });
  await client.push('cpu.sys', 3);
  assert.deepEqual(handed, {
    endpoint: 'https://metrics.internal',
    token: 'tok-fake-boot',
    orgId: 'org-42',
  });
});
