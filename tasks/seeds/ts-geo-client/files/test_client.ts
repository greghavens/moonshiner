import { test } from 'node:test';
import assert from 'node:assert/strict';
import { GeoClient } from './client.ts';

test('a plain client resolves plans from the documented defaults', () => {
  const client = new GeoClient('https://geo.internal/');
  const plan = client.plan('/v1/forward');
  assert.equal(plan.url, 'https://geo.internal/v1/forward');
  assert.equal(plan.timeoutMs, 5_000);
  assert.equal(plan.retries, 2);
  assert.equal(plan.cacheTtlS, 300);
  assert.deepEqual(plan.headers, { accept: 'application/json' });
});

test('constructor overrides stay with the client that asked for them', () => {
  const admin = new GeoClient('https://geo.internal', {
    retries: 5,
    headers: { accept: 'application/json', 'x-api-key': 'admin-key' },
  });
  const anon = new GeoClient('https://geo.internal');

  const adminPlan = admin.plan('v1/reverse');
  assert.equal(adminPlan.retries, 5);
  assert.equal(adminPlan.headers['x-api-key'], 'admin-key');

  const anonPlan = anon.plan('v1/reverse');
  assert.equal(anonPlan.retries, 2, 'anonymous client should keep default retries');
  assert.equal(anonPlan.headers['x-api-key'], undefined,
    'anonymous client must not send an api key');
});

test('per-call options apply to that call only', () => {
  const client = new GeoClient('https://geo.internal');
  const probe = client.plan('healthz', { timeoutMs: 500, retries: 0 });
  assert.equal(probe.timeoutMs, 500);
  assert.equal(probe.retries, 0);

  const lookup = client.plan('v1/forward');
  assert.equal(lookup.timeoutMs, 5_000, 'later lookups should use the client timeout');
  assert.equal(lookup.retries, 2, 'later lookups should use the client retry policy');
});

test('per-call headers do not stick to the client', () => {
  const client = new GeoClient('https://geo.internal');
  const traced = client.plan('v1/forward', { headers: { 'x-trace-id': 'abc123' } });
  assert.equal(traced.headers['x-trace-id'], 'abc123');
  assert.equal(traced.headers.accept, 'application/json');

  const plain = client.plan('v1/forward');
  assert.equal(plain.headers['x-trace-id'], undefined,
    'trace header should not appear on later calls');
});

test('setHeader affects only its own client', () => {
  const billing = new GeoClient('https://geo.internal');
  const search = new GeoClient('https://geo.internal');
  billing.setHeader('x-api-key', 'billing-key');

  assert.equal(billing.plan('v1/forward').headers['x-api-key'], 'billing-key');
  assert.equal(search.plan('v1/forward').headers['x-api-key'], undefined,
    'other clients must not inherit the header');
});

test('a returned plan can be tweaked without corrupting the client', () => {
  const client = new GeoClient('https://geo.internal');
  const plan = client.plan('v1/forward');
  plan.headers['x-experiment'] = 'on';

  const next = client.plan('v1/forward');
  assert.equal(next.headers['x-experiment'], undefined);
});
