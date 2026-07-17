// Acceptance for the async/await + typed-error roster client.
// Run: node --test test_roster.ts
//
// These tests exercise the post-refactor surface: class RosterClient with
// async methods that RESOLVE to data and THROW typed errors. Every error
// message below is pinned to what the promise-chain client produced in
// its result objects — dashboards grep logs for these strings.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  RosterClient,
  RosterError,
  NotFoundError,
  AlreadySubscribedError,
  ValidationError,
  RateLimitError,
  NetworkError,
} from './roster.ts';

type Scripted = { status: number; body: any } | Error;

function scripted(...responses: Scripted[]) {
  const calls: Array<{ method: string; path: string; body?: unknown }> = [];
  const transport = (req: { method: string; path: string; body?: unknown }) => {
    calls.push(req);
    const next = responses.shift();
    if (next === undefined) throw new Error('test scripted too few responses');
    if (next instanceof Error) return Promise.reject(next);
    return Promise.resolve(next);
  };
  return { transport, calls };
}

const ana = { id: 's_81', email: 'ana@example.com', name: 'Ana', tags: ['weekly'] };

test('getSubscriber resolves with the payload and sends the same request as before', async () => {
  const { transport, calls } = scripted({ status: 200, body: ana });
  const client = new RosterClient(transport);
  const got = await client.getSubscriber('s_81');
  assert.deepEqual(got, ana);
  assert.deepEqual(calls, [{ method: 'GET', path: '/subscribers/s_81' }]);
});

test('subscribe resolves with the created record and posts the same body', async () => {
  const { transport, calls } = scripted({ status: 201, body: ana });
  const client = new RosterClient(transport);
  const got = await client.subscribe('ana@example.com', 'Ana');
  assert.deepEqual(got, ana);
  assert.deepEqual(calls, [
    {
      method: 'POST',
      path: '/subscribers',
      body: { email: 'ana@example.com', name: 'Ana' },
    },
  ]);
});

test('unsubscribe resolves to null and issues the same DELETE', async () => {
  const { transport, calls } = scripted({ status: 204, body: null });
  const client = new RosterClient(transport);
  assert.equal(await client.unsubscribe('s_81'), null);
  assert.deepEqual(calls, [{ method: 'DELETE', path: '/subscribers/s_81' }]);
});

test('a missing subscriber is a thrown NotFoundError with the legacy message', async () => {
  const { transport } = scripted({ status: 404, body: null });
  const client = new RosterClient(transport);
  await assert.rejects(client.getSubscriber('s_9'), (err: any) => {
    assert.ok(err instanceof NotFoundError, 'expected NotFoundError');
    assert.ok(err instanceof RosterError, 'typed errors extend RosterError');
    assert.ok(err instanceof Error);
    assert.equal(err.name, 'NotFoundError');
    assert.equal(err.code, 'not_found');
    assert.equal(err.message, 'subscriber s_9 not found');
    return true;
  });
});

test('unsubscribe of an unknown id throws NotFoundError too', async () => {
  const { transport } = scripted({ status: 404, body: null });
  const client = new RosterClient(transport);
  await assert.rejects(client.unsubscribe('s_404'), (err: any) => {
    assert.ok(err instanceof NotFoundError);
    assert.equal(err.message, 'subscriber s_404 not found');
    return true;
  });
});

test('duplicate signup throws AlreadySubscribedError carrying the email', async () => {
  const { transport } = scripted({ status: 409, body: null });
  const client = new RosterClient(transport);
  await assert.rejects(client.subscribe('ana@example.com', 'Ana'), (err: any) => {
    assert.ok(err instanceof AlreadySubscribedError);
    assert.equal(err.name, 'AlreadySubscribedError');
    assert.equal(err.code, 'already_subscribed');
    assert.equal(err.message, 'ana@example.com is already subscribed');
    assert.equal(err.email, 'ana@example.com');
    return true;
  });
});

test('validation failures throw ValidationError with the rejected fields', async () => {
  const { transport } = scripted({ status: 422, body: { fields: ['email', 'name'] } });
  const client = new RosterClient(transport);
  await assert.rejects(client.subscribe('', ''), (err: any) => {
    assert.ok(err instanceof ValidationError);
    assert.equal(err.name, 'ValidationError');
    assert.equal(err.code, 'invalid');
    assert.equal(err.message, 'invalid subscriber: email, name');
    assert.deepEqual(err.fields, ['email', 'name']);
    return true;
  });
});

test('rate limiting throws RateLimitError with the wait, message included', async () => {
  const { transport } = scripted({ status: 429, body: { retryAfterSeconds: 30 } });
  const client = new RosterClient(transport);
  await assert.rejects(client.subscribe('ana@example.com', 'Ana'), (err: any) => {
    assert.ok(err instanceof RateLimitError);
    assert.equal(err.name, 'RateLimitError');
    assert.equal(err.code, 'rate_limited');
    assert.equal(err.message, 'rate limited, retry in 30s');
    assert.equal(err.retryAfterSeconds, 30);
    return true;
  });
});

test('transport rejections become NetworkError and keep the original as cause', async () => {
  const boom = new Error('socket hang up');
  const { transport } = scripted(boom);
  const client = new RosterClient(transport);
  await assert.rejects(client.getSubscriber('s_81'), (err: any) => {
    assert.ok(err instanceof NetworkError);
    assert.equal(err.name, 'NetworkError');
    assert.equal(err.code, 'network');
    assert.equal(err.message, 'request failed: socket hang up');
    assert.equal(err.cause, boom);
    return true;
  });
});

test('surprise statuses throw the base RosterError with code api', async () => {
  const { transport } = scripted({ status: 500, body: null });
  const client = new RosterClient(transport);
  await assert.rejects(client.getSubscriber('s_81'), (err: any) => {
    assert.ok(err instanceof RosterError);
    assert.equal(err.name, 'RosterError');
    assert.equal(err.code, 'api');
    assert.equal(err.message, 'unexpected status 500');
    return true;
  });
});

test('errors reject the promise — there is no ok flag to forget to check', async () => {
  const { transport } = scripted({ status: 404, body: null });
  const client = new RosterClient(transport);
  let settled: 'resolved' | 'rejected' = 'resolved';
  let value: any;
  try {
    value = await client.getSubscriber('ghost');
  } catch {
    settled = 'rejected';
  }
  assert.equal(settled, 'rejected', `expected a rejection, resolved with ${JSON.stringify(value)}`);
});
