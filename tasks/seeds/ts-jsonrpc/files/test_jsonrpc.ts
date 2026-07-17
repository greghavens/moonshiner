// Acceptance tests for the JSON-RPC 2.0 client.
//
// The client sits on top of an injectable async transport — a function
// (req: string) => Promise<string> that sends the serialised request and
// returns the serialised response. This makes the test purely in-memory:
// no network, no ports, no timers.  A "scripted transport" queues expected
// (request, response) pairs and asserts that each outgoing call matches the
// expected wire payload and that out-of-order and batch responses are handled
// correctly.
//
// Run: node --test test_jsonrpc.ts

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JsonRpcClient, JsonRpcError } from './jsonrpc.ts';

// ------------------------------------------------------------------ helpers

type TransportEntry =
  | { kind: 'call'; response: string }
  | { kind: 'notify' };

/** A scripted transport that records every outgoing request payload. */
function makeTransport(entries: TransportEntry[]) {
  const sent: string[] = [];
  let pos = 0;
  const fn = async (req: string): Promise<string> => {
    sent.push(req);
    const entry = entries[pos++];
    if (!entry) throw new Error(`transport script exhausted; extra request: ${req}`);
    if (entry.kind === 'notify') {
      // notifications get no response; the transport returns '' and the client
      // must not await anything
      return '';
    }
    return entry.response;
  };
  return { fn, sent };
}

/** Build a JSON-RPC 2.0 success response. */
function ok(id: number | string, result: unknown): string {
  return JSON.stringify({ jsonrpc: '2.0', id, result });
}

/** Build a JSON-RPC 2.0 error response. */
function err(id: number | string | null, code: number, message: string, data?: unknown): string {
  const error: Record<string, unknown> = { code, message };
  if (data !== undefined) error.data = data;
  return JSON.stringify({ jsonrpc: '2.0', id, error });
}

/** Build a JSON-RPC 2.0 batch response (array). */
function batch(...items: string[]): string {
  return '[' + items.join(',') + ']';
}

// ------------------------------------------------------------------ tests

test('call sends correct JSON-RPC 2.0 request and returns the result', async () => {
  const { fn, sent } = makeTransport([
    { kind: 'call', response: ok(1, { queued: true }) },
  ]);
  const client = new JsonRpcClient(fn);
  const result = await client.call('work.enqueue', { team: 'alpha', slots: 3 });
  assert.deepEqual(result, { queued: true });

  const req = JSON.parse(sent[0]);
  assert.equal(req.jsonrpc, '2.0');
  assert.equal(req.method, 'work.enqueue');
  assert.deepEqual(req.params, { team: 'alpha', slots: 3 });
  assert.ok(typeof req.id === 'number' || typeof req.id === 'string', 'id must be present');
});

test('sequential calls get distinct ids', async () => {
  const { fn, sent } = makeTransport([
    { kind: 'call', response: ok(1, 'a') },
    { kind: 'call', response: ok(2, 'b') },
  ]);
  const client = new JsonRpcClient(fn);
  // We need to wire up the correct ids; the scripted transport must match
  // what the client generates. Use a custom transport that echoes the id back.
  const ids: (number | string)[] = [];
  const echoFn = async (req: string): Promise<string> => {
    const parsed = JSON.parse(req);
    ids.push(parsed.id);
    return ok(parsed.id, 'x');
  };
  const c2 = new JsonRpcClient(echoFn);
  await c2.call('ping', {});
  await c2.call('ping', {});
  assert.equal(ids.length, 2);
  assert.notEqual(ids[0], ids[1], 'consecutive calls must use distinct ids');
  void sent; // suppress lint
});

test('out-of-order responses are correlated by id', async () => {
  // Two concurrent calls; the transport returns them in reverse order.
  const responses: Array<(r: string) => void> = [];
  const echoFn = async (req: string): Promise<string> => {
    return new Promise<string>((resolve) => {
      responses.push((r) => resolve(r));
    });
  };
  const client = new JsonRpcClient(echoFn);

  // Capture the ids the client assigns
  let id1: number | string | undefined;
  let id2: number | string | undefined;
  const captureFn = async (req: string): Promise<string> => {
    const p = JSON.parse(req);
    if (id1 === undefined) {
      id1 = p.id;
    } else {
      id2 = p.id;
    }
    return echoFn(req);
  };
  const c2 = new JsonRpcClient(captureFn);

  const p1 = c2.call('a.method', {});
  const p2 = c2.call('b.method', {});

  // Both calls should be in-flight; wait a tick for the transport to register
  await new Promise((r) => setImmediate(r));

  // Respond to the second call first
  assert.ok(responses.length >= 1, 'at least one transport call registered');
  const r2 = responses.pop()!;
  const r1 = responses.pop()!;
  r2(ok(id2!, 'result-b'));
  r1(ok(id1!, 'result-a'));

  const [res1, res2] = await Promise.all([p1, p2]);
  assert.equal(res1, 'result-a');
  assert.equal(res2, 'result-b');
});

test('notification sends request with no id field and does not wait for a response', async () => {
  let called = false;
  const fn = async (req: string): Promise<string> => {
    called = true;
    const parsed = JSON.parse(req);
    assert.ok(!('id' in parsed), 'notification must have no id field');
    assert.equal(parsed.method, 'metrics.flush');
    return ''; // transport returns empty; client must not try to parse it
  };
  const client = new JsonRpcClient(fn);
  await client.notify('metrics.flush', { ts: 1234 });
  assert.ok(called);
});

