import { test } from 'node:test';
import assert from 'node:assert/strict';
import { expand } from './recur.ts';

test('an event without a rule occurs exactly once, at its start', () => {
  assert.deepEqual(
    expand('2026-03-02T09:00', null, '2026-03-01T00:00', '2026-04-01T00:00'),
    ['2026-03-02T09:00'],
  );
});

test('a one-off outside the window yields nothing', () => {
  assert.deepEqual(
    expand('2026-03-02T09:00', null, '2026-03-03T00:00', '2026-04-01T00:00'),
    [],
  );
});

test('daily with count stops after count occurrences', () => {
  assert.deepEqual(
    expand(
      '2026-03-02T09:00',
      { freq: 'daily', count: 4 },
      '2026-03-01T00:00',
      '2026-04-01T00:00',
    ),
    ['2026-03-02T09:00', '2026-03-03T09:00', '2026-03-04T09:00', '2026-03-05T09:00'],
  );
});

test('daily with interval skips days; until is inclusive', () => {
  assert.deepEqual(
    expand(
      '2026-03-02T08:00',
      { freq: 'daily', interval: 2, until: '2026-03-08T08:00' },
      '2026-03-01T00:00',
      '2026-04-01T00:00',
    ),
    ['2026-03-02T08:00', '2026-03-04T08:00', '2026-03-06T08:00', '2026-03-08T08:00'],
  );
});

test('an until before the first occurrence yields nothing', () => {
  assert.deepEqual(
    expand(
      '2026-03-02T09:00',
      { freq: 'daily', until: '2026-03-01T00:00' },
      '2026-01-01T00:00',
      '2026-12-01T00:00',
    ),
    [],
  );
});

test('occurrences before the window still consume count', () => {
  assert.deepEqual(
    expand(
      '2026-03-02T09:00',
      { freq: 'daily', count: 5 },
      '2026-03-04T00:00',
      '2026-04-01T00:00',
    ),
    ['2026-03-04T09:00', '2026-03-05T09:00', '2026-03-06T09:00'],
  );
});

test('the window is half-open: start inclusive, end exclusive', () => {
  assert.deepEqual(
    expand(
      '2026-03-02T09:00',
      { freq: 'daily' },
      '2026-03-02T09:00',
      '2026-03-04T09:00',
    ),
    ['2026-03-02T09:00', '2026-03-03T09:00'],
  );
});

test('weekly recurrence honors the interval', () => {
  assert.deepEqual(
    expand(
      '2026-03-02T10:00',
      { freq: 'weekly', interval: 2, count: 3 },
      '2026-01-01T00:00',
      '2026-12-01T00:00',
    ),
    ['2026-03-02T10:00', '2026-03-16T10:00', '2026-03-30T10:00'],
  );
});

test('monthly recurrence rolls across a year boundary', () => {
  assert.deepEqual(
    expand(
      '2026-11-15T12:00',
      { freq: 'monthly', count: 4 },
      '2026-01-01T00:00',
      '2027-12-01T00:00',
    ),
    ['2026-11-15T12:00', '2026-12-15T12:00', '2027-01-15T12:00', '2027-02-15T12:00'],
  );
});

test('monthly on the 31st skips short months without consuming count', () => {
  assert.deepEqual(
    expand(
      '2026-01-31T09:00',
      { freq: 'monthly', count: 3 },
      '2026-01-01T00:00',
      '2027-01-01T00:00',
    ),
    ['2026-01-31T09:00', '2026-03-31T09:00', '2026-05-31T09:00'],
  );
});

test('monthly with until still skips short months', () => {
  assert.deepEqual(
    expand(
      '2026-01-31T09:00',
      { freq: 'monthly', until: '2026-04-30T00:00' },
      '2026-01-01T00:00',
      '2027-01-01T00:00',
    ),
    ['2026-01-31T09:00', '2026-03-31T09:00'],
  );
});

test('rules are validated', () => {
  const win: [string, string] = ['2026-01-01T00:00', '2026-02-01T00:00'];
  assert.throws(() => expand('2026-01-05T09:00', { freq: 'daily', interval: 0 }, ...win), /interval/);
  assert.throws(() => expand('2026-01-05T09:00', { freq: 'daily', interval: 1.5 }, ...win), /interval/);
  assert.throws(() => expand('2026-01-05T09:00', { freq: 'daily', count: 0 }, ...win), /count/);
  assert.throws(
    () =>
      expand(
        '2026-01-05T09:00',
        { freq: 'daily', count: 2, until: '2026-01-20T00:00' },
        ...win,
      ),
    /count.*until|until.*count/,
  );
  assert.throws(
    () => expand('2026-01-05T09:00', { freq: 'yearly' } as never, ...win),
    /freq|yearly/,
  );
});

test('malformed timestamps and inverted windows are refused', () => {
  assert.throws(
    () => expand('2026-3-2 9:00', null, '2026-01-01T00:00', '2026-02-01T00:00'),
    /2026-3-2 9:00/,
  );
  assert.throws(
    () => expand('2026-01-05T09:00', null, '2026-02-01T00:00', '2026-01-01T00:00'),
    /window/,
  );
});
