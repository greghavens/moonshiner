// Acceptance tests for the two-level statechart engine (statechart.ts).
//
// The fixture models our label-printer controller: three top-level modes,
// two of them compound. Exit/entry ordering, initial-substate descent,
// internal vs external self-transitions, and leaf-to-root event bubbling
// are all pinned as exact arrays on the result object send() returns.
//
// Run: node --test test_statechart.ts

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Statechart, ChartError } from './statechart.ts';

function printerChart() {
  return {
    initial: 'offline',
    states: {
      offline: {
        on: { power: 'online', diag: 'maintenance', restore: 'paused' },
      },
      online: {
        initial: 'idle',
        on: { power: 'offline', reset: 'online', tick: { internal: true } },
        children: {
          idle: { on: { job: 'running' } },
          running: {
            on: { pause: 'paused', done: 'idle', retry: 'running', jam: 'blocked' },
          },
          paused: { on: { resume: 'running', power: 'offline' } },
          blocked: {},
        },
      },
      maintenance: {
        initial: 'diag',
        on: { power: 'offline' },
        children: {
          diag: { on: { ok: 'idle', deep: 'update' } },
          update: { on: { ok: 'diag' } },
        },
      },
    },
  };
}

function chartError(def: object): void {
  assert.throws(() => new Statechart(def as ConstructorParameters<typeof Statechart>[0]),
    ChartError);
}

test('definition validation catches structural mistakes', () => {
  // duplicate id across levels
  chartError({
    initial: 'a',
    states: {
      a: { on: {} },
      b: { initial: 'a', children: { a: {} } },
    },
  });
  // nesting deeper than two levels
  chartError({
    initial: 'a',
    states: { a: { initial: 'b', children: { b: { initial: 'c', children: { c: {} } } } } },
  });
  // compound state without an initial substate
  chartError({ initial: 'a', states: { a: { children: { b: {} } } } });
  // initial naming a state that is not a child
  chartError({
    initial: 'a',
    states: { a: { initial: 'z', children: { b: {} } }, z: {} },
  });
  // initial declared on a leaf
  chartError({ initial: 'a', states: { a: { initial: 'a' } } });
  // chart initial must be a top-level state
  chartError({
    initial: 'b',
    states: { a: { initial: 'b', children: { b: {} } } },
  });
  // chart initial that does not exist at all
  chartError({ initial: 'ghost', states: { a: {} } });
  // unknown transition target
  chartError({ initial: 'a', states: { a: { on: { go: 'nowhere' } } } });
  // internal transition pointing somewhere else
  chartError({
    initial: 'a',
    states: { a: {}, b: { on: { poke: { internal: true, target: 'a' } } } },
  });
  // object transition with neither target nor internal
  chartError({ initial: 'a', states: { a: { on: { go: {} } } } });
});

test('construction lands on the initial state, descending if compound', () => {
  const m = new Statechart(printerChart());
  assert.deepEqual(m.current(), ['offline']);

  const def = printerChart();
  def.initial = 'online';
  const n = new Statechart(def);
  assert.deepEqual(n.current(), ['online', 'idle']);
});

test('entering a compound state descends to its initial substate', () => {
  const m = new Statechart(printerChart());
  assert.deepEqual(m.send('power'), {
    event: 'power',
    handled: true,
    by: 'offline',
    internal: false,
    from: 'offline',
    to: 'idle',
    exited: ['offline'],
    entered: ['online', 'idle'],
  });
  assert.deepEqual(m.current(), ['online', 'idle']);
});

test('sibling transitions stay inside the compound parent', () => {
  const m = new Statechart(printerChart());
  m.send('power');
  const r = m.send('job');
  assert.equal(r.by, 'idle');
  assert.deepEqual(r.exited, ['idle'], 'the shared parent must not exit');
  assert.deepEqual(r.entered, ['running']);
  assert.equal(r.from, 'idle');
  assert.equal(r.to, 'running');
  assert.deepEqual(m.current(), ['online', 'running']);
});

test('unhandled leaf events bubble to the parent handler', () => {
  const m = new Statechart(printerChart());
  m.send('power');
  m.send('job'); // ['online', 'running']; 'running' has no power handler
  const r = m.send('power');
  assert.equal(r.by, 'online');
  assert.deepEqual(r.exited, ['running', 'online'], 'exit runs deepest-first');
  assert.deepEqual(r.entered, ['offline']);
  assert.equal(r.to, 'offline');
  assert.deepEqual(m.current(), ['offline']);
});

