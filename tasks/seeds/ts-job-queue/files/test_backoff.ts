import { test } from 'node:test';
import assert from 'node:assert/strict';
import { backoffDelay } from './backoff.ts';

test('defaults double from one second', () => {
  assert.equal(backoffDelay(1), 1000);
  assert.equal(backoffDelay(2), 2000);
  assert.equal(backoffDelay(4), 8000);
});

test('base and factor are configurable', () => {
  assert.equal(backoffDelay(1, { baseMs: 10, factor: 3 }), 10);
  assert.equal(backoffDelay(3, { baseMs: 10, factor: 3 }), 90);
});

test('maxMs caps the delay', () => {
  assert.equal(backoffDelay(4, { baseMs: 100, factor: 10, maxMs: 2500 }), 2500);
  assert.equal(backoffDelay(1, { baseMs: 100, maxMs: 50 }), 50);
});

test('factor 1 keeps the delay constant', () => {
  assert.equal(backoffDelay(5, { baseMs: 250, factor: 1 }), 250);
});

test('attempt numbers below 1 or fractional are rejected', () => {
  assert.throws(() => backoffDelay(0), RangeError);
  assert.throws(() => backoffDelay(-2), RangeError);
  assert.throws(() => backoffDelay(1.5), RangeError);
});
