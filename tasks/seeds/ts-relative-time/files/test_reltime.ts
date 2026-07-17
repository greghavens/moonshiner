import { test } from 'node:test';
import assert from 'node:assert/strict';
import { formatRelative } from './reltime.ts';

// Fixed reference instant so every assertion is deterministic.
const NOW = Date.UTC(2026, 5, 15, 12, 0, 0); // 2026-06-15T12:00:00Z

const SEC = 1000;
const MIN = 60 * SEC;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

function ago(ms: number): number {
  return NOW - ms;
}
function ahead(ms: number): number {
  return NOW + ms;
}

test('the reference instant itself is "just now"', () => {
  assert.equal(formatRelative(NOW, { now: NOW }), 'just now');
});

test('anything within ten seconds either side is "just now"', () => {
  assert.equal(formatRelative(ago(10 * SEC), { now: NOW }), 'just now');
  assert.equal(formatRelative(ahead(10 * SEC), { now: NOW }), 'just now');
  assert.equal(formatRelative(ago(3 * SEC), { now: NOW }), 'just now');
});

test('eleven seconds leaves the just-now window', () => {
  assert.equal(formatRelative(ago(11 * SEC), { now: NOW }), '11 seconds ago');
  assert.equal(formatRelative(ahead(11 * SEC), { now: NOW }), 'in 11 seconds');
});

test('seconds run up to 59, then minutes take over', () => {
  assert.equal(formatRelative(ago(59 * SEC), { now: NOW }), '59 seconds ago');
  assert.equal(formatRelative(ago(60 * SEC), { now: NOW }), '1 minute ago');
});

test('unit counts truncate toward zero, never round up', () => {
  assert.equal(formatRelative(ago(119 * SEC), { now: NOW }), '1 minute ago');
  assert.equal(formatRelative(ago(90 * MIN), { now: NOW }), '1 hour ago');
  assert.equal(formatRelative(ago(47 * HOUR), { now: NOW }), '1 day ago');
});

test('singular units drop the plural s', () => {
  assert.equal(formatRelative(ago(1 * MIN), { now: NOW }), '1 minute ago');
  assert.equal(formatRelative(ahead(1 * HOUR), { now: NOW }), 'in 1 hour');
  assert.equal(formatRelative(ago(1 * DAY), { now: NOW }), '1 day ago');
});

test('minutes run up to 59, hours up to 23', () => {
  assert.equal(formatRelative(ago(59 * MIN), { now: NOW }), '59 minutes ago');
  assert.equal(formatRelative(ago(60 * MIN), { now: NOW }), '1 hour ago');
  assert.equal(formatRelative(ago(23 * HOUR + 59 * MIN), { now: NOW }), '23 hours ago');
  assert.equal(formatRelative(ago(24 * HOUR), { now: NOW }), '1 day ago');
});

test('days hand over to weeks at seven days', () => {
  assert.equal(formatRelative(ago(6 * DAY + 23 * HOUR), { now: NOW }), '6 days ago');
  assert.equal(formatRelative(ago(7 * DAY), { now: NOW }), '1 week ago');
  assert.equal(formatRelative(ago(13 * DAY), { now: NOW }), '1 week ago');
  assert.equal(formatRelative(ago(14 * DAY), { now: NOW }), '2 weeks ago');
});

test('weeks hand over to months at thirty days', () => {
  assert.equal(formatRelative(ago(29 * DAY), { now: NOW }), '4 weeks ago');
  assert.equal(formatRelative(ago(30 * DAY), { now: NOW }), '1 month ago');
  assert.equal(formatRelative(ago(72 * DAY), { now: NOW }), '2 months ago');
});

test('months hand over to years at 365 days', () => {
  assert.equal(formatRelative(ago(364 * DAY), { now: NOW }), '12 months ago');
  assert.equal(formatRelative(ago(365 * DAY), { now: NOW }), '1 year ago');
  assert.equal(formatRelative(ago(3 * 365 * DAY), { now: NOW }), '3 years ago');
});

test('future phrasing mirrors past phrasing at every magnitude', () => {
  assert.equal(formatRelative(ahead(5 * MIN), { now: NOW }), 'in 5 minutes');
  assert.equal(formatRelative(ahead(2 * DAY), { now: NOW }), 'in 2 days');
  assert.equal(formatRelative(ahead(3 * 7 * DAY), { now: NOW }), 'in 3 weeks');
  assert.equal(formatRelative(ahead(400 * DAY), { now: NOW }), 'in 1 year');
});

test('Date objects are accepted for both target and now', () => {
  assert.equal(
    formatRelative(new Date(ago(2 * HOUR)), { now: new Date(NOW) }),
    '2 hours ago',
  );
});

test('short style abbreviates units', () => {
  assert.equal(formatRelative(ago(3 * HOUR), { now: NOW, style: 'short' }), '3h ago');
  assert.equal(formatRelative(ago(45 * SEC), { now: NOW, style: 'short' }), '45s ago');
  assert.equal(formatRelative(ago(12 * MIN), { now: NOW, style: 'short' }), '12m ago');
  assert.equal(formatRelative(ago(5 * DAY), { now: NOW, style: 'short' }), '5d ago');
  assert.equal(formatRelative(ago(2 * 7 * DAY), { now: NOW, style: 'short' }), '2w ago');
  assert.equal(formatRelative(ago(90 * DAY), { now: NOW, style: 'short' }), '3mo ago');
  assert.equal(formatRelative(ago(800 * DAY), { now: NOW, style: 'short' }), '2y ago');
});

test('short style future and just-now forms', () => {
  assert.equal(formatRelative(ahead(2 * DAY), { now: NOW, style: 'short' }), 'in 2d');
  assert.equal(formatRelative(ahead(30 * SEC), { now: NOW, style: 'short' }), 'in 30s');
  assert.equal(formatRelative(ahead(4 * SEC), { now: NOW, style: 'short' }), 'now');
  assert.equal(formatRelative(ago(9 * SEC), { now: NOW, style: 'short' }), 'now');
});

test('short style never pluralizes the unit letter', () => {
  assert.equal(formatRelative(ago(2 * HOUR), { now: NOW, style: 'short' }), '2h ago');
  assert.equal(formatRelative(ago(1 * HOUR), { now: NOW, style: 'short' }), '1h ago');
});

test('an invalid date is rejected loudly', () => {
  assert.throws(() => formatRelative(new Date(NaN), { now: NOW }), TypeError);
  assert.throws(() => formatRelative(NOW, { now: new Date(NaN) }), TypeError);
  assert.throws(() => formatRelative(Number.NaN, { now: NOW }), TypeError);
});
