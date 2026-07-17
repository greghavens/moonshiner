import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Calendar } from './calendar.ts';

const MARCH: [string, string] = ['2026-03-01T00:00', '2026-04-01T00:00'];

test('a one-off event shows up with a computed end', () => {
  const cal = new Calendar();
  cal.add({ id: 'standup', title: 'Standup', start: '2026-03-02T09:30', durationMinutes: 15 });
  assert.deepEqual(cal.occurrences(...MARCH), [
    { id: 'standup', title: 'Standup', start: '2026-03-02T09:30', end: '2026-03-02T09:45' },
  ]);
});

test('ends can cross midnight', () => {
  const cal = new Calendar();
  cal.add({ id: 'deploy', title: 'Deploy window', start: '2026-03-02T23:30', durationMinutes: 60 });
  assert.equal(cal.occurrences(...MARCH)[0].end, '2026-03-03T00:30');
});

test('recurring events expand into one occurrence per instance', () => {
  const cal = new Calendar();
  cal.add({
    id: 'standup',
    title: 'Standup',
    start: '2026-03-02T09:30',
    durationMinutes: 15,
    rule: { freq: 'daily', count: 3 },
  });
  assert.deepEqual(
    cal.occurrences(...MARCH).map((o) => o.start),
    ['2026-03-02T09:30', '2026-03-03T09:30', '2026-03-04T09:30'],
  );
});

test('occurrences are sorted by start, ties broken by id', () => {
  const cal = new Calendar();
  cal.add({ id: 'b', title: 'B', start: '2026-03-02T09:00', durationMinutes: 30 });
  cal.add({ id: 'a', title: 'A', start: '2026-03-02T09:00', durationMinutes: 30 });
  cal.add({ id: 'c', title: 'C', start: '2026-03-02T08:00', durationMinutes: 30 });
  assert.deepEqual(
    cal.occurrences(...MARCH).map((o) => o.id),
    ['c', 'a', 'b'],
  );
});

test('only occurrences starting inside the window are listed', () => {
  const cal = new Calendar();
  cal.add({ id: 'early', title: 'Early', start: '2026-03-02T08:00', durationMinutes: 120 });
  assert.deepEqual(cal.occurrences('2026-03-02T09:00', '2026-03-02T17:00'), []);
});

test('duplicate ids, unknown removals and bad durations are refused by name', () => {
  const cal = new Calendar();
  cal.add({ id: 'standup', title: 'Standup', start: '2026-03-02T09:30', durationMinutes: 15 });
  assert.throws(
    () => cal.add({ id: 'standup', title: 'Again', start: '2026-03-03T09:30', durationMinutes: 15 }),
    /standup/,
  );
  assert.throws(() => cal.remove('ghost'), /ghost/);
  for (const bad of [0, -5, 2.5]) {
    assert.throws(
      () => cal.add({ id: 'x', title: 'X', start: '2026-03-02T10:00', durationMinutes: bad }),
      /duration/i,
    );
  }
});

test('removed events vanish and their id can be reused', () => {
  const cal = new Calendar();
  cal.add({ id: 'gym', title: 'Gym', start: '2026-03-02T18:00', durationMinutes: 60 });
  cal.remove('gym');
  assert.deepEqual(cal.occurrences(...MARCH), []);
  cal.add({ id: 'gym', title: 'Gym v2', start: '2026-03-03T18:00', durationMinutes: 60 });
  assert.equal(cal.occurrences(...MARCH)[0].title, 'Gym v2');
});

test('overlapping occurrences are reported as a pair', () => {
  const cal = new Calendar();
  cal.add({ id: 'a', title: 'A', start: '2026-03-02T09:00', durationMinutes: 60 });
  cal.add({ id: 'b', title: 'B', start: '2026-03-02T09:30', durationMinutes: 60 });
  assert.deepEqual(cal.conflicts(...MARCH), [
    [
      { id: 'a', title: 'A', start: '2026-03-02T09:00', end: '2026-03-02T10:00' },
      { id: 'b', title: 'B', start: '2026-03-02T09:30', end: '2026-03-02T10:30' },
    ],
  ]);
});

test('back-to-back events do not conflict', () => {
  const cal = new Calendar();
  cal.add({ id: 'a', title: 'A', start: '2026-03-02T09:00', durationMinutes: 60 });
  cal.add({ id: 'b', title: 'B', start: '2026-03-02T10:00', durationMinutes: 60 });
  assert.deepEqual(cal.conflicts(...MARCH), []);
});

test('a recurring event only conflicts on the instances that actually overlap', () => {
  const cal = new Calendar();
  cal.add({
    id: 'standup',
    title: 'Standup',
    start: '2026-03-02T09:30',
    durationMinutes: 15,
    rule: { freq: 'daily', count: 5 },
  });
  cal.add({ id: 'oneoff', title: 'Vendor call', start: '2026-03-04T09:40', durationMinutes: 30 });
  const pairs = cal.conflicts(...MARCH);
  assert.equal(pairs.length, 1);
  assert.deepEqual(
    pairs[0].map((o) => [o.id, o.start]),
    [
      ['standup', '2026-03-04T09:30'],
      ['oneoff', '2026-03-04T09:40'],
    ],
  );
});

test('an event long enough to reach its own next instance conflicts with itself', () => {
  const cal = new Calendar();
  cal.add({
    id: 'render',
    title: 'Render job',
    start: '2026-03-02T00:00',
    durationMinutes: 1500,
    rule: { freq: 'daily', count: 3 },
  });
  const pairs = cal.conflicts(...MARCH);
  assert.deepEqual(
    pairs.map((p) => p.map((o) => o.start)),
    [
      ['2026-03-02T00:00', '2026-03-03T00:00'],
      ['2026-03-03T00:00', '2026-03-04T00:00'],
    ],
  );
});

test('conflict pairs come out ordered: earlier occurrence first, list sorted by that occurrence', () => {
  const cal = new Calendar();
  cal.add({ id: 'late2', title: 'L2', start: '2026-03-03T14:30', durationMinutes: 60 });
  cal.add({ id: 'late1', title: 'L1', start: '2026-03-03T14:00', durationMinutes: 60 });
  cal.add({ id: 'y', title: 'Y', start: '2026-03-02T09:00', durationMinutes: 60 });
  cal.add({ id: 'x', title: 'X', start: '2026-03-02T09:00', durationMinutes: 30 });
  const pairs = cal.conflicts(...MARCH);
  assert.deepEqual(
    pairs.map((p) => p.map((o) => o.id)),
    [
      ['x', 'y'],
      ['late1', 'late2'],
    ],
  );
});
