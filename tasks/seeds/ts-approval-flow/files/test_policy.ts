import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Policy } from './policy.ts';

const TIERS = [
  { upTo: 100, approvers: ['manager'] },
  { upTo: 1000, approvers: ['manager', 'director'] },
  { approvers: ['manager', 'director', 'finance'] },
];

test('a policy needs at least one tier', () => {
  assert.throws(() => new Policy([]), /tier/);
});

test('every tier needs at least one approver role', () => {
  assert.throws(() => new Policy([{ approvers: [] }]));
});

test('duplicate roles within a tier are refused by name', () => {
  assert.throws(
    () => new Policy([{ approvers: ['manager', 'manager'] }]),
    /manager/,
  );
});

test('upTo must be a positive finite number', () => {
  for (const bad of [0, -50, NaN, Infinity]) {
    assert.throws(
      () => new Policy([{ upTo: bad, approvers: ['manager'] }, { approvers: ['director'] }]),
      undefined,
      String(bad),
    );
  }
});

test('tier thresholds must strictly increase', () => {
  assert.throws(() =>
    new Policy([
      { upTo: 500, approvers: ['manager'] },
      { upTo: 500, approvers: ['director'] },
      { approvers: ['finance'] },
    ]),
  );
  assert.throws(() =>
    new Policy([
      { upTo: 500, approvers: ['manager'] },
      { upTo: 100, approvers: ['director'] },
      { approvers: ['finance'] },
    ]),
  );
});

test('exactly the last tier is the catch-all', () => {
  // a non-last tier without upTo makes later tiers unreachable
  assert.throws(() =>
    new Policy([{ approvers: ['manager'] }, { upTo: 100, approvers: ['director'] }]),
  );
  // a last tier with upTo leaves big amounts unroutable
  assert.throws(() => new Policy([{ upTo: 100, approvers: ['manager'] }]));
});

test('chainFor picks the first tier whose threshold covers the amount, inclusively', () => {
  const p = new Policy(TIERS);
  assert.deepEqual(p.chainFor(15), ['manager']);
  assert.deepEqual(p.chainFor(100), ['manager']);
  assert.deepEqual(p.chainFor(100.01), ['manager', 'director']);
  assert.deepEqual(p.chainFor(1000), ['manager', 'director']);
  assert.deepEqual(p.chainFor(1000.01), ['manager', 'director', 'finance']);
  assert.deepEqual(p.chainFor(250000), ['manager', 'director', 'finance']);
});

test('chainFor refuses non-positive or non-finite amounts', () => {
  const p = new Policy(TIERS);
  for (const bad of [0, -10, NaN, Infinity]) {
    assert.throws(() => p.chainFor(bad), undefined, String(bad));
  }
});

test('chainFor hands out a copy, not policy internals', () => {
  const p = new Policy(TIERS);
  p.chainFor(50).push('intruder');
  assert.deepEqual(p.chainFor(50), ['manager']);
});

test('roles() lists every role once, in first-appearance order', () => {
  const p = new Policy(TIERS);
  assert.deepEqual(p.roles(), ['manager', 'director', 'finance']);
  p.roles().pop();
  assert.deepEqual(p.roles(), ['manager', 'director', 'finance']);
});
