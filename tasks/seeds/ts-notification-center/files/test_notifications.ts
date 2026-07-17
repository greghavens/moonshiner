import { test } from 'node:test';
import assert from 'node:assert/strict';
import { NotificationCenter } from './notifications.ts';

function makeClock(start = 0) {
  const clock = { t: start, now: () => clock.t };
  return clock;
}

test('publish assigns sequential ids and clock timestamps', () => {
  const clock = makeClock(100);
  const center = new NotificationCenter({ now: clock.now });
  const first = center.publish({ kind: 'deploy', title: 'Deploy finished' });
  clock.t = 200;
  const second = center.publish({ kind: 'alert', title: 'CPU high', body: 'web-3 at 97%' });
  assert.equal(first.id, 'n1');
  assert.equal(first.createdAt, 100);
  assert.equal(second.id, 'n2');
  assert.equal(second.createdAt, 200);
  assert.equal(second.body, 'web-3 at 97%');
});

test('list returns newest first', () => {
  const clock = makeClock();
  const center = new NotificationCenter({ now: clock.now });
  center.publish({ kind: 'a', title: 'first' });
  clock.t = 10;
  center.publish({ kind: 'a', title: 'second' });
  clock.t = 5; // out-of-order timestamp, still sorted by time
  center.publish({ kind: 'a', title: 'third' });
  assert.deepEqual(center.list().map((n) => n.title), ['second', 'third', 'first']);
});

test('same-timestamp notifications list latest-published first', () => {
  const center = new NotificationCenter({ now: () => 42 });
  center.publish({ kind: 'a', title: 'one' });
  center.publish({ kind: 'a', title: 'two' });
  assert.deepEqual(center.list().map((n) => n.title), ['two', 'one']);
});

test('dismiss removes by id and reports whether it existed', () => {
  const center = new NotificationCenter({ now: () => 0 });
  const n = center.publish({ kind: 'a', title: 'x' });
  assert.equal(center.count(), 1);
  assert.equal(center.dismiss(n.id), true);
  assert.equal(center.count(), 0);
  assert.equal(center.dismiss(n.id), false);
});

test('kind and title are mandatory', () => {
  const center = new NotificationCenter({ now: () => 0 });
  assert.throws(() => center.publish({ kind: '', title: 'x' }));
  assert.throws(() => center.publish({ kind: 'a', title: '' }));
});