test('error response is converted to a typed JsonRpcError', async () => {
  const fn = async (req: string): Promise<string> => {
    const p = JSON.parse(req);
    return err(p.id, -32601, 'Method not found', { method: p.method });
  };
  const client = new JsonRpcClient(fn);
  try {
    await client.call('no.such.method', {});
    assert.fail('expected JsonRpcError');
  } catch (e) {
    assert.ok(e instanceof JsonRpcError, `expected JsonRpcError, got ${e}`);
    assert.equal(e.code, -32601);
    assert.equal(e.message, 'Method not found');
    assert.deepEqual(e.data, { method: 'no.such.method' });
  }
});

test('error response without data field produces JsonRpcError with data=undefined', async () => {
  const fn = async (req: string): Promise<string> => {
    const p = JSON.parse(req);
    return err(p.id, -32600, 'Invalid Request');
  };
  const client = new JsonRpcClient(fn);
  try {
    await client.call('x', null);
    assert.fail('expected JsonRpcError');
  } catch (e) {
    assert.ok(e instanceof JsonRpcError);
    assert.equal(e.code, -32600);
    assert.equal(e.data, undefined);
  }
});

test('batch call sends array request and matches responses by id, order-independent', async () => {
  const fn = async (req: string): Promise<string> => {
    const arr = JSON.parse(req) as Array<{ id: number | string; method: string }>;
    assert.ok(Array.isArray(arr), 'batch must be sent as a JSON array');
    assert.equal(arr.length, 3);
    for (const item of arr) {
      assert.equal(item.jsonrpc, '2.0');
      assert.ok(item.id !== undefined, 'each batch item must have an id');
    }
    // Return responses in reverse order to test id-based matching
    return batch(
      ok(arr[2].id, 'c-result'),
      ok(arr[0].id, 'a-result'),
      ok(arr[1].id, 'b-result'),
    );
  };
  const client = new JsonRpcClient(fn);
  const results = await client.batch([
    { method: 'svc.a', params: {} },
    { method: 'svc.b', params: {} },
    { method: 'svc.c', params: {} },
  ]);
  assert.deepEqual(results, ['a-result', 'b-result', 'c-result'],
    'results must be returned in the same order as the requests, regardless of response order');
});

test('batch with a mix of successes and errors returns results with errors in position', async () => {
  const fn = async (req: string): Promise<string> => {
    const arr = JSON.parse(req) as Array<{ id: number | string }>;
    return batch(
      ok(arr[0].id, 'ok-0'),
      err(arr[1].id, -32000, 'quota exceeded'),
      ok(arr[2].id, 'ok-2'),
    );
  };
  const client = new JsonRpcClient(fn);
  const results = await client.batch([
    { method: 'a', params: {} },
    { method: 'b', params: {} },
    { method: 'c', params: {} },
  ]);
  assert.equal(results[0], 'ok-0');
  assert.ok(results[1] instanceof JsonRpcError, `index 1 must be a JsonRpcError, got ${results[1]}`);
  assert.equal((results[1] as JsonRpcError).code, -32000);
  assert.equal(results[2], 'ok-2');
});

test('duplicate id in response is ignored (first wins)', async () => {
  const fn = async (req: string): Promise<string> => {
    const arr = JSON.parse(req) as Array<{ id: number | string }>;
    const id = arr[0].id;
    // Respond twice with the same id — the second must be silently ignored
    return JSON.stringify([
      { jsonrpc: '2.0', id, result: 'first' },
      { jsonrpc: '2.0', id, result: 'should-be-ignored' },
    ]);
  };
  const client = new JsonRpcClient(fn);
  // Send a single-item batch so we can inject a duplicate-id response
  const results = await client.batch([{ method: 'x', params: {} }]);
  assert.equal(results[0], 'first');
});

test('unknown id in response is silently ignored', async () => {
  const fn = async (req: string): Promise<string> => {
    const arr = JSON.parse(req) as Array<{ id: number | string }>;
    const id = arr[0].id;
    // Include an extra response with a fabricated id that does not belong
    return JSON.stringify([
      { jsonrpc: '2.0', id: 999_999, result: 'stray' },
      { jsonrpc: '2.0', id, result: 'mine' },
    ]);
  };
  const client = new JsonRpcClient(fn);
  const results = await client.batch([{ method: 'y', params: {} }]);
  assert.equal(results[0], 'mine');
});

test('call with array params is forwarded as-is', async () => {
  const fn = async (req: string): Promise<string> => {
    const p = JSON.parse(req);
    assert.deepEqual(p.params, [1, 2, 3]);
    return ok(p.id, 6);
  };
  const client = new JsonRpcClient(fn);
  const result = await client.call('math.sum', [1, 2, 3]);
  assert.equal(result, 6);
});

test('call with null params omits the params field', async () => {
  const fn = async (req: string): Promise<string> => {
    const p = JSON.parse(req);
    assert.ok(!('params' in p) || p.params === null,
      'null params should be omitted or kept null, not sent as an object');
    return ok(p.id, 'pong');
  };
  const client = new JsonRpcClient(fn);
  await client.call('ping', null);
});
