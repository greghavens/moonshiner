import { test } from 'node:test';
import assert from 'node:assert/strict';
import { breachTimeline, digestIds, minutesLeft, nextDue } from './triage.ts';

const now = 1_750_000_000_000;

function ticket(id: number, slaMinutes: number, minutesAgo: number, subject = 'help') {
  return { id, subject, slaMinutes, openedAt: now - minutesAgo * 60_000 };
}

test('minutesLeft counts down from the SLA budget', () => {
  assert.equal(minutesLeft(ticket(1, 60, 15), now), 45);
  assert.equal(minutesLeft(ticket(2, 30, 40), now), -10);
});

test('breach timeline runs soonest-first', () => {
  const tickets = [ticket(1, 240, 0), ticket(2, 60, 15), ticket(3, 15, 10)];
  assert.deepEqual(breachTimeline(tickets, now), [5, 45, 240]);
});

test('breach timeline orders numerically regardless of digit count', () => {
  const tickets = [ticket(4, 15, 0), ticket(5, 300, 0), ticket(6, 90, 0)];
  assert.deepEqual(breachTimeline(tickets, now), [15, 90, 300]);
});

test('already-breached tickets lead the timeline', () => {
  const tickets = [ticket(7, 30, 60), ticket(8, 120, 30)];
  assert.deepEqual(breachTimeline(tickets, now), [-30, 90]);
});

test('nextDue picks the most urgent ticket', () => {
  const tickets = [ticket(1, 240, 0), ticket(2, 60, 15), ticket(3, 15, 10)];
  assert.equal(nextDue(tickets, now)?.id, 3);
});

test('digest ids come out ascending', () => {
  const tickets = [ticket(101, 60, 0), ticket(12, 60, 0), ticket(7, 60, 0)];
  assert.deepEqual(digestIds(tickets), [7, 12, 101]);
});
