import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  civilDateOf,
  civilDayHours,
  dailyTotals,
  nextCivilDate,
  startOfCivilDay,
} from './dayreport.ts';
import type { Zone } from './dayreport.ts';

// Fixture zones: explicit adapters, no host timezone anywhere.
const UTC: Zone = { offsetMinutes: () => 0 };
const KOLKATA: Zone = { offsetMinutes: () => 330 };

// America/New_York for 2026: EST (-300) until 2026-03-08T07:00Z,
// EDT (-240) until 2026-11-01T06:00Z, then EST again.
const DST_START = Date.parse('2026-03-08T07:00:00Z');
const DST_END = Date.parse('2026-11-01T06:00:00Z');
const NEW_YORK: Zone = {
  offsetMinutes: (utcMs) => (utcMs >= DST_START && utcMs < DST_END ? -240 : -300),
};

test('in UTC the civil day starts at UTC midnight', () => {
  assert.equal(startOfCivilDay('2026-03-08', UTC), Date.parse('2026-03-08T00:00:00Z'));
});

test('a negative-offset zone starts its civil day after UTC midnight', () => {
  assert.equal(startOfCivilDay('2026-03-08', NEW_YORK), Date.parse('2026-03-08T05:00:00Z'));
  assert.equal(startOfCivilDay('2026-01-15', NEW_YORK), Date.parse('2026-01-15T05:00:00Z'));
});

test('a positive-offset zone starts its civil day before UTC midnight', () => {
  assert.equal(startOfCivilDay('2026-03-08', KOLKATA), Date.parse('2026-03-07T18:30:00Z'));
});

test('round trip: the civil date of a day start is that same date', () => {
  const dates = ['2026-01-15', '2026-03-08', '2026-07-04', '2026-11-01'];
  const zones: [string, Zone][] = [
    ['UTC', UTC],
    ['New York', NEW_YORK],
    ['Kolkata', KOLKATA],
  ];
  for (const date of dates) {
    for (const [label, zone] of zones) {
      assert.equal(
        civilDateOf(startOfCivilDay(date, zone), zone),
        date,
        `${date} drifted to another day in ${label}`,
      );
    }
  }
});

test('instants map to the tenant-local civil day, not the UTC day', () => {
  const lateEvening = Date.parse('2026-03-08T02:30:00Z'); // 21:30 the day before in New York
  assert.equal(civilDateOf(lateEvening, NEW_YORK), '2026-03-07');
  assert.equal(civilDateOf(lateEvening, UTC), '2026-03-08');
  const beforeUtcMidnight = Date.parse('2026-03-07T19:30:00Z'); // 01:00 next day in Kolkata
  assert.equal(civilDateOf(beforeUtcMidnight, KOLKATA), '2026-03-08');
});

test('day starts across the DST boundary use the offset in force that day', () => {
  assert.equal(startOfCivilDay('2026-03-09', NEW_YORK), Date.parse('2026-03-09T04:00:00Z'));
  assert.equal(startOfCivilDay('2026-11-01', NEW_YORK), Date.parse('2026-11-01T04:00:00Z'));
  assert.equal(startOfCivilDay('2026-11-02', NEW_YORK), Date.parse('2026-11-02T05:00:00Z'));
});

test('the DST-start day has 23 hours and the DST-end day has 25', () => {
  assert.equal(civilDayHours('2026-03-08', NEW_YORK), 23);
  assert.equal(civilDayHours('2026-11-01', NEW_YORK), 25);
  assert.equal(civilDayHours('2026-06-15', NEW_YORK), 24);
  assert.equal(civilDayHours('2026-03-08', KOLKATA), 24);
});

test('daily totals bucket by tenant-local day', () => {
  const events = [
    { at: Date.parse('2026-03-08T02:30:00Z'), qty: 3 }, // Mar 7, 21:30 in New York
    { at: Date.parse('2026-03-08T04:59:00Z'), qty: 5 }, // Mar 7, 23:59 in New York
    { at: Date.parse('2026-03-08T08:00:00Z'), qty: 2 }, // Mar 8, 03:00 EDT in New York
  ];
  assert.deepEqual(dailyTotals(events, NEW_YORK), {
    '2026-03-07': 8,
    '2026-03-08': 2,
  });
  assert.deepEqual(dailyTotals(events, UTC), { '2026-03-08': 10 });
});

test('timestamps are rejected where a civil date is required', () => {
  assert.throws(() => startOfCivilDay('2026-03-08T12:00:00Z', UTC), TypeError);
  assert.throws(() => startOfCivilDay('2026-3-8', UTC), TypeError);
  assert.throws(() => startOfCivilDay('', NEW_YORK), TypeError);
});

test('civil dates serialize as plain YYYY-MM-DD', () => {
  assert.equal(nextCivilDate('2026-02-28'), '2026-03-01');
  assert.equal(nextCivilDate('2026-12-31'), '2027-01-01');
  assert.match(civilDateOf(Date.parse('2026-03-08T12:00:00Z'), NEW_YORK), /^\d{4}-\d{2}-\d{2}$/);
});
