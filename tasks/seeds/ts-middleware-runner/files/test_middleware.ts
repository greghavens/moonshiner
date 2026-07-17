// NOTE: every call site in the gateway awaits pipeline.run(), so these
// tests do too — run() must always be awaitable.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Pipeline, type Context } from './middleware.ts';

const ctx = (): Context => ({ path: '/x', state: {} });

test('middleware run in registration order, unwinding in reverse after next()', async () => {
  const log: string[] = [];
  const p = new Pipeline()
    .use((c, next) => {
      log.push('auth:in');
      next();
      log.push('auth:out');
    })
    .use((c, next) => {
      log.push('log:in');
      next();
      log.push('log:out');
    });
  await p.run(ctx());
  assert.deepEqual(log, ['auth:in', 'log:in', 'log:out', 'auth:out']);
});

test('run resolves to the same context object, mutated by the chain', async () => {
  const p = new Pipeline().use((c, next) => {
    c.state.user = 'ada';
    next();
  });
  const c = ctx();
  const out = await p.run(c);
  assert.equal(out, c);
  assert.equal(out.state.user, 'ada');
});

test('skipping next() short-circuits everything after', async () => {
  const log: string[] = [];
  const p = new Pipeline()
    .use((c) => {
      log.push('gatekeeper');
      c.response = { status: 403 };
    })
    .use(() => {
      log.push('never');
    });
  const out = await p.run(ctx());
  assert.deepEqual(log, ['gatekeeper']);
  assert.deepEqual(out.response, { status: 403 });
});

test('calling next() twice in one middleware is an error', async () => {
  const p = new Pipeline().use((c, next) => {
    next();
    next();
  });
  await assert.rejects(async () => p.run(ctx()), /twice/);
});

test('a throwing middleware propagates out of run', async () => {
  const p = new Pipeline()
    .use((c, next) => next())
    .use(() => {
      throw new Error('boom');
    });
  await assert.rejects(async () => p.run(ctx()), /boom/);
});

test('use() chains fluently and an empty pipeline is a no-op', async () => {
  const p = new Pipeline();
  assert.equal(p.use((c, next) => next()), p);
  const out = await new Pipeline().run(ctx());
  assert.deepEqual(out.state, {});
});
