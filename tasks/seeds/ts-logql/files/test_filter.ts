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

const pick = (q: string) => (run(q, LOGS) as typeof LOGS).map((r) => LOGS.indexOf(r));

test('string equality', () => {
  assert.deepEqual(pick('level = "error"'), [0, 2, 4]);
});

test('numeric comparisons', () => {
  assert.deepEqual(pick('status >= 500'), [0, 2, 4]);
  assert.deepEqual(pick('duration < 100'), [1, 3]);
});

test('AND binds tighter than OR', () => {
  assert.deepEqual(pick('service = "web" OR level = "error" AND status = 502'), [2, 3]);
});

test('parentheses regroup', () => {
  assert.deepEqual(pick('(service = "web" OR level = "error") AND status = 502'), [2]);
});

test('NOT negates a comparison, including the missing-field false', () => {
  assert.deepEqual(pick('NOT level = "error"'), [1, 3]);
  assert.deepEqual(pick('NOT duration > 0'), [4]);
});

test('!= requires the field to be present', () => {
  assert.deepEqual(pick('level != "error"'), [1, 3]);
  assert.deepEqual(pick('duration != 5'), [0, 2, 3]);
});

test('~ is case-insensitive substring match', () => {
  assert.deepEqual(pick('msg ~ "timeout"'), [0, 2, 4]);
});

test('comparisons never coerce across types', () => {
  assert.deepEqual(pick('status = "500"'), []);
  assert.deepEqual(pick('msg > 5'), []);
});

test('boolean literals compare strictly', () => {
  const deploys = [
    { env: 'prod', ok: true },
    { env: 'dev', ok: false },
  ];
  assert.deepEqual(run('ok = true', deploys), [deploys[0]]);
  assert.deepEqual(run('ok = false', deploys), [deploys[1]]);
});

test('negative and fractional numbers are legal literals', () => {
  const rows = [{ delta: -1 }, { delta: -2 }];
  assert.deepEqual(run('delta >= -1.5', rows), [rows[0]]);
});

test('string escapes work', () => {
  const rows = [{ msg: 'say "hi"' }, { msg: 'say hi' }];
  assert.deepEqual(run('msg = "say \\"hi\\""', rows), [rows[0]]);
});

test('filters return the original records, in input order', () => {
  const out = run('level = "error"', LOGS) as typeof LOGS;
  assert.equal(out[1], LOGS[2]);
});

test('an empty record set stays empty', () => {
  assert.deepEqual(run('level = "error"', []), []);
});