test('a leaf handler shadows the parent handler for the same event', () => {
  const m = new Statechart(printerChart());
  m.send('power');
  m.send('job');
  m.send('pause'); // ['online', 'paused']; paused declares its own power
  const r = m.send('power');
  assert.equal(r.by, 'paused', 'the deepest handler wins');
  assert.deepEqual(r.exited, ['paused', 'online']);
  assert.deepEqual(r.entered, ['offline']);
});

test('targeting a nested leaf enters it directly, skipping the initial', () => {
  const m = new Statechart(printerChart());
  const r = m.send('restore'); // offline -> paused, deep target
  assert.deepEqual(r.exited, ['offline']);
  assert.deepEqual(r.entered, ['online', 'paused'], 'no detour through idle');
  assert.deepEqual(m.current(), ['online', 'paused']);
});

test('targeting a compound from outside enters via its initial substate', () => {
  const m = new Statechart(printerChart());
  const r = m.send('diag'); // offline -> maintenance
  assert.deepEqual(r.exited, ['offline']);
  assert.deepEqual(r.entered, ['maintenance', 'diag']);
  assert.deepEqual(m.current(), ['maintenance', 'diag']);
});

test('cross-subtree transitions exit one branch and enter the other', () => {
  const m = new Statechart(printerChart());
  m.send('diag'); // ['maintenance', 'diag']
  const r = m.send('ok'); // diag -> idle, across subtrees
  assert.deepEqual(r.exited, ['diag', 'maintenance']);
  assert.deepEqual(r.entered, ['online', 'idle']);
  assert.equal(r.from, 'diag');
  assert.equal(r.to, 'idle');
});

test('an external self-transition on a compound exits and re-enters it', () => {
  const m = new Statechart(printerChart());
  m.send('power');
  m.send('job'); // ['online', 'running']
  const r = m.send('reset'); // online.on.reset targets online itself
  assert.equal(r.by, 'online');
  assert.deepEqual(r.exited, ['running', 'online']);
  assert.deepEqual(r.entered, ['online', 'idle'], 're-entry re-runs the initial descent');
  assert.equal(r.from, 'running');
  assert.equal(r.to, 'idle');
});

test('an external self-transition on a leaf exits and re-enters the leaf', () => {
  const m = new Statechart(printerChart());
  m.send('power');
  m.send('job'); // ['online', 'running']
  const r = m.send('retry');
  assert.deepEqual(r.exited, ['running']);
  assert.deepEqual(r.entered, ['running']);
  assert.equal(r.internal, false);
  assert.deepEqual(m.current(), ['online', 'running']);
});

test('an internal transition is handled with no exits or entries', () => {
  const m = new Statechart(printerChart());
  m.send('power');
  m.send('job'); // ['online', 'running']
  assert.deepEqual(m.send('tick'), {
    event: 'tick',
    handled: true,
    by: 'online',
    internal: true,
    from: 'running',
    to: 'running',
    exited: [],
    entered: [],
  });
  assert.deepEqual(m.current(), ['online', 'running']);
});

test('an event nobody handles is a quiet no-op, not an error', () => {
  const m = new Statechart(printerChart());
  m.send('power');
  m.send('job');
  m.send('jam'); // ['online', 'blocked']; blocked handles nothing
  assert.deepEqual(m.send('job'), {
    event: 'job',
    handled: false,
    by: null,
    internal: false,
    from: 'blocked',
    to: 'blocked',
    exited: [],
    entered: [],
  });
  assert.deepEqual(m.current(), ['online', 'blocked']);

  const fresh = new Statechart(printerChart());
  assert.equal(fresh.send('resume').handled, false, 'top-level miss is quiet too');
  assert.deepEqual(fresh.current(), ['offline']);
});

test('current() hands out a copy', () => {
  const m = new Statechart(printerChart());
  m.send('power');
  const snapshot = m.current();
  snapshot.push('junk');
  snapshot[0] = 'tampered';
  assert.deepEqual(m.current(), ['online', 'idle']);
});
