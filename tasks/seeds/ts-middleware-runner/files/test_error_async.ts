import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Pipeline, type Context } from './middleware.ts';

const ctx = (): Context => ({ path: '/x', state: {} });
const tick = () => new Promise<void>((r) => setImmediate(r));

// --- async middleware ---

test('async middleware are awaited before run resolves', async () => {
  const p = new Pipeline().use(async (c: Context, next: () => Promise<void>) => {
    await tick();
    c.state.loaded = true;
    await next();
  });
  const out = await p.run(ctx());
  assert.equal(out.state.loaded, true);
});

test('await next() sees work done by async downstream middleware', async () => {
  const log: string[] = [];
  const p = new Pipeline()
    .use(async (c: Context, next: () => Promise<void>) => {
      log.push('timer:start');
      await next();
      log.push(`timer:end user=${c.state.user}`);
    })
    .use(async (c: Context) => {
      await tick();
      c.state.user = 'ada';
      log.push('load');
    });
  await p.run(ctx());
  assert.deepEqual(log, ['timer:start', 'load', 'timer:end user=ada']);
});

// --- error handlers (arity 3, Express-style) ---

test('a throw skips the remaining regular middleware and reaches the error handler', async () => {
  const log: string[] = [];
  const p = new Pipeline()
    .use((c: Context, next: () => Promise<void>) => {
      log.push('first');
      return next();
    })
    .use(() => {
      throw new Error('db down');
    })
    .use(() => {
      log.push('skipped');
    })
    .use((err: unknown, c: Context, next: unknown) => {
      log.push(`caught:${(err as Error).message}`);
      c.response = { status: 500 };
    });
  const out = await p.run(ctx());
  assert.deepEqual(log, ['first', 'caught:db down']);
  assert.deepEqual(out.response, { status: 500 });
});

test('a rejected async middleware is handled exactly like a throw', async () => {
  const p = new Pipeline()
    .use(async () => {
      await tick();
      throw new Error('rejected');
    })
    .use((err: unknown, c: Context, next: unknown) => {
      c.response = `handled ${(err as Error).message}`;
    });
  const out = await p.run(ctx());
  assert.equal(out.response, 'handled rejected');
});

test('an error thrown on the way OUT (after next()) still reaches the handler', async () => {
  const p = new Pipeline()
    .use(async (c: Context, next: () => Promise<void>) => {
      await next();
      throw new Error('post-processing blew up');
    })
    .use((c: Context) => {
      c.state.done = true;
    })
    .use((err: unknown, c: Context, next: unknown) => {
      c.response = 'saved';
    });
  const out = await p.run(ctx());
  assert.equal(out.state.done, true);
  assert.equal(out.response, 'saved');
});

test('next(err) inside a handler forwards to the next handler', async () => {
  const log: string[] = [];
  const p = new Pipeline()
    .use(() => {
      throw new Error('original');
    })
    .use((err: unknown, c: Context, next: (e?: unknown) => Promise<void>) => {
      log.push('first-handler');
      return next(new Error('rewrapped'));
    })
    .use((err: unknown, c: Context, next: unknown) => {
      log.push(`second-handler:${(err as Error).message}`);
    });
  await p.run(ctx());
  assert.deepEqual(log, ['first-handler', 'second-handler:rewrapped']);
});

test('a handler that itself throws passes the NEW error down the handler chain', async () => {
  const p = new Pipeline()
    .use(() => {
      throw new Error('original');
    })
    .use((err: unknown, c: Context, next: unknown) => {
      throw new Error('handler exploded');
    })
    .use((err: unknown, c: Context, next: unknown) => {
      c.response = (err as Error).message;
    });
  const out = await p.run(ctx());
  assert.equal(out.response, 'handler exploded');
});

test('next() with no argument in a handler marks the error handled', async () => {
  const log: string[] = [];
  const p = new Pipeline()
    .use(() => {
      throw new Error('minor');
    })
    .use((err: unknown, c: Context, next: (e?: unknown) => Promise<void>) => {
      log.push('first');
      return next();
    })
    .use((err: unknown, c: Context, next: unknown) => {
      log.push('second');
    });
  await p.run(ctx()); // resolves: the error was declared handled
  assert.deepEqual(log, ['first']);
});

test('when no handler is registered the rejection carries the original error', async () => {
  const p = new Pipeline().use(() => {
    throw new Error('unseen');
  });
  await assert.rejects(async () => p.run(ctx()), /unseen/);
});

test('when every handler re-forwards, run rejects with the last error', async () => {
  const p = new Pipeline()
    .use(() => {
      throw new Error('first');
    })
    .use((err: unknown, c: Context, next: (e?: unknown) => Promise<void>) => next(err));
  await assert.rejects(async () => p.run(ctx()), /first/);
});

test('error handlers are invisible to a clean run', async () => {
  const log: string[] = [];
  const p = new Pipeline()
    .use((c: Context, next: () => Promise<void>) => {
      log.push('before');
      return next();
    })
    .use((err: unknown, c: Context, next: unknown) => {
      log.push('handler');
    })
    .use((c: Context) => {
      log.push('after');
    });
  await p.run(ctx());
  assert.deepEqual(log, ['before', 'after']);
});
