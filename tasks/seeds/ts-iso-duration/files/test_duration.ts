import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  parseDuration,
  formatDuration,
  addDurations,
  subtractDurations,
  normalizeDuration,
  compareDurations,
} from './duration.ts';
import type { Duration } from './duration.ts';

// Expected values are always complete six-field objects.
function dur(partial: Partial<Duration>): Duration {
  return { years: 0, months: 0, days: 0, hours: 0, minutes: 0, seconds: 0, ...partial };
}

// ---------- parsing ----------

test('parses a full designator string', () => {
  assert.deepEqual(
    parseDuration('P1Y2M3DT4H5M6S'),
    dur({ years: 1, months: 2, days: 3, hours: 4, minutes: 5, seconds: 6 }),
  );
});

test('absent components come back as zero, not undefined', () => {
  assert.deepEqual(parseDuration('PT15M'), dur({ minutes: 15 }));
  assert.deepEqual(parseDuration('P3D'), dur({ days: 3 }));
  assert.deepEqual(parseDuration('P2Y'), dur({ years: 2 }));
});

test('M means months before T and minutes after it', () => {
  assert.deepEqual(parseDuration('P2M'), dur({ months: 2 }));
  assert.deepEqual(parseDuration('PT2M'), dur({ minutes: 2 }));
  assert.deepEqual(parseDuration('P1MT1M'), dur({ months: 1, minutes: 1 }));
});

test('a week duration parses as seven-day units', () => {
  assert.deepEqual(parseDuration('P2W'), dur({ days: 14 }));
});

test('weeks refuse to combine with any other component', () => {
  assert.throws(() => parseDuration('P2W3D'), /week/i);
  assert.throws(() => parseDuration('P1Y2W'), /week/i);
});

test('a leading minus negates every component', () => {
  assert.deepEqual(parseDuration('-P1DT12H'), dur({ days: -1, hours: -12 }));
});

test('zero components parse fine', () => {
  assert.deepEqual(parseDuration('PT0S'), dur({}));
});

test('rejects input that does not start with P', () => {
  assert.throws(() => parseDuration('1D'), /P/);
  assert.throws(() => parseDuration(''), Error);
});

test('rejects a P with no components at all', () => {
  assert.throws(() => parseDuration('P'), /component/i);
  assert.throws(() => parseDuration('PT'), /component/i);
});

test('rejects time designators that appear before T', () => {
  assert.throws(() => parseDuration('P1H'), /T/);
  assert.throws(() => parseDuration('P30S'), /T/);
});

test('rejects date designators that appear after T', () => {
  assert.throws(() => parseDuration('PT1D'), /T/);
  assert.throws(() => parseDuration('PT1Y'), /T/);
});

test('rejects components out of order or duplicated', () => {
  assert.throws(() => parseDuration('P1M2Y'), /order/i);
  assert.throws(() => parseDuration('P1D2D'), /order/i);
  assert.throws(() => parseDuration('PT5S1M'), /order/i);
});

test('rejects fractional values with a message that says so', () => {
  assert.throws(() => parseDuration('P1.5D'), /fraction|integer/i);
  assert.throws(() => parseDuration('PT0.5S'), /fraction|integer/i);
});

test('rejects stray characters and names the offender', () => {
  assert.throws(() => parseDuration('PT5X'), /X/);
  assert.throws(() => parseDuration('P1Q2D'), /Q/);
});

// ---------- formatting ----------

test('formats a full duration in canonical order', () => {
  assert.equal(
    formatDuration({ years: 1, months: 2, days: 3, hours: 4, minutes: 5, seconds: 6 }),
    'P1Y2M3DT4H5M6S',
  );
});

test('zero components are omitted and T only appears when needed', () => {
  assert.equal(formatDuration(dur({ days: 3, minutes: 15 })), 'P3DT15M');
  assert.equal(formatDuration(dur({ years: 2 })), 'P2Y');
  assert.equal(formatDuration(dur({ seconds: 45 })), 'PT45S');
});

test('the zero duration formats as PT0S', () => {
  assert.equal(formatDuration(dur({})), 'PT0S');
});

test('uniformly negative durations format with a leading minus', () => {
  assert.equal(formatDuration(dur({ days: -1, hours: -12 })), '-P1DT12H');
  assert.equal(formatDuration(dur({ minutes: -15 })), '-PT15M');
});

