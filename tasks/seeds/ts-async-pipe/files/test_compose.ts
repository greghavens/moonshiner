import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pipe, compose, tap } from './compose.ts';

const inc = (n: number) => n + 1;
const double = (n: number) => n * 2;
const asyncInc = async (n: number) => n + 1;
const delayed = <T>(value: T, ms = 1): Promise<T> =>
  new Promise((resolve) => setTimeout(() => resolve(value), ms));

test('pipe runs left to right', () => {
  assert.equal(pipe(inc, double)(5), 12);
  assert.equal(pipe(double, inc)(5), 11);
});

test('compose runs right to left', () => {
  assert.equal(compose(inc, double)(5), 11);
  assert.equal(compose(double, inc)(5), 12);
});

test('an all-sync pipeline returns a plain value, not a promise', () => {
  const result = pipe(inc, double)(1);
  assert.equal(typeof result, 'number');
  assert.equal(result, 4);
});

test('pipe with no functions is identity on its first argument', () => {
  assert.equal(pipe()(42), 42);
  assert.deepEqual(pipe()({ a: 1 }), { a: 1 });
});

test('the first function receives every call argument', () => {
  const join = (a: string, b: string, c: string) => [a, b, c].join('-');
  assert.equal(pipe(join, (s: string) => s.toUpperCase())('x', 'y', 'z'), 'X-Y-Z');
});

test('one async step turns the pipeline result into a promise', async () => {
  const run = pipe(inc, asyncInc, double);
  const out = run(1);
  assert.equal(typeof (out as Promise<number>).then, 'function');
  assert.equal(await out, 6);
});

test('sync steps after an async step receive the resolved value', async () => {
  const run = pipe(
    (n: number) => delayed(n + 10),
    (n: number) => {
      assert.equal(typeof n, 'number', 'must not receive a promise');
      return n * 2;
    },
  );
  assert.equal(await run(5), 30);
});

test('consecutive async steps each get the settled value', async () => {
  const run = pipe(asyncInc, asyncInc, asyncInc);
  assert.equal(await run(0), 3);
});

test('an all-sync chain with a throwing step throws synchronously', () => {
  const run = pipe(inc, () => {
    throw new Error('sync boom');
  }, double);
  assert.throws(() => run(1), /sync boom/);
});

test('a sync throw after an async step becomes a rejection', async () => {
  const run = pipe(asyncInc, () => {
    throw new Error('late boom');
  });
  let threwSync = false;
  let out: unknown;
  try {
    out = run(1);
  } catch {
    threwSync = true;
  }
  assert.equal(threwSync, false, 'must not throw synchronously past an async step');
  await assert.rejects(out as Promise<unknown>, /late boom/);
});

test('a rejection stops the pipeline; later steps never run', async () => {
  const calls: string[] = [];
  const run = pipe(
    async (n: number) => {
      calls.push('first');
      throw new Error('reject me');
    },
    (n: number) => {
      calls.push('second');
      return n;
    },
  );
  await assert.rejects(run(1) as Promise<unknown>, /reject me/);
  assert.deepEqual(calls, ['first']);
});

test('non-function arguments are rejected at composition time', () => {
  assert.throws(() => pipe(inc, 42 as never), TypeError);
  assert.throws(() => compose('nope' as never), TypeError);
  // and NOT at call time: composing valid fns never throws
  const ok = pipe(inc);
  assert.equal(ok(1), 2);
});

test('tap passes the value through and runs the side effect', () => {
  const seen: number[] = [];
  const run = pipe(inc, tap((n: number) => { seen.push(n); }), double);
  assert.equal(run(1), 4);
  assert.deepEqual(seen, [2]);
});

test('tap ignores the side effect\'s return value', () => {
  const run = pipe(tap(() => 999), inc);
  assert.equal(run(1), 2);
});

test('an async tap is awaited but still yields the original value', async () => {
  const seen: number[] = [];
  const run = pipe(
    inc,
    tap(async (n: number) => {
      await delayed(null, 2);
      seen.push(n);
    }),
    double,
  );
  assert.equal(await run(1), 4);
  assert.deepEqual(seen, [2], 'the side effect must have completed first');
});

test('a throwing tap fails the pipeline', async () => {
  const syncRun = pipe(tap(() => { throw new Error('tap boom'); }), inc);
  assert.throws(() => syncRun(1), /tap boom/);

  const asyncRun = pipe(tap(async () => { throw new Error('async tap boom'); }), inc);
  await assert.rejects(asyncRun(1) as Promise<unknown>, /async tap boom/);
});

test('a realistic enrichment chain works end to end', async () => {
  const audit: string[] = [];
  const enrich = pipe(
    (id: number) => ({ id, name: `user-${id}` }),
    tap((u: { id: number }) => audit.push(`loaded:${u.id}`)),
    async (u: { id: number; name: string }) => ({ ...u, score: await delayed(u.id * 10) }),
    (u: { name: string; score: number }) => `${u.name}:${u.score}`,
  );
  assert.equal(await enrich(7), 'user-7:70');
  assert.deepEqual(audit, ['loaded:7']);
});
