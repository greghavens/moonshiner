import { test } from 'node:test';
import assert from 'node:assert/strict';
import { runPipeline } from './pipeline.ts';

// -- helpers -----------------------------------------------------------------

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (v: T) => void;
  reject: (e: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

async function tick(turns = 10): Promise<void> {
  for (let i = 0; i < turns; i++) await Promise.resolve();
}

// -- happy path --------------------------------------------------------------

test('threads each stage output into the next stage input', async () => {
  const inputs: unknown[] = [];
  const result = await runPipeline(
    [
      {
        name: 'parse',
        run: (input: unknown) => {
          inputs.push(input);
          return Number(input) + 1;
        },
      },
      {
        name: 'double',
        run: async (input: unknown) => {
          inputs.push(input);
          return (input as number) * 2;
        },
      },
      {
        name: 'label',
        run: (input: unknown) => {
          inputs.push(input);
          return `result=${input}`;
        },
      },
    ],
    '20',
  );
  assert.equal(result, 'result=42');
  assert.deepEqual(inputs, ['20', 21, 42]);
});

test('cleanups do not run when every stage succeeds', async () => {
  const events: string[] = [];
  await runPipeline(
    [
      { name: 'a', run: async () => 1, cleanup: () => void events.push('cleanup:a') },
      { name: 'b', run: async () => 2, cleanup: () => void events.push('cleanup:b') },
    ],
    null,
  );
  assert.deepEqual(events, []);
});

test('an empty pipeline resolves with its input', async () => {
  assert.equal(await runPipeline([], 'passthrough'), 'passthrough');
});

// -- failure and rollback ----------------------------------------------------

test('a stage failure rolls back completed stages in reverse order', async () => {
  const events: string[] = [];
  const boom = new Error('disk full');
  const stage = (name: string, fail = false) => ({
    name,
    run: async () => {
      events.push(`run:${name}`);
      if (fail) throw boom;
      return name;
    },
    cleanup: async () => {
      events.push(`cleanup:${name}`);
    },
  });
  await assert.rejects(
    runPipeline([stage('provision'), stage('configure'), stage('announce', true)], null),
    boom,
  );
  assert.deepEqual(events, [
    'run:provision',
    'run:configure',
    'run:announce',
    'cleanup:configure',
    'cleanup:provision',
  ]);
});

test('the failing stage own cleanup is not invoked', async () => {
  const events: string[] = [];
  await assert.rejects(
    runPipeline(
      [
        {
          name: 'bad',
          run: async () => {
            throw new Error('never made it');
          },
          cleanup: () => void events.push('cleanup:bad'),
        },
      ],
      null,
    ),
    /never made it/,
  );
  assert.deepEqual(events, []);
});

test('stages without a cleanup are skipped during rollback', async () => {
  const events: string[] = [];
  await assert.rejects(
    runPipeline(
      [
        { name: 'a', run: async () => 1, cleanup: () => void events.push('cleanup:a') },
        { name: 'b', run: async () => 2 }, // no cleanup
        {
          name: 'c',
          run: async () => {
            throw new Error('late failure');
          },
        },
      ],
      null,
    ),
    /late failure/,
  );
  assert.deepEqual(events, ['cleanup:a']);
});

test('a throwing cleanup does not stop the rest of the rollback or mask the error', async () => {
  const events: string[] = [];
  const original = new Error('stage exploded');
  await assert.rejects(
    runPipeline(
      [
        { name: 'a', run: async () => 1, cleanup: () => void events.push('cleanup:a') },
        {
          name: 'b',
          run: async () => 2,
          cleanup: () => {
            events.push('cleanup:b');
            throw new Error('rollback also broken');
          },
        },
        {
          name: 'c',
          run: async () => {
            throw original;
          },
        },
      ],
      null,
    ),
    original,
  );
  assert.deepEqual(events, ['cleanup:b', 'cleanup:a']);
});

test('cleanups run one at a time, each awaited before the next starts', async () => {
  const events: string[] = [];
  const slowCleanup = (name: string) => async () => {
    events.push(`cleanup:${name}:start`);
    await tick(3);
    events.push(`cleanup:${name}:end`);
  };
  await assert.rejects(
    runPipeline(
      [
        { name: 'a', run: async () => 1, cleanup: slowCleanup('a') },
        { name: 'b', run: async () => 2, cleanup: slowCleanup('b') },
        {
          name: 'c',
          run: async () => {
            throw new Error('nope');
          },
        },
      ],
      null,
    ),
    /nope/,
  );
  assert.deepEqual(events, [
    'cleanup:b:start',
    'cleanup:b:end',
    'cleanup:a:start',
    'cleanup:a:end',
  ]);
});

// -- cancellation ------------------------------------------------------------

test('an already-aborted signal rejects before any stage runs', async () => {
  const ac = new AbortController();
  const reason = new Error('deploy window closed');
  ac.abort(reason);
  let ran = false;
  await assert.rejects(
    runPipeline(
      [
        {
          name: 'a',
          run: async () => {
            ran = true;
            return 1;
          },
        },
      ],
      null,
      { signal: ac.signal },
    ),
    reason,
  );
  assert.equal(ran, false);
});

test('each stage receives an AbortSignal that reflects cancellation', async () => {
  const ac = new AbortController();
  let seen: AbortSignal | null = null;
  await runPipeline(
    [
      {
        name: 'a',
        run: async (_input: unknown, signal: AbortSignal) => {
          seen = signal;
          return 1;
        },
      },
    ],
    null,
    { signal: ac.signal },
  );
  assert.ok(seen instanceof AbortSignal);
  assert.equal(seen!.aborted, false);
});

test('aborting mid-stage: waits for the stage, rolls back, rejects with the reason', async () => {
  const events: string[] = [];
  const ac = new AbortController();
  const reason = new Error('operator hit cancel');
  const gate = deferred<never>();
  const p = runPipeline(
    [
      { name: 'a', run: async () => events.push('run:a'), cleanup: () => void events.push('cleanup:a') },
      {
        name: 'b',
        run: (_input: unknown, signal: AbortSignal) => {
          events.push('run:b');
          signal.addEventListener('abort', () => gate.reject(signal.reason), { once: true });
          return gate.promise;
        },
        cleanup: () => void events.push('cleanup:b'),
      },
      { name: 'c', run: async () => events.push('run:c'), cleanup: () => void events.push('cleanup:c') },
    ],
    null,
    { signal: ac.signal },
  );
  const guarded = p.catch((e: unknown) => e);
  await tick();
  assert.deepEqual(events, ['run:a', 'run:b']);
  ac.abort(reason);
  assert.equal(await guarded, reason);
  // b never completed, so only a rolls back; c never starts
  assert.deepEqual(events, ['run:a', 'run:b', 'cleanup:a']);
});

test('a stage that completes despite the abort is still rolled back', async () => {
  const events: string[] = [];
  const ac = new AbortController();
  const reason = new Error('cancelled');
  const gate = deferred<string>();
  const p = runPipeline(
    [
      { name: 'a', run: async () => 'A', cleanup: () => void events.push('cleanup:a') },
      {
        name: 'b',
        run: () => gate.promise, // ignores its signal, finishes anyway
        cleanup: () => void events.push('cleanup:b'),
      },
      { name: 'c', run: async () => events.push('run:c') },
    ],
    null,
    { signal: ac.signal },
  );
  const guarded = p.catch((e: unknown) => e);
  await tick();
  ac.abort(reason);
  gate.resolve('B'); // b finishes after the abort
  assert.equal(await guarded, reason);
  assert.deepEqual(events, ['cleanup:b', 'cleanup:a']);
});

test('aborting between stages prevents the next stage from starting', async () => {
  const events: string[] = [];
  const ac = new AbortController();
  const reason = new Error('halt');
  const p = runPipeline(
    [
      {
        name: 'a',
        run: async () => {
          events.push('run:a');
          ac.abort(reason); // cancellation lands while a is finishing
          return 'A';
        },
        cleanup: () => void events.push('cleanup:a'),
      },
      { name: 'b', run: async () => events.push('run:b') },
    ],
    null,
    { signal: ac.signal },
  );
  assert.equal(await p.catch((e: unknown) => e), reason);
  assert.deepEqual(events, ['run:a', 'cleanup:a']);
});
