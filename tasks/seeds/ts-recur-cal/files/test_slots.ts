import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Calendar } from './calendar.ts';
import { freeSlots } from './slots.ts';

const day = (from: string, to: string, durationMinutes: number) =>
  ({ from, to, durationMinutes });

test('an empty calendar is one big slot', () => {
  const cal = new Calendar();
  assert.deepEqual(freeSlots(cal, day('2026-03-02T09:00', '2026-03-02T17:00', 60)), [
    { start: '2026-03-02T09:00', end: '2026-03-02T17:00' },
  ]);
});

test('a window shorter than the requested duration has no slots', () => {
  const cal = new Calendar();
  assert.deepEqual(freeSlots(cal, day('2026-03-02T09:00', '2026-03-02T09:45', 60)), []);
});

test('a meeting splits the window; a gap exactly the duration counts', () => {
  const cal = new Calendar();
  cal.add({ id: 'm', title: 'M', start: '2026-03-02T10:00', durationMinutes: 60 });
  assert.deepEqual(freeSlots(cal, day('2026-03-02T09:00', '2026-03-02T17:00', 60)), [
    { start: '2026-03-02T09:00', end: '2026-03-02T10:00' },
    { start: '2026-03-02T11:00', end: '2026-03-02T17:00' },
  ]);
});

test('gaps shorter than the duration are dropped', () => {
  const cal = new Calendar();
  cal.add({ id: 'a', title: 'A', start: '2026-03-02T09:30', durationMinutes: 30 });
  cal.add({ id: 'b', title: 'B', start: '2026-03-02T10:45', durationMinutes: 15 });
  assert.deepEqual(freeSlots(cal, day('2026-03-02T09:00', '2026-03-02T12:00', 45)), [
    { start: '2026-03-02T10:00', end: '2026-03-02T10:45' },
    { start: '2026-03-02T11:00', end: '2026-03-02T12:00' },
  ]);
});

test('overlapping busy time is merged before gaps are computed', () => {
  const cal = new Calendar();
  cal.add({ id: 'a', title: 'A', start: '2026-03-02T10:00', durationMinutes: 60 });
  cal.add({ id: 'b', title: 'B', start: '2026-03-02T10:30', durationMinutes: 60 });
  assert.deepEqual(freeSlots(cal, day('2026-03-02T09:00', '2026-03-02T13:00', 60)), [
    { start: '2026-03-02T09:00', end: '2026-03-02T10:00' },
    { start: '2026-03-02T11:30', end: '2026-03-02T13:00' },
  ]);
});

test('back-to-back busy blocks leave no phantom gap between them', () => {
  const cal = new Calendar();
  cal.add({ id: 'a', title: 'A', start: '2026-03-02T10:00', durationMinutes: 60 });
  cal.add({ id: 'b', title: 'B', start: '2026-03-02T11:00', durationMinutes: 60 });
  assert.deepEqual(freeSlots(cal, day('2026-03-02T09:00', '2026-03-02T13:00', 30)), [
    { start: '2026-03-02T09:00', end: '2026-03-02T10:00' },
    { start: '2026-03-02T12:00', end: '2026-03-02T13:00' },
  ]);
});

test('busy time running past the window is clipped, not ignored', () => {
  const cal = new Calendar();
  cal.add({ id: 'late', title: 'Late', start: '2026-03-02T16:00', durationMinutes: 120 });
  assert.deepEqual(freeSlots(cal, day('2026-03-02T09:00', '2026-03-02T17:00', 60)), [
    { start: '2026-03-02T09:00', end: '2026-03-02T16:00' },
  ]);
});

test('a fully booked window has no slots', () => {
  const cal = new Calendar();
  cal.add({ id: 'all', title: 'All day', start: '2026-03-02T09:00', durationMinutes: 480 });
  assert.deepEqual(freeSlots(cal, day('2026-03-02T09:00', '2026-03-02T17:00', 30)), []);
});

test('recurring events carve the window across days, slots may span midnight', () => {
  const cal = new Calendar();
  cal.add({
    id: 'standup',
    title: 'Standup',
    start: '2026-03-02T09:00',
    durationMinutes: 30,
    rule: { freq: 'daily', count: 30 },
  });
  assert.deepEqual(freeSlots(cal, day('2026-03-02T08:00', '2026-03-03T10:00', 120)), [
    { start: '2026-03-02T09:30', end: '2026-03-03T09:00' },
  ]);
});

test('slot search validates its inputs', () => {
  const cal = new Calendar();
  assert.throws(
    () => freeSlots(cal, day('2026-03-02T09:00', '2026-03-02T17:00', 0)),
    /duration/i,
  );
  assert.throws(
    () => freeSlots(cal, day('2026-03-02T17:00', '2026-03-02T09:00', 30)),
    /window/,
  );
});
