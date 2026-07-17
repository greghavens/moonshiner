import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SlidingWindowLimiter } from './ratelimit.ts';

// -- deterministic clock -------------------------------------------------------

class FakeClock {
  private t = 0;
  private seq = 0;
  private timers = new Map<number, { at: number; fn: () => void }>();

  now = (): number => this.t;

  setTimeout = (fn: () => void, ms: number): unknown => {
    const id = ++this.seq;
    this.timers.set(id, { at: this.t + ms, fn });
    return id;
  };

  clearTimeout = (id: unknown): void => {
    this.timers.delete(id as number);
  };

  async advance(ms: number): Promise<void> {
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
      await drain();
    }
    this.t = target;
    await drain();
  }
}

async function drain(turns = 10): Promise<void> {
  for (let i = 0; i < turns; i++) await Promise.resolve();
}

async function settled(p: Promise<unknown>): Promise<'pending' | 'resolved' | 'rejected'> {
  let state: 'pending' | 'resolved' | 'rejected' = 'pending';
  p.then(
    () => (state = 'resolved'),
    () => (state = 'rejected'),
  );
  await drain();
  return state;
}

function makeLimiter(limit: number, windowMs: number) {
  const clock = new FakeClock();
  const limiter = new SlidingWindowLimiter({ limit, windowMs, timers: clock });
  return { clock, limiter };
}

// -- immediate grants ----------------------------------------------------------

test('the first `limit` acquisitions resolve immediately', async () => {
  const { limiter } = makeLimiter(3, 1000);
  await limiter.acquire();
  await limiter.acquire();
  await limiter.acquire();
  assert.equal(limiter.pending, 0);
});

test('the acquisition after the limit waits', async () => {
  const { limiter } = makeLimiter(2, 1000);
  await limiter.acquire();
  await limiter.acquire();
  const third = limiter.acquire();
  assert.equal(await settled(third), 'pending');
  assert.equal(limiter.pending, 1);
  third.catch(() => {}); // not settled in this test; keep node quiet
});

// -- window behavior -----------------------------------------------------------

test('a waiter resolves exactly when the oldest slot leaves the window', async () => {
  const { clock, limiter } = makeLimiter(2, 100);
  await limiter.acquire(); // t=0
  await limiter.acquire(); // t=0
  const third = limiter.acquire();
  await clock.advance(99);
  assert.equal(await settled(third), 'pending');
  await clock.advance(1); // t=100: the t=0 slots age out
  assert.equal(await settled(third), 'resolved');
});

test('the window slides — it is not a fixed bucket', async () => {
  const { clock, limiter } = makeLimiter(2, 100);
  await limiter.acquire(); // slot stamped t=0
  await clock.advance(50);
  await limiter.acquire(); // slot stamped t=50
  const c = limiter.acquire();
  const d = limiter.acquire();
  await clock.advance(50); // t=100: only the t=0 slot has aged out
  assert.equal(await settled(c), 'resolved');
  assert.equal(await settled(d), 'pending');
  await clock.advance(50); // t=150: the t=50 slot ages out
  assert.equal(await settled(d), 'resolved');
});

test('waiters are granted in FIFO order', async () => {
  const { clock, limiter } = makeLimiter(1, 100);
  await limiter.acquire(); // t=0
  const order: number[] = [];
  const w1 = limiter.acquire().then(() => order.push(1));
  const w2 = limiter.acquire().then(() => order.push(2));
  const w3 = limiter.acquire().then(() => order.push(3));
  await clock.advance(300);
  await Promise.all([w1, w2, w3]);
  assert.deepEqual(order, [1, 2, 3]);
});

test('several expiring slots release several waiters together', async () => {
  const { clock, limiter } = makeLimiter(2, 100);
  await limiter.acquire(); // t=0
  await limiter.acquire(); // t=0
  const a = limiter.acquire();
  const b = limiter.acquire();
  await clock.advance(100);
  assert.equal(await settled(a), 'resolved');
  assert.equal(await settled(b), 'resolved');
});

test('a queued grant is stamped at grant time, not request time', async () => {
  const { clock, limiter } = makeLimiter(1, 100);
  await limiter.acquire(); // t=0
  const w = limiter.acquire(); // requested t=0, granted t=100
  await clock.advance(100);
  assert.equal(await settled(w), 'resolved');
  await clock.advance(50); // t=150
  const x = limiter.acquire(); // w's slot lives until t=200
  await clock.advance(49); // t=199
  assert.equal(await settled(x), 'pending');
  await clock.advance(1); // t=200
  assert.equal(await settled(x), 'resolved');
});

// -- cancellation ----------------------------------------------------------------

test('an already-aborted signal rejects without queueing', async () => {
  const { limiter } = makeLimiter(1, 100);
  await limiter.acquire();
  const ac = new AbortController();
  const reason = new Error('gave up before asking');
  ac.abort(reason);
  await assert.rejects(limiter.acquire(ac.signal), reason);
  assert.equal(limiter.pending, 0);
});

test('aborting a queued waiter rejects it and hands its turn to the next in line', async () => {
  const { clock, limiter } = makeLimiter(1, 100);
  await limiter.acquire(); // t=0
  const ac = new AbortController();
  const reason = new Error('request timed out upstream');
  const w1 = limiter.acquire(ac.signal);
  const w2 = limiter.acquire();
  const w1err = w1.catch((e: unknown) => e);
  ac.abort(reason);
  assert.equal(await w1err, reason);
  assert.equal(limiter.pending, 1);
  await clock.advance(100);
  assert.equal(await settled(w2), 'resolved');
});

test('an aborted waiter never consumed a slot', async () => {
  const { clock, limiter } = makeLimiter(2, 100);
  await limiter.acquire(); // t=0
  await limiter.acquire(); // t=0
  const ac = new AbortController();
  const doomed = limiter.acquire(ac.signal).catch((e: unknown) => e);
  ac.abort(new Error('nope'));
  await doomed;
  await clock.advance(100); // both t=0 slots expire
  // if the aborted waiter had taken a slot, one of these would queue
  assert.equal(await settled(limiter.acquire()), 'resolved');
  assert.equal(await settled(limiter.acquire()), 'resolved');
});

// -- tryAcquire ------------------------------------------------------------------

test('tryAcquire grants without waiting or returns false', async () => {
  const { clock, limiter } = makeLimiter(2, 100);
  assert.equal(limiter.tryAcquire(), true);
  assert.equal(limiter.tryAcquire(), true);
  assert.equal(limiter.tryAcquire(), false);
  assert.equal(limiter.pending, 0); // never queues
  await clock.advance(100);
  assert.equal(limiter.tryAcquire(), true);
});

test('tryAcquire does not cut in front of queued waiters', async () => {
  const { clock, limiter } = makeLimiter(1, 100);
  await limiter.acquire(); // t=0
  const w = limiter.acquire();
  await clock.advance(100);
  assert.equal(await settled(w), 'resolved'); // w got the freed slot
  assert.equal(limiter.tryAcquire(), false); // w's grant fills the window again
});

// -- validation --------------------------------------------------------------------

test('constructor rejects nonsense limits and windows', () => {
  const timers = new FakeClock();
  assert.throws(() => new SlidingWindowLimiter({ limit: 0, windowMs: 100, timers }), TypeError);
  assert.throws(() => new SlidingWindowLimiter({ limit: 1.5, windowMs: 100, timers }), TypeError);
  assert.throws(() => new SlidingWindowLimiter({ limit: 2, windowMs: 0, timers }), TypeError);
});
