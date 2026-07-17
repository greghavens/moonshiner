import { test } from 'node:test';
import assert from 'node:assert/strict';
import { BusinessCalendar } from './bizhours.ts';

// Week under test: 2026-03-02 is a Monday, 2026-03-08 the following Sunday.
// 2026-03-06 (Friday) is a holiday.
const HOURS = {
  mon: [{ open: '09:00', close: '17:00' }],
  tue: [{ open: '09:00', close: '17:00' }],
  wed: [
    { open: '09:00', close: '12:00' },
    { open: '13:00', close: '17:00' },
  ],
  thu: [{ open: '09:00', close: '17:00' }],
  fri: [{ open: '09:00', close: '17:00' }],
  sat: [{ open: '10:00', close: '14:00' }],
};

const mk = (now?: () => string) =>
  new BusinessCalendar({
    hours: HOURS,
    holidays: ['2026-03-06', '2026-12-25'],
    ...(now ? { now } : {}),
  });

// ---------- configuration validation ----------

test('unknown weekday keys are refused by name', () => {
  assert.throws(
    () => new BusinessCalendar({ hours: { monday: [{ open: '09:00', close: '17:00' }] } }),
    /monday/,
  );
});

test('times must be zero-padded HH:MM within range', () => {
  for (const bad of ['9:00', '09:5', '09:60', '25:00']) {
    assert.throws(
      () => new BusinessCalendar({ hours: { mon: [{ open: bad, close: '23:59' }] } }),
      new RegExp(bad),
      bad,
    );
  }
});

test('an interval must open before it closes', () => {
  assert.throws(
    () => new BusinessCalendar({ hours: { mon: [{ open: '17:00', close: '09:00' }] } }),
  );
  assert.throws(
    () => new BusinessCalendar({ hours: { mon: [{ open: '09:00', close: '09:00' }] } }),
  );
});

test('intervals must be sorted and non-overlapping; touching is fine', () => {
  assert.throws(
    () =>
      new BusinessCalendar({
        hours: {
          mon: [
            { open: '09:00', close: '13:00' },
            { open: '12:00', close: '17:00' },
          ],
        },
      }),
  );
  assert.throws(
    () =>
      new BusinessCalendar({
        hours: {
          mon: [
            { open: '13:00', close: '17:00' },
            { open: '09:00', close: '12:00' },
          ],
        },
      }),
  );
  const cal = new BusinessCalendar({
    hours: {
      mon: [
        { open: '09:00', close: '13:00' },
        { open: '13:00', close: '17:00' },
      ],
    },
  });
  assert.equal(cal.isOpen('2026-03-02T13:00'), true);
});

test('a calendar that is never open is a configuration error', () => {
  assert.throws(() => new BusinessCalendar({ hours: {} }));
  assert.throws(() => new BusinessCalendar({ hours: { mon: [] } }));
});

test('holidays must be real YYYY-MM-DD dates', () => {
  assert.throws(
    () => new BusinessCalendar({ hours: HOURS, holidays: ['2026-2-05'] }),
    /2026-2-05/,
  );
  assert.throws(
    () => new BusinessCalendar({ hours: HOURS, holidays: ['2026-02-30'] }),
    /2026-02-30/,
  );
});

test('malformed timestamps are rejected by every method', () => {
  const cal = mk();
  for (const bad of ['2026-03-02', '2026-03-02T09:5', '2026-02-30T10:00', 'yesterday']) {
    assert.throws(() => cal.isOpen(bad), new RegExp(''), bad);
    assert.throws(() => cal.nextOpen(bad), new RegExp(''), bad);
    assert.throws(() => cal.addBusinessMinutes(bad, 10), new RegExp(''), bad);
  }
});

// ---------- isOpen ----------

test('open is inclusive, close is exclusive', () => {
  const cal = mk();
  assert.equal(cal.isOpen('2026-03-02T08:59'), false);
  assert.equal(cal.isOpen('2026-03-02T09:00'), true);
  assert.equal(cal.isOpen('2026-03-02T16:59'), true);
  assert.equal(cal.isOpen('2026-03-02T17:00'), false);
});

test('split shifts close over lunch', () => {
  const cal = mk();
  assert.equal(cal.isOpen('2026-03-04T11:59'), true);
  assert.equal(cal.isOpen('2026-03-04T12:00'), false);
  assert.equal(cal.isOpen('2026-03-04T12:30'), false);
  assert.equal(cal.isOpen('2026-03-04T13:00'), true);
});

test('weekends follow their own hours, closed days are closed', () => {
  const cal = mk();
  assert.equal(cal.isOpen('2026-03-07T09:30'), false); // saturday opens at 10
  assert.equal(cal.isOpen('2026-03-07T10:00'), true);
  assert.equal(cal.isOpen('2026-03-07T14:00'), false);
  assert.equal(cal.isOpen('2026-03-08T11:00'), false); // sunday: no hours at all
});

test('holidays beat the weekday schedule', () => {
  const cal = mk();
  assert.equal(cal.isOpen('2026-03-06T11:00'), false);
  assert.equal(cal.isOpen('2026-12-25T11:00'), false);
});

// ---------- nextOpen ----------

test('an already-open instant is its own nextOpen', () => {
  const cal = mk();
  assert.equal(cal.nextOpen('2026-03-02T10:15'), '2026-03-02T10:15');
});