test('mixed-sign durations cannot be formatted', () => {
  assert.throws(() => formatDuration(dur({ months: 1, days: -3 })), /mixed/i);
});

test('parse(format(x)) is the identity for normalized durations', () => {
  const cases: Duration[] = [
    dur({ years: 1, months: 11, days: 30 }),
    dur({ hours: 23, minutes: 59, seconds: 59 }),
    dur({ days: -2, hours: -3 }),
    dur({}),
  ];
  for (const d of cases) {
    assert.deepEqual(parseDuration(formatDuration(d)), d);
  }
});

// ---------- normalization ----------

test('seconds carry into minutes and minutes into hours', () => {
  assert.deepEqual(normalizeDuration(dur({ seconds: 90 })), dur({ minutes: 1, seconds: 30 }));
  assert.deepEqual(normalizeDuration(dur({ minutes: 130 })), dur({ hours: 2, minutes: 10 }));
});

test('hours carry into days', () => {
  assert.deepEqual(normalizeDuration(dur({ hours: 26 })), dur({ days: 1, hours: 2 }));
  assert.deepEqual(
    normalizeDuration(dur({ seconds: 86461 })),
    dur({ days: 1, minutes: 1, seconds: 1 }),
  );
});

test('months carry into years but days never carry into months', () => {
  assert.deepEqual(normalizeDuration(dur({ months: 26 })), dur({ years: 2, months: 2 }));
  assert.deepEqual(normalizeDuration(dur({ days: 45 })), dur({ days: 45 }));
});

test('normalization settles opposite signs inside a group', () => {
  assert.deepEqual(normalizeDuration(dur({ hours: 2, minutes: -30 })), dur({ hours: 1, minutes: 30 }));
  assert.deepEqual(normalizeDuration(dur({ years: 1, months: -1 })), dur({ months: 11 }));
  assert.deepEqual(normalizeDuration(dur({ days: -1, seconds: 86400 })), dur({}));
});

test('normalization does not mutate its argument', () => {
  const input = dur({ seconds: 3661 });
  normalizeDuration(input);
  assert.deepEqual(input, dur({ seconds: 3661 }));
});

// ---------- arithmetic ----------

test('addition carries into larger units', () => {
  const sum = addDurations(parseDuration('PT45S'), parseDuration('PT30S'));
  assert.equal(formatDuration(sum), 'PT1M15S');
});

test('addition keeps calendar and clock parts separate', () => {
  const sum = addDurations(parseDuration('P1M20D'), parseDuration('P1M15D'));
  assert.deepEqual(sum, dur({ months: 2, days: 35 }));
});

test('subtraction borrows across units', () => {
  const diff = subtractDurations(parseDuration('P1DT2H'), parseDuration('PT3H'));
  assert.deepEqual(diff, dur({ hours: 23 }));
});

test('subtraction below zero yields a negative duration', () => {
  const diff = subtractDurations(parseDuration('PT30M'), parseDuration('PT45M'));
  assert.equal(formatDuration(diff), '-PT15M');
});

// ---------- comparison ----------

test('clock-only durations compare by total seconds', () => {
  assert.equal(compareDurations(parseDuration('PT90S'), parseDuration('PT1M30S')), 0);
  assert.equal(compareDurations(parseDuration('PT2H'), parseDuration('PT119M')), 1);
  assert.equal(compareDurations(parseDuration('PT1H'), parseDuration('P1D')), -1);
});

test('calendar-only durations compare by total months', () => {
  assert.equal(compareDurations(parseDuration('P1Y'), parseDuration('P12M')), 0);
  assert.equal(compareDurations(parseDuration('P13M'), parseDuration('P1Y')), 1);
});

test('agreement on both axes gives a definite answer', () => {
  assert.equal(compareDurations(parseDuration('P1Y1D'), parseDuration('P1M')), 1);
  assert.equal(compareDurations(parseDuration('P1M'), parseDuration('P1M1D')), -1);
});

test('months against days is explicitly incomparable', () => {
  assert.equal(compareDurations(parseDuration('P1M'), parseDuration('P30D')), null);
  assert.equal(compareDurations(parseDuration('P2M'), parseDuration('P1M30D')), null);
});
