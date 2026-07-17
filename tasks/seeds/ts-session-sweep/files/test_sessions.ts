import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SessionStore } from './sessions.ts';

test('sweep drops every expired session', () => {
  const store = new SessionStore();
  for (let u = 0; u < 4; u++) {
    store.issue(`user${u}`, 1_000, 0); // all four expire at t=1000
  }
  const live = store.issue('user4', 60_000, 0);
  const dropped = store.sweep(5_000);
  assert.equal(dropped, 4);
  assert.equal(store.storedCount(), 1);
  assert.notEqual(store.authenticate(live.token, 5_000), undefined);
});

test('sweep keeps live sessions intact', () => {
  const store = new SessionStore();
  const e1 = store.issue('amy', 1_000, 0);
  const l1 = store.issue('bo', 600_000, 0);
  const e2 = store.issue('cat', 1_000, 0);
  const l2 = store.issue('dev', 600_000, 0);
  const e3 = store.issue('eli', 1_000, 0);
  const dropped = store.sweep(50_000);
  assert.equal(dropped, 3);
  assert.equal(store.storedCount(), 2);
  assert.notEqual(store.authenticate(l1.token, 50_000), undefined);
  assert.notEqual(store.authenticate(l2.token, 50_000), undefined);
  assert.equal(store.authenticate(e1.token, 50_000), undefined);
  assert.equal(store.authenticate(e2.token, 50_000), undefined);
  assert.equal(store.authenticate(e3.token, 50_000), undefined);
});

test('sweeping an all-live store drops nothing', () => {
  const store = new SessionStore();
  store.issue('fay', 60_000, 0);
  store.issue('gil', 60_000, 0);
  assert.equal(store.sweep(1_000), 0);
  assert.equal(store.storedCount(), 2);
});

test('revoking a user kills all their sessions immediately', () => {
  const store = new SessionStore();
  const s1 = store.issue('mallory', 60_000, 0);
  const s2 = store.issue('mallory', 60_000, 0);
  const s3 = store.issue('mallory', 60_000, 0);
  const other = store.issue('trent', 60_000, 0);
  const revoked = store.revokeUser('mallory');
  assert.equal(revoked, 3);
  assert.equal(store.authenticate(s1.token, 1_000), undefined, 's1 still authenticates');
  assert.equal(store.authenticate(s2.token, 1_000), undefined, 's2 still authenticates');
  assert.equal(store.authenticate(s3.token, 1_000), undefined, 's3 still authenticates');
  assert.notEqual(store.authenticate(other.token, 1_000), undefined, 'unrelated user was logged out');
  assert.equal(store.storedCount(), 1);
});