test('nextOpen rolls forward over lunch, holidays and closed days', () => {
  const cal = mk();
  assert.equal(cal.nextOpen('2026-03-04T12:15'), '2026-03-04T13:00');
  assert.equal(cal.nextOpen('2026-03-05T17:00'), '2026-03-07T10:00'); // friday is a holiday
  assert.equal(cal.nextOpen('2026-03-07T14:00'), '2026-03-09T09:00'); // over sunday
  assert.equal(cal.nextOpen('2026-03-02T06:00'), '2026-03-02T09:00');
});

test('nextOpen crosses a month boundary', () => {
  const cal = mk();
  assert.equal(cal.nextOpen('2026-02-28T15:00'), '2026-03-02T09:00');
});

// ---------- addBusinessMinutes ----------

test('minutes are consumed within a single shift', () => {
  const cal = mk();
  assert.equal(cal.addBusinessMinutes('2026-03-02T09:00', 60), '2026-03-02T10:00');
});

test('landing exactly on the close boundary stays there', () => {
  const cal = mk();
  assert.equal(cal.addBusinessMinutes('2026-03-02T16:00', 60), '2026-03-02T17:00');
  assert.equal(cal.addBusinessMinutes('2026-03-02T09:00', 480), '2026-03-02T17:00');
});

test('overflow spills into the next open shift', () => {
  const cal = mk();
  assert.equal(cal.addBusinessMinutes('2026-03-02T16:30', 60), '2026-03-03T09:30');
  assert.equal(cal.addBusinessMinutes('2026-03-02T09:00', 481), '2026-03-03T09:01');
});

test('starting while closed starts the clock at the next open', () => {
  const cal = mk();
  assert.equal(cal.addBusinessMinutes('2026-03-02T07:00', 30), '2026-03-02T09:30');
  assert.equal(cal.addBusinessMinutes('2026-03-08T12:00', 30), '2026-03-09T09:30');
});

test('lunch does not count as business time', () => {
  const cal = mk();
  assert.equal(cal.addBusinessMinutes('2026-03-04T11:30', 60), '2026-03-04T13:30');
});

test('holidays are skipped mid-addition', () => {
  const cal = mk();
  // thursday 16:30 + 60: 30 left on thursday, friday is a holiday, so
  // the remaining 30 land on saturday which opens at 10:00.
  assert.equal(cal.addBusinessMinutes('2026-03-05T16:30', 60), '2026-03-07T10:30');
});

test('adding zero minutes means the next open instant', () => {
  const cal = mk();
  assert.equal(cal.addBusinessMinutes('2026-03-02T10:15', 0), '2026-03-02T10:15');
  assert.equal(cal.addBusinessMinutes('2026-03-08T12:00', 0), '2026-03-09T09:00');
});

test('additions cross the year boundary', () => {
  const cal = mk();
  // 2026-12-31 is a thursday; friday 2027-01-01 is not in the holiday list.
  assert.equal(cal.addBusinessMinutes('2026-12-31T16:30', 60), '2027-01-01T09:30');
});

test('minutes must be a non-negative integer', () => {
  const cal = mk();
  for (const bad of [-1, 1.5, NaN, Infinity]) {
    assert.throws(() => cal.addBusinessMinutes('2026-03-02T09:00', bad), new RegExp(''), String(bad));
  }
});

// ---------- businessMinutesBetween ----------

test('minutes between two instants count only open time', () => {
  const cal = mk();
  assert.equal(cal.businessMinutesBetween('2026-03-02T09:00', '2026-03-02T17:00'), 480);
  assert.equal(cal.businessMinutesBetween('2026-03-02T08:00', '2026-03-02T18:00'), 480);
  assert.equal(cal.businessMinutesBetween('2026-03-04T09:00', '2026-03-04T17:00'), 420);
  assert.equal(cal.businessMinutesBetween('2026-03-06T09:00', '2026-03-06T17:00'), 0);
});

test('a full week adds up shift by shift', () => {
  const cal = mk();
  // mon 480 + tue 480 + wed 420 + thu 480 + fri(holiday) 0 + sat 240 + sun 0
  assert.equal(cal.businessMinutesBetween('2026-03-02T09:00', '2026-03-09T09:00'), 2100);
});

test('an empty or inverted range', () => {
  const cal = mk();
  assert.equal(cal.businessMinutesBetween('2026-03-02T10:00', '2026-03-02T10:00'), 0);
  assert.throws(() => cal.businessMinutesBetween('2026-03-03T10:00', '2026-03-02T10:00'));
});

test('businessMinutesBetween inverts addBusinessMinutes', () => {
  const cal = mk();
  for (const n of [0, 1, 29, 480, 500, 2000]) {
    const start = '2026-03-02T09:37';
    const end = cal.addBusinessMinutes(start, n);
    assert.equal(cal.businessMinutesBetween(start, end), n, `n=${n}`);
  }
});

// ---------- deadline and the injected clock ----------

test('deadline uses the injected now', () => {
  const cal = mk(() => '2026-03-02T16:30');
  assert.equal(cal.deadline(60), '2026-03-03T09:30');
});

test('deadline reads the clock on every call', () => {
  let t = '2026-03-02T09:00';
  const cal = mk(() => t);
  assert.equal(cal.deadline(30), '2026-03-02T09:30');
  t = '2026-03-05T16:45';
  assert.equal(cal.deadline(30), '2026-03-07T10:15');
});
