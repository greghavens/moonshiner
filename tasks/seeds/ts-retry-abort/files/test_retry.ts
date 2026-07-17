import { test } from 'node:test';
import assert from 'node:assert/strict';
import { retry } from './retry.ts';

// -- helpers -----------------------------------------------------------------

async function tick(turns = 20): Promise<void> {
  for (let i = 0; i < turns; i++) await Promise.resolve();
}

/** Records every delay retry asks for and resolves it immediately. */
function instantDelays() {
  const requested: number[] = [];
  const delay = (ms: number, _signal?: AbortSignal): Promise<void> => {
    requested.push(ms);
    return Promise.resolve();
  };
  return { requested, delay };
}

interface DelayEntry {
  ms: number;
  aborted: boolean;
  resolve: () => void;
  reject: (e: unknown) => void;
}

/** Records delays but leaves them pending until the test settles them. */
function manualDelays() {
  const entries: DelayEntry[] = [];
  const delay = (ms: number, signal?: AbortSignal): Promise<void> =>
    new Promise<void>((resolve, reject) => {
      const entry: DelayEntry = { ms, aborted: false, resolve, reject };
      entries.push(entry);
      signal?.addEventListener(
        'abort',
        () => {
          entry.aborted = true;
          reject(signal.reason);
        },
        { once: true },
      );
    });
  return { entries, delay };
}

// -- basic retry behavior ----------------------------------------------------

test('a first-attempt success calls fn once and never waits', async () => {
  const { requested, delay } = instantDelays();
  let calls = 0;
  const result = await retry(
    async () => {
      calls++;
      return 'fresh';
    },
    { attempts: 5, baseDelayMs: 100, delay },
  );
  assert.equal(result, 'fresh');
  assert.equal(calls, 1);
  assert.deepEqual(requested, []);
});

test('waits base*factor^(n-1) between attempts', async () => {
  const { requested, delay } = instantDelays();
  let calls = 0;
  const result = await retry(
    async () => {
      calls++;
      if (calls < 4) throw new Error(`attempt ${calls} failed`);
      return 'finally';
    },
    { attempts: 5, baseDelayMs: 100, factor: 2, delay },
  );
  assert.equal(result, 'finally');
  assert.deepEqual(requested, [100, 200, 400]);
});

test('maxDelayMs caps the backoff', async () => {
  const { requested, delay } = instantDelays();
  let calls = 0;
  await retry(
    async () => {
      calls++;
      if (calls < 4) throw new Error('nope');
      return 'ok';
    },
    { attempts: 5, baseDelayMs: 100, factor: 10, maxDelayMs: 250, delay },
  );
  assert.deepEqual(requested, [100, 250, 250]);
});

test('fn receives 1-based attempt numbers', async () => {
  const { delay } = instantDelays();
  const seen: number[] = [];
  await retry(
    async ({ attempt }) => {
      seen.push(attempt);
      if (attempt < 3) throw new Error('again');
      return 'done';
    },
    { attempts: 3, baseDelayMs: 1, delay },
  );
  assert.deepEqual(seen, [1, 2, 3]);
});

test('exhausting attempts rejects with the last error', async () => {
  const { delay } = instantDelays();
  let calls = 0;
  await assert.rejects(
    retry(
      async () => {
        calls++;
        throw new Error(`failure number ${calls}`);
      },
      { attempts: 3, baseDelayMs: 1, delay },
    ),
    /failure number 3/,
  );
  assert.equal(calls, 3);
});

test('non-Error rejection values are passed through untouched', async () => {
  const { delay } = instantDelays();
  const err = await retry(async () => Promise.reject('just a string'), {
    attempts: 1,
    baseDelayMs: 1,
    delay,
  }).then(
    () => assert.fail('should have rejected'),
    (e: unknown) => e,
  );
  assert.equal(err, 'just a string');
});

test('attempts:1 means no retry at all', async () => {
  const { requested, delay } = instantDelays();
  let calls = 0;
  await assert.rejects(
    retry(
      async () => {
        calls++;
        throw new Error('single shot');
      },
      { attempts: 1, baseDelayMs: 100, delay },
    ),
    /single shot/,
  );
  assert.equal(calls, 1);
  assert.deepEqual(requested, []);
});

test('attempts must be a positive integer', () => {
  const fn = async () => 'x';
  assert.throws(() => retry(fn, { attempts: 0, baseDelayMs: 1 }), TypeError);
  assert.throws(() => retry(fn, { attempts: -1, baseDelayMs: 1 }), TypeError);
  assert.throws(() => retry(fn, { attempts: 2.5, baseDelayMs: 1 }), TypeError);
});

