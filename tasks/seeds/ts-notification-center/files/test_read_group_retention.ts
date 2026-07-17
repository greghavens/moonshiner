import { test } from 'node:test';
import assert from 'node:assert/strict';
import { NotificationCenter } from './notifications.ts';

function makeClock(start = 0) {
  const clock = { t: start, now: () => clock.t };
  return clock;
}

// --- read / unread ---

test('notifications are born unread; markRead flips exactly one', () => {
  const center = new NotificationCenter({ now: () => 0 });
  const a = center.publish({ kind: 'x', title: 'a' });
  center.publish({ kind: 'x', title: 'b' });
  assert.equal(center.unreadCount(), 2);
  assert.equal(a.read, false);
  assert.equal(center.markRead(a.id), true);
  assert.equal(center.unreadCount(), 1);
  assert.equal(center.list().find((n) => n.id === a.id)!.read, true);
});

test('markRead on unknown or already-read ids returns false', () => {
  const center = new NotificationCenter({ now: () => 0 });
  const a = center.publish({ kind: 'x', title: 'a' });
  assert.equal(center.markRead('n999'), false);
  assert.equal(center.markRead(a.id), true);
  assert.equal(center.markRead(a.id), false);
});

test('markAllRead reports how many it flipped', () => {
  const center = new NotificationCenter({ now: () => 0 });
  const a = center.publish({ kind: 'x', title: 'a' });
  center.publish({ kind: 'x', title: 'b' });
  center.markRead(a.id);
  assert.equal(center.markAllRead(), 1);
  assert.equal(center.unreadCount(), 0);
  assert.equal(center.markAllRead(), 0);
});

test('list({ unreadOnly: true }) filters but keeps newest-first order', () => {
  const clock = makeClock();
  const center = new NotificationCenter({ now: clock.now });
  center.publish({ kind: 'x', title: 'old-unread' });
  clock.t = 10;
  const mid = center.publish({ kind: 'x', title: 'mid-read' });
  clock.t = 20;
  center.publish({ kind: 'x', title: 'new-unread' });
  center.markRead(mid.id);
  assert.deepEqual(
    center.list({ unreadOnly: true }).map((n) => n.title),
    ['new-unread', 'old-unread'],
  );
  assert.equal(center.list().length, 3);
});

// --- grouping ---

test('grouped() buckets by kind, newest activity first, items newest first', () => {
  const clock = makeClock();
  const center = new NotificationCenter({ now: clock.now });
  center.publish({ kind: 'deploy', title: 'deploy A' });
  clock.t = 10;
  center.publish({ kind: 'alert', title: 'alert B' });
  clock.t = 20;
  center.publish({ kind: 'deploy', title: 'deploy C' });

  const groups = center.grouped();
  assert.deepEqual(groups.map((g) => g.kind), ['deploy', 'alert']);
  assert.equal(groups[0].count, 2);
  assert.equal(groups[0].latestAt, 20);
  assert.deepEqual(groups[0].items.map((n) => n.title), ['deploy C', 'deploy A']);
  assert.equal(groups[1].count, 1);
  assert.equal(groups[1].latestAt, 10);
});

// --- retention ---

test('publish prunes read notifications older than maxAgeMs, sparing unread when asked', () => {
  const clock = makeClock();
  const center = new NotificationCenter({ now: clock.now });
  center.setRetention({ maxAgeMs: 100, keepUnread: true });
  const oldRead = center.publish({ kind: 'x', title: 'old-read' });
  center.publish({ kind: 'x', title: 'old-unread' });
  center.markRead(oldRead.id);
  clock.t = 150;
  center.publish({ kind: 'x', title: 'fresh' });
  assert.deepEqual(center.list().map((n) => n.title), ['fresh', 'old-unread']);
});

test('without keepUnread, age prunes unread notifications too', () => {
  const clock = makeClock();
  const center = new NotificationCenter({ now: clock.now });
  center.setRetention({ maxAgeMs: 100 });
  center.publish({ kind: 'x', title: 'doomed' });
  clock.t = 150;
  center.publish({ kind: 'x', title: 'fresh' });
  assert.deepEqual(center.list().map((n) => n.title), ['fresh']);
});

test('a notification exactly maxAgeMs old survives; one tick older does not', () => {
  const clock = makeClock();
  const center = new NotificationCenter({ now: clock.now });
  center.setRetention({ maxAgeMs: 100 });
  center.publish({ kind: 'x', title: 'edge' });
  clock.t = 100;
  assert.equal(center.prune(), 0);
  assert.equal(center.count(), 1);
  clock.t = 101;
  assert.equal(center.prune(), 1);
  assert.equal(center.count(), 0);
});

test('maxCount is a hard cap: oldest go first, unread or not', () => {
  const clock = makeClock();
  const center = new NotificationCenter({ now: clock.now });
  center.setRetention({ maxCount: 3, keepUnread: true });
  for (let i = 1; i <= 5; i++) {
    clock.t = i;
    center.publish({ kind: 'x', title: `n${i}` });
  }
  assert.deepEqual(center.list().map((n) => n.title), ['n5', 'n4', 'n3']);
});

test('prune() reports how many it removed', () => {
  const clock = makeClock();
  const center = new NotificationCenter({ now: clock.now });
  center.setRetention({ maxAgeMs: 10 });
  center.publish({ kind: 'x', title: 'a' });
  center.publish({ kind: 'x', title: 'b' });
  clock.t = 50;
  assert.equal(center.prune(), 2);
  assert.equal(center.count(), 0);
  assert.equal(center.prune(), 0);
});

test('with no retention policy set, prune() is a no-op', () => {
  const clock = makeClock();
  const center = new NotificationCenter({ now: clock.now });
  center.publish({ kind: 'x', title: 'a' });
  clock.t = 999999;
  assert.equal(center.prune(), 0);
  assert.equal(center.count(), 1);
});
