import { test } from 'node:test';
import assert from 'node:assert/strict';
import { VirtualScheduler } from './scheduler.ts';

test('time starts at zero and only moves when advanced', () => {
  const s = new VirtualScheduler();
  assert.equal(s.now(), 0);
  s.advance(250);
  assert.equal(s.now(), 250);
  s.advance(0);
  assert.equal(s.now(), 250);
});

test('a one-shot fires only once its delay is fully crossed', () => {
  const s = new VirtualScheduler();
  const fired: number[] = [];
  s.schedule(() => fired.push(s.now()), 100);
  s.advance(99);
  assert.deepEqual(fired, []);
  s.advance(1);
  assert.deepEqual(fired, [100]);
  s.advance(500);
  assert.deepEqual(fired, [100], 'one-shot must not fire again');
});

test('partial advances accumulate toward a delay', () => {
  const s = new VirtualScheduler();
  let ran = 0;
  s.schedule(() => { ran += 1; }, 30);
  s.advance(10);
  s.advance(10);
  assert.equal(ran, 0);
  s.advance(10);
  assert.equal(ran, 1);
});

test('due tasks run in fire-time order regardless of scheduling order', () => {
  const s = new VirtualScheduler();
  const order: string[] = [];
  s.schedule(() => order.push('late'), 50);
  s.schedule(() => order.push('early'), 10);
  s.schedule(() => order.push('middle'), 25);
  s.advance(100);
  assert.deepEqual(order, ['early', 'middle', 'late']);
});

test('ties at the same timestamp run in scheduling order', () => {
  const s = new VirtualScheduler();
  const order: string[] = [];
  s.schedule(() => order.push('first'), 20);
  s.schedule(() => order.push('second'), 20);
  s.schedule(() => order.push('third'), 20);
  s.advance(20);
  assert.deepEqual(order, ['first', 'second', 'third']);
});

test('inside a callback, now() is the scheduled fire time, not the advance target', () => {
  const s = new VirtualScheduler();
  const seen: number[] = [];
  s.schedule(() => seen.push(s.now()), 10);
  s.schedule(() => seen.push(s.now()), 70);
  s.advance(1000);
  assert.deepEqual(seen, [10, 70]);
  assert.equal(s.now(), 1000, 'after the advance, now() is the target');
});

test('zero-delay tasks fire on the next advance, even advance(0)', () => {
  const s = new VirtualScheduler();
  let ran = 0;
  s.schedule(() => { ran += 1; }, 0);
  s.advance(0);
  assert.equal(ran, 1);
});

test('negative delay and non-positive interval are RangeErrors', () => {
  const s = new VirtualScheduler();
  assert.throws(() => s.schedule(() => {}, -1), RangeError);
  assert.throws(() => s.scheduleRepeating(() => {}, 0), RangeError);
  assert.throws(() => s.scheduleRepeating(() => {}, -5), RangeError);
});

test('cancel stops a pending one-shot and reports what it did', () => {
  const s = new VirtualScheduler();
  let ran = 0;
  const id = s.schedule(() => { ran += 1; }, 10);
  assert.equal(s.cancel(id), true);
  assert.equal(s.cancel(id), false, 'second cancel has nothing to do');
  s.advance(100);
  assert.equal(ran, 0);
});

test('a callback can cancel a task queued at the same timestamp', () => {
  const s = new VirtualScheduler();
  const order: string[] = [];
  let victim = -1;
  s.schedule(() => {
    order.push('assassin');
    s.cancel(victim);
  }, 10);
  victim = s.schedule(() => order.push('victim'), 10);
  s.advance(10);
  assert.deepEqual(order, ['assassin']);
});

test('a repeater fires at every exact multiple, catching up within one advance', () => {
  const s = new VirtualScheduler();
  const fires: number[] = [];
  s.scheduleRepeating(() => fires.push(s.now()), 10);
  s.advance(35);
  assert.deepEqual(fires, [10, 20, 30]);
});

test('repeats stay drift-free across ragged advances', () => {
  const s = new VirtualScheduler();
  const fires: number[] = [];
  s.scheduleRepeating(() => fires.push(s.now()), 10);
  s.advance(25); // fires 10, 20
  s.advance(4);  // t=29, nothing
  s.advance(1);  // t=30 fires
  s.advance(14); // t=44: fires 40
  assert.deepEqual(fires, [10, 20, 30, 40]);
});

test('cancelling a repeater stops all future fires', () => {
  const s = new VirtualScheduler();
  let count = 0;
  const id = s.scheduleRepeating(() => { count += 1; }, 10);
  s.advance(20);
  assert.equal(count, 2);
  assert.equal(s.cancel(id), true);
  s.advance(100);
  assert.equal(count, 2);
});

test('a repeater can cancel itself from inside its callback', () => {
  const s = new VirtualScheduler();
  const fires: number[] = [];
  const id = s.scheduleRepeating(() => {
    fires.push(s.now());
    if (fires.length === 3) s.cancel(id);
  }, 5);
  s.advance(100);
  assert.deepEqual(fires, [5, 10, 15]);
});

test('tasks scheduled by a callback run within the same advance when due', () => {
  const s = new VirtualScheduler();
  const order: string[] = [];
  s.schedule(() => {
    order.push(`outer@${s.now()}`);
    s.schedule(() => order.push(`inner@${s.now()}`), 5);
  }, 10);
  s.advance(20);
  assert.deepEqual(order, ['outer@10', 'inner@15']);
});

test('tasks scheduled by a callback past the window wait for the next advance', () => {
  const s = new VirtualScheduler();
  const order: string[] = [];
  s.schedule(() => {
    order.push('outer');
    s.schedule(() => order.push('inner'), 50);
  }, 10);
  s.advance(20);
  assert.deepEqual(order, ['outer']);
  s.advance(40); // inner due at t=60
  assert.deepEqual(order, ['outer', 'inner']);
});

test('advanceTo moves to an absolute time', () => {
  const s = new VirtualScheduler();
  const fires: number[] = [];
  s.scheduleRepeating(() => fires.push(s.now()), 100);
  s.advanceTo(250);
  assert.deepEqual(fires, [100, 200]);
  assert.equal(s.now(), 250);
});

test('a throwing callback surfaces but leaves the scheduler usable', () => {
  const s = new VirtualScheduler();
  const order: string[] = [];
  s.schedule(() => { throw new Error('boom'); }, 10);
  s.schedule(() => order.push('survivor'), 15);
  assert.throws(() => s.advance(20), /boom/);
  assert.equal(s.now(), 10, 'time froze at the failed fire');
  s.advance(20);
  assert.deepEqual(order, ['survivor']);
  assert.equal(s.now(), 30);
});

test('pending counts live tasks, with a repeater counting once', () => {
  const s = new VirtualScheduler();
  assert.equal(s.pending(), 0);
  const a = s.schedule(() => {}, 10);
  s.scheduleRepeating(() => {}, 10);
  assert.equal(s.pending(), 2);
  s.cancel(a);
  assert.equal(s.pending(), 1);
  s.advance(100);
  assert.equal(s.pending(), 1, 'the repeater is still live');
});
