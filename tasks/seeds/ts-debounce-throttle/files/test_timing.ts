import { test } from 'node:test';
import assert from 'node:assert/strict';
import { debounce, throttle } from './timing.ts';

// A deterministic manual clock implementing the injectable timer interface.
class FakeClock {
  private t = 0;
  private seq = 0;
  private timers = new Map<number, { at: number; fn: () => void }>();

  setTimeout = (fn: () => void, ms: number): unknown => {
    const id = ++this.seq;
    this.timers.set(id, { at: this.t + ms, fn });
    return id;
  };

  clearTimeout = (id: unknown): void => {
    this.timers.delete(id as number);
  };

  advance(ms: number): void {
    const target = this.t + ms;
    for (;;) {
      let dueId = -1;
      let dueAt = Infinity;
      for (const [id, timer] of this.timers) {
        if (timer.at <= target && (timer.at < dueAt || (timer.at === dueAt && id < dueId))) {
          dueId = id;
          dueAt = timer.at;
        }
      }
      if (dueId === -1) break;
      const timer = this.timers.get(dueId)!;
      this.timers.delete(dueId);
      this.t = Math.max(this.t, timer.at);
      timer.fn();
    }
    this.t = target;
  }
}

function recorder() {
  const calls: unknown[][] = [];
  const fn = (...args: unknown[]) => {
    calls.push(args);
  };
  return { calls, fn };
}

// -- debounce ----------------------------------------------------------------

test('debounce: fires once on the trailing edge with the last arguments', () => {
  const clock = new FakeClock();
  const { calls, fn } = recorder();
  const d = debounce(fn, 50, { timers: clock });
  d('a');
  d('b');
  clock.advance(49);
  assert.deepEqual(calls, []);
  clock.advance(1);
  assert.deepEqual(calls, [['b']]);
});

test('debounce: every call restarts the quiet period', () => {
  const clock = new FakeClock();
  const { calls, fn } = recorder();
  const d = debounce(fn, 50, { timers: clock });
  d(1);
  clock.advance(30);
  d(2);
  clock.advance(30); // 60ms since the first call, only 30 since the last
  assert.deepEqual(calls, []);
  clock.advance(20);
  assert.deepEqual(calls, [[2]]);
});

test('debounce: cancel drops the pending invocation', () => {
  const clock = new FakeClock();
  const { calls, fn } = recorder();
  const d = debounce(fn, 50, { timers: clock });
  d('x');
  d.cancel();
  clock.advance(200);
  assert.deepEqual(calls, []);
});

test('debounce: flush invokes the pending call immediately, exactly once', () => {
  const clock = new FakeClock();
  const { calls, fn } = recorder();
  const d = debounce(fn, 50, { timers: clock });
  d('now');
  d.flush();
  assert.deepEqual(calls, [['now']]);
  clock.advance(200);
  assert.deepEqual(calls, [['now']]);
});

test('debounce: flush with nothing pending is a no-op', () => {
  const clock = new FakeClock();
  const { calls, fn } = recorder();
  const d = debounce(fn, 50, { timers: clock });
  d.flush();
  assert.deepEqual(calls, []);
});

test('debounce: pending() reflects whether a call is waiting', () => {
  const clock = new FakeClock();
  const { fn } = recorder();
  const d = debounce(fn, 50, { timers: clock });
  assert.equal(d.pending(), false);
  d('x');
  assert.equal(d.pending(), true);
  clock.advance(50);
  assert.equal(d.pending(), false);
});

test('debounce leading: first call of a burst fires immediately', () => {
  const clock = new FakeClock();
  const { calls, fn } = recorder();
  const d = debounce(fn, 50, { leading: true, timers: clock });
  d('first');
  assert.deepEqual(calls, [['first']]);
});

test('debounce leading+trailing: a lone call fires only the leading edge', () => {
  const clock = new FakeClock();
  const { calls, fn } = recorder();
  const d = debounce(fn, 50, { leading: true, trailing: true, timers: clock });
  d('only');
  clock.advance(200);
  assert.deepEqual(calls, [['only']]);
});

test('debounce leading+trailing: a burst fires leading then one trailing with latest args', () => {
  const clock = new FakeClock();
  const { calls, fn } = recorder();
  const d = debounce(fn, 50, { leading: true, trailing: true, timers: clock });
  d(1);
  clock.advance(10);
  d(2);
  clock.advance(10);
  d(3);
  clock.advance(50);
  assert.deepEqual(calls, [[1], [3]]);
});

