import { test } from 'node:test';
import assert from 'node:assert/strict';
import { addMonths, formatIsoDay, parseIsoDay, renewalSchedule } from './billing.ts';

test('a parsed day round-trips through format', () => {
  assert.equal(formatIsoDay(parseIsoDay('2024-03-07')), '2024-03-07');
  assert.equal(formatIsoDay(parseIsoDay('2023-12-25')), '2023-12-25');
  assert.equal(formatIsoDay(parseIsoDay('2024-01-31')), '2024-01-31');
});

test('rejects strings that are not plain calendar days', () => {
  assert.throws(() => parseIsoDay('2024-3-7'), RangeError);
  assert.throws(() => parseIsoDay('2024-03-07T00:00:00Z'), RangeError);
  assert.throws(() => parseIsoDay('next tuesday'), RangeError);
});

test('mid-month renewals keep their day of month', () => {
  assert.equal(formatIsoDay(addMonths(new Date(2024, 0, 15), 1)), '2024-02-15');
  assert.equal(formatIsoDay(addMonths(new Date(2024, 3, 1), 2)), '2024-06-01');
});

test('renewing on the 31st clamps to shorter months', () => {
  assert.equal(formatIsoDay(addMonths(new Date(2024, 0, 31), 1)), '2024-02-29');
  assert.equal(formatIsoDay(addMonths(new Date(2023, 0, 31), 1)), '2023-02-28');
  assert.equal(formatIsoDay(addMonths(new Date(2024, 7, 31), 1)), '2024-09-30');
});

test('schedules stay anchored to the signup day across the year boundary', () => {
  assert.deepEqual(renewalSchedule('2024-10-31', 4), [
    '2024-11-30',
    '2024-12-31',
    '2025-01-31',
    '2025-02-28',
  ]);
});

test('a simple monthly schedule lands on the same day each month', () => {
  assert.deepEqual(renewalSchedule('2024-05-10', 3), [
    '2024-06-10',
    '2024-07-10',
    '2024-08-10',
  ]);
});
