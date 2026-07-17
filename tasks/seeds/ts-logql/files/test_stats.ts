import { test } from 'node:test';
import assert from 'node:assert/strict';
import { run } from './engine.ts';

const LOGS = [
  { level: 'error', service: 'api', status: 500, msg: 'DB timeout after 30s', duration: 1200 },
  { level: 'warn', service: 'api', status: 429, msg: 'rate limited', duration: 5 },
  { level: 'error', service: 'billing', status: 502, msg: 'upstream Timeout', duration: 800 },
  { level: 'info', service: 'web', status: 200, msg: 'ok', duration: 15 },
  { level: 'error', service: 'api', status: 500, msg: 'DB timeout after 31s' },
];

test('count over the filtered set', () => {
  assert.deepEqual(run('level = "error" | stats count', LOGS), [{ value: 3 }]);
});

test('count with no matches is zero, not empty', () => {
  assert.deepEqual(run('level = "fatal" | stats count', LOGS), [{ value: 0 }]);
});

test('count by groups and sorts by group', () => {
  assert.deepEqual(run('status >= 200 | stats count by service', LOGS), [
    { group: 'api', value: 3 },
    { group: 'billing', value: 1 },
    { group: 'web', value: 1 },
  ]);
});

test('sum skips records without a numeric value', () => {
  assert.deepEqual(run('level = "error" | stats sum(duration)', LOGS), [{ value: 2000 }]);
});

test('avg divides by the numeric values it actually saw', () => {
  assert.deepEqual(run('status >= 200 | stats avg(duration)', LOGS), [{ value: 505 }]);
  const mixed = [
    { g: 'a', n: 1 },
    { g: 'a', n: 'x' },
    { g: 'a' },
  ];
  assert.deepEqual(run('g = "a" | stats avg(n)', mixed), [{ value: 1 }]);
});

test('min and max', () => {
  assert.deepEqual(run('level = "error" | stats min(duration)', LOGS), [{ value: 800 }]);
  assert.deepEqual(run('level = "error" | stats max(duration)', LOGS), [{ value: 1200 }]);
});

test('sum of nothing is 0; avg, min and max of nothing are null', () => {
  assert.deepEqual(run('level = "warn" | stats sum(nope)', LOGS), [{ value: 0 }]);
  assert.deepEqual(run('level = "warn" | stats avg(nope)', LOGS), [{ value: null }]);
  assert.deepEqual(run('level = "warn" | stats min(nope)', LOGS), [{ value: null }]);
  assert.deepEqual(run('level = "warn" | stats max(nope)', LOGS), [{ value: null }]);
});

test('aggregates group with by, rows sorted by group', () => {
  assert.deepEqual(run('status >= 200 | stats max(duration) by service', LOGS), [
    { group: 'api', value: 1200 },
    { group: 'billing', value: 800 },
    { group: 'web', value: 15 },
  ]);
});

test('records missing the group field are dropped from grouped stats', () => {
  const rows = [
    { service: 'api', duration: 10 },
    { duration: 99 },
    { service: 'api', duration: 20 },
  ];
  assert.deepEqual(run('duration > 0 | stats count by service', rows), [
    { group: 'api', value: 2 },
  ]);
});

test('a group present only through non-numeric values still yields a row', () => {
  const rows = [
    { service: 'api', duration: 10 },
    { service: 'cron', duration: 'slow' },
  ];
  assert.deepEqual(run('duration != 0 | stats avg(duration) by service', rows), [
    { group: 'api', value: 10 },
    { group: 'cron', value: null },
  ]);
});