// -- AbortSignal -------------------------------------------------------------

test('an already-aborted signal rejects immediately without calling fn', async () => {
  const { delay } = instantDelays();
  const ac = new AbortController();
  const reason = new Error('shutting down');
  ac.abort(reason);
  let calls = 0;
  await assert.rejects(
    retry(
      async () => {
        calls++;
        return 'never';
      },
      { attempts: 3, baseDelayMs: 1, signal: ac.signal, delay },
    ),
    reason,
  );
  assert.equal(calls, 0);
});

test('aborting during backoff rejects with the abort reason and stops retrying', async () => {
  const { entries, delay } = manualDelays();
  const ac = new AbortController();
  let calls = 0;
  const p = retry(
    async () => {
      calls++;
      throw new Error('down');
    },
    { attempts: 5, baseDelayMs: 50, signal: ac.signal, delay },
  );
  const guarded = p.catch((e: unknown) => e);
  await tick();
  assert.equal(calls, 1);
  assert.equal(entries.length, 1); // sitting in the first backoff
  const reason = new Error('user navigated away');
  ac.abort(reason);
  assert.equal(await guarded, reason);
  assert.equal(entries[0].aborted, true); // the backoff timer was cancelled
  await tick();
  assert.equal(calls, 1);
});

test('aborting mid-attempt rejects promptly and aborts the per-attempt signal', async () => {
  const { delay } = instantDelays();
  const ac = new AbortController();
  let attemptSignal: AbortSignal | null = null;
  const p = retry(
    ({ signal }) =>
      new Promise<never>((_, rej) => {
        attemptSignal = signal;
        signal.addEventListener('abort', () => rej(signal.reason), { once: true });
      }),
    { attempts: 3, baseDelayMs: 1, signal: ac.signal, delay },
  );
  const guarded = p.catch((e: unknown) => e);
  await tick();
  assert.equal(attemptSignal!.aborted, false);
  const reason = new Error('cancelled by caller');
  ac.abort(reason);
  assert.equal(await guarded, reason);
  assert.equal(attemptSignal!.aborted, true);
});

// -- per-attempt timeout -----------------------------------------------------

test('a hung attempt times out, is retried, and the next attempt can win', async () => {
  const { entries, delay } = manualDelays();
  const signals: AbortSignal[] = [];
  const p = retry(
    ({ attempt, signal }) => {
      signals.push(signal);
      if (attempt === 1) {
        return new Promise<string>((_, rej) => {
          signal.addEventListener('abort', () => rej(signal.reason), { once: true });
        });
      }
      return Promise.resolve('second time lucky');
    },
    { attempts: 2, baseDelayMs: 30, attemptTimeoutMs: 500, delay },
  );
  await tick();
  // one pending delay: the 500ms attempt timer
  assert.deepEqual(entries.map((e) => e.ms), [500]);
  entries[0].resolve(); // the attempt timer fires
  await tick();
  assert.equal(signals[0].aborted, true); // hung attempt was told to stop
  // now we should be sitting in the 30ms backoff
  assert.deepEqual(entries.map((e) => e.ms), [500, 30]);
  entries[1].resolve();
  assert.equal(await p, 'second time lucky');
});

test('a timeout on the final attempt rejects with a TimeoutError', async () => {
  const { entries, delay } = manualDelays();
  const p = retry(
    ({ signal }) =>
      new Promise<never>((_, rej) => {
        signal.addEventListener('abort', () => rej(signal.reason), { once: true });
      }),
    { attempts: 1, baseDelayMs: 1, attemptTimeoutMs: 200, delay },
  );
  const guarded = p.catch((e: unknown) => e);
  await tick();
  entries[0].resolve();
  const err = (await guarded) as Error;
  assert.equal(err.name, 'TimeoutError');
});

test('a fast success cancels the pending attempt timer', async () => {
  const { entries, delay } = manualDelays();
  const result = await retry(async () => 'quick', {
    attempts: 3,
    baseDelayMs: 1,
    attemptTimeoutMs: 5000,
    delay,
  });
  assert.equal(result, 'quick');
  await tick();
  assert.equal(entries.length, 1);
  assert.equal(entries[0].ms, 5000);
  assert.equal(entries[0].aborted, true); // timer released, nothing left running
});

test('without attemptTimeoutMs no timeout timer is ever started', async () => {
  const { entries, delay } = manualDelays();
  const result = await retry(async () => 'plain', { attempts: 2, baseDelayMs: 10, delay });
  assert.equal(result, 'plain');
  assert.equal(entries.length, 0);
});
