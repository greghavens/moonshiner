import { test } from 'node:test';
import assert from 'node:assert/strict';
import { StateMachine, InvalidTransitionError } from './fsm.ts';

type Ctx = Record<string, unknown>;

function docMachine(log: string[] = []) {
  return new StateMachine({
    initial: 'draft',
    context: { revisions: 0, approvals: 0 } as Ctx,
    onTransition: (info: { from: string; to: string; event: string }) => {
      log.push(`transition:${info.from}->${info.to}:${info.event}`);
    },
    states: {
      draft: {
        on: { submit: 'review' },
        onExit: () => log.push('exit:draft'),
        onEnter: () => log.push('enter:draft'),
      },
      review: {
        on: {
          approve: {
            target: 'published',
            guard: (ctx: Ctx) => (ctx.approvals as number) >= 2,
          },
          reject: 'draft',
          nudge: 'review',
        },
        onExit: () => log.push('exit:review'),
        onEnter: () => log.push('enter:review'),
      },
      published: {
        onEnter: () => log.push('enter:published'),
      },
    },
  });
}

test('starts in the initial state without firing any hooks', () => {
  const log: string[] = [];
  const m = docMachine(log);
  assert.equal(m.state, 'draft');
  assert.deepEqual(log, []);
});

test('exposes the context object', () => {
  const m = docMachine();
  assert.deepEqual(m.context, { revisions: 0, approvals: 0 });
});

test('send follows a plain string transition and returns the new state', () => {
  const m = docMachine();
  assert.equal(m.send('submit'), 'review');
  assert.equal(m.state, 'review');
});

test('an event with no transition from the current state throws InvalidTransitionError', () => {
  const m = docMachine();
  assert.throws(() => m.send('approve'), InvalidTransitionError);
  try {
    m.send('publish_now');
  } catch (err) {
    assert.ok(err instanceof InvalidTransitionError);
    assert.ok(err instanceof Error);
    const e = err as InvalidTransitionError & { from: string; event: string };
    assert.equal(e.from, 'draft');
    assert.equal(e.event, 'publish_now');
    return;
  }
  assert.fail('expected send to throw');
});

test('a failed send leaves the machine state untouched', () => {
  const m = docMachine();
  assert.throws(() => m.send('reject'), InvalidTransitionError);
  assert.equal(m.state, 'draft');
});

test('hooks fire in exit, onTransition, enter order', () => {
  const log: string[] = [];
  const m = docMachine(log);
  m.send('submit');
  assert.deepEqual(log, ['exit:draft', 'transition:draft->review:submit', 'enter:review']);
});

test('each hook receives one info object with from, to, event, payload, context', () => {
  const seen: unknown[] = [];
  const m = new StateMachine({
    initial: 'a',
    context: { tag: 'ctx' },
    onTransition: (info: unknown) => seen.push(['transition', info]),
    states: {
      a: { on: { go: 'b' }, onExit: (info: unknown) => seen.push(['exit', info]) },
      b: { onEnter: (info: unknown) => seen.push(['enter', info]) },
    },
  });
  m.send('go', { reason: 'test' });
  const expected = {
    from: 'a',
    to: 'b',
    event: 'go',
    payload: { reason: 'test' },
    context: { tag: 'ctx' },
  };
  assert.deepEqual(seen, [
    ['exit', expected],
    ['transition', expected],
    ['enter', expected],
  ]);
});

test('an explicit self-transition re-fires exit and enter', () => {
  const log: string[] = [];
  const m = docMachine(log);
  m.send('submit');
  log.length = 0;
  assert.equal(m.send('nudge'), 'review');
  assert.deepEqual(log, ['exit:review', 'transition:review->review:nudge', 'enter:review']);
});

test('a guard returning false blocks the transition quietly', () => {
  const log: string[] = [];
  const m = docMachine(log);
  m.send('submit');
  log.length = 0;
  assert.equal(m.send('approve'), 'review'); // approvals: 0 — blocked
  assert.equal(m.state, 'review');
  assert.deepEqual(log, [], 'no hooks may fire on a blocked transition');
});

test('a guard returning true lets the transition proceed', () => {
  const m = docMachine();
  m.send('submit');
  (m.context as Ctx).approvals = 2;
  assert.equal(m.send('approve'), 'published');
  assert.equal(m.state, 'published');
});

test('guards receive the context and the payload', () => {
  const calls: unknown[] = [];
  const m = new StateMachine({
    initial: 'locked',
    context: { pin: '4921' },
    states: {
      locked: {
        on: {
          unlock: {
            target: 'open',
            guard: (ctx: { pin: string }, payload: { pin?: string } | undefined) => {
              calls.push([ctx.pin, payload]);
              return payload?.pin === ctx.pin;
            },
          },
        },
      },
      open: {},
    },
  });
  assert.equal(m.send('unlock', { pin: '0000' }), 'locked');
  assert.equal(m.send('unlock', { pin: '4921' }), 'open');
  assert.deepEqual(calls, [
    ['4921', { pin: '0000' }],
    ['4921', { pin: '4921' }],
  ]);
});

test('can() reports transitions without side effects', () => {
  const log: string[] = [];
  const m = docMachine(log);
  assert.equal(m.can('submit'), true);
  assert.equal(m.can('approve'), false);
  assert.equal(m.state, 'draft');
  assert.deepEqual(log, []);
  m.send('submit');
  assert.equal(m.can('approve'), false, 'guard says no with zero approvals');
  (m.context as Ctx).approvals = 3;
  assert.equal(m.can('approve'), true);
  assert.equal(m.state, 'review', 'can() must not transition');
});

test('hooks may mutate context and later guards observe it', () => {
  const m = new StateMachine({
    initial: 'idle',
    context: { runs: 0 },
    states: {
      idle: { on: { start: 'running' } },
      running: {
        onEnter: (info: { context: { runs: number } }) => {
          info.context.runs += 1;
        },
        on: {
          finish: {
            target: 'idle',
            guard: (ctx: { runs: number }) => ctx.runs > 0,
          },
        },
      },
    },
  });
  m.send('start');
  assert.equal((m.context as { runs: number }).runs, 1);
  assert.equal(m.can('finish'), true);
  assert.equal(m.send('finish'), 'idle');
});

test('constructor rejects an unknown initial state', () => {
  assert.throws(
    () => new StateMachine({ initial: 'ghost', states: { real: {} } }),
    /ghost/,
  );
});

test('constructor rejects a transition target that is not a declared state', () => {
  assert.throws(
    () =>
      new StateMachine({
        initial: 'a',
        states: { a: { on: { go: 'nowhere' } } },
      }),
    /nowhere/,
  );
  assert.throws(
    () =>
      new StateMachine({
        initial: 'a',
        states: { a: { on: { go: { target: 'void' } } }, b: {} },
      }),
    /void/,
  );
});

test('a full workflow walk keeps state and hooks consistent', () => {
  const log: string[] = [];
  const m = docMachine(log);
  m.send('submit');
  m.send('reject');
  m.send('submit');
  (m.context as Ctx).approvals = 2;
  m.send('approve');
  assert.equal(m.state, 'published');
  assert.deepEqual(log, [
    'exit:draft', 'transition:draft->review:submit', 'enter:review',
    'exit:review', 'transition:review->draft:reject', 'enter:draft',
    'exit:draft', 'transition:draft->review:submit', 'enter:review',
    'exit:review', 'transition:review->published:approve', 'enter:published',
  ]);
});