test('debounce leading only: burst collapses to the first call, then re-arms after quiet', () => {
  const clock = new FakeClock();
  const { calls, fn } = recorder();
  const d = debounce(fn, 50, { leading: true, trailing: false, timers: clock });
  d('a');
  d('b');
  d('c');
  clock.advance(50);
  assert.deepEqual(calls, [['a']]);
  d('d');
  assert.deepEqual(calls, [['a'], ['d']]);
});

test('debounce: after firing, the next call starts a fresh cycle', () => {
  const clock = new FakeClock();
  const { calls, fn } = recorder();
  const d = debounce(fn, 50, { timers: clock });
  d('one');
  clock.advance(50);
  d('two');
  clock.advance(50);
  assert.deepEqual(calls, [['one'], ['two']]);
});

test('debounce: forwards all arguments', () => {
  const clock = new FakeClock();
  const { calls, fn } = recorder();
  const d = debounce(fn, 10, { timers: clock });
  d('a', 2, true);
  clock.advance(10);
  assert.deepEqual(calls, [['a', 2, true]]);
});

// -- throttle ----------------------------------------------------------------

test('throttle: leading call fires immediately by default', () => {
  const clock = new FakeClock();
  const { calls, fn } = recorder();
  const t = throttle(fn, 100, { timers: clock });
  t('go');
  assert.deepEqual(calls, [['go']]);
});

test('throttle: calls inside the window coalesce into one trailing call with latest args', () => {
  const clock = new FakeClock();
  const { calls, fn } = recorder();
  const t = throttle(fn, 100, { timers: clock });
  t(1);
  clock.advance(10);
  t(2);
  clock.advance(10);
  t(3);
  clock.advance(79); // t=99
  assert.deepEqual(calls, [[1]]);
  clock.advance(1); // t=100
  assert.deepEqual(calls, [[1], [3]]);
});

test('throttle: a trailing invocation opens a new window', () => {
  const clock = new FakeClock();
  const { calls, fn } = recorder();
  const t = throttle(fn, 100, { timers: clock });
  t('a'); // fires at t=0
  clock.advance(10);
  t('b'); // trailing at t=100
  clock.advance(90);
  assert.deepEqual(calls, [['a'], ['b']]);
  clock.advance(50); // t=150, inside window opened at 100
  t('c');
  assert.deepEqual(calls, [['a'], ['b']]); // not yet
  clock.advance(50); // t=200
  assert.deepEqual(calls, [['a'], ['b'], ['c']]);
});

test('throttle: an idle window expires and the next call is leading again', () => {
  const clock = new FakeClock();
  const { calls, fn } = recorder();
  const t = throttle(fn, 100, { timers: clock });
  t('a');
  clock.advance(100); // no calls during the window
  t('b');
  assert.deepEqual(calls, [['a'], ['b']]);
});

test('throttle trailing:false: calls inside the window are dropped', () => {
  const clock = new FakeClock();
  const { calls, fn } = recorder();
  const t = throttle(fn, 100, { trailing: false, timers: clock });
  t(1);
  t(2);
  t(3);
  clock.advance(300);
  assert.deepEqual(calls, [[1]]);
});

test('throttle leading:false: first call waits for the window end', () => {
  const clock = new FakeClock();
  const { calls, fn } = recorder();
  const t = throttle(fn, 100, { leading: false, timers: clock });
  t('early');
  clock.advance(50);
  t('late');
  assert.deepEqual(calls, []);
  clock.advance(50);
  assert.deepEqual(calls, [['late']]);
});

test('throttle: cancel drops the pending trailing call and resets the window', () => {
  const clock = new FakeClock();
  const { calls, fn } = recorder();
  const t = throttle(fn, 100, { timers: clock });
  t('a');
  t('b');
  t.cancel();
  clock.advance(300);
  assert.deepEqual(calls, [['a']]);
  t('c'); // window was reset, so this is leading
  assert.deepEqual(calls, [['a'], ['c']]);
});

test('throttle: flush fires the pending trailing call right away, exactly once', () => {
  const clock = new FakeClock();
  const { calls, fn } = recorder();
  const t = throttle(fn, 100, { timers: clock });
  t('a');
  t('b');
  t.flush();
  assert.deepEqual(calls, [['a'], ['b']]);
  clock.advance(300);
  assert.deepEqual(calls, [['a'], ['b']]);
});

test('throttle: a rapid burst produces exactly leading + trailing', () => {
  const clock = new FakeClock();
  const { calls, fn } = recorder();
  const t = throttle(fn, 100, { timers: clock });
  for (let i = 0; i < 10; i++) t(i);
  clock.advance(100);
  assert.deepEqual(calls, [[0], [9]]);
});
