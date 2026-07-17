// Acceptance tests for the control-task workflow engine — protected file.
//
// The module under test (forwait.ts, not written yet) executes the control
// subset of our workflow DSL: `for:` loops with nested do-blocks, `wait:`
// durations against an injected logical clock, plus just enough set/call to
// give loop bodies something to do. Everything is synchronous and
// deterministic — no timers, no real sleeping, no I/O.
//
// Run: node --test test_forwait.ts

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { runWorkflow, LoadError } from './forwait.ts';

const HDR = `document:
  dsl: "1.0"
  namespace: ops
  name: unit-flow
`;

type Args = Record<string, unknown>;

function makeClock() {
  const waits: number[] = [];
  return {
    t: 0,
    waits,
    now() { return this.t; },
    advance(ms: number) { this.t += ms; waits.push(ms); },
  };
}

function recorder() {
  const calls: Args[] = [];
  const handler = (args: Args) => {
    calls.push(args);
    return { n: calls.length };
  };
  return { calls, handler };
}

// ------------------------------------------------------------------ loading

test('load rejects a wrong dsl version and missing header fields', () => {
  assert.throws(() => runWorkflow(`document:
  dsl: "3.0"
  namespace: ops
  name: x
do:
  - a:
      set: { k: 1 }
`, {}), LoadError);
  assert.throws(() => runWorkflow(`document:
  dsl: "1.0"
  name: x
do:
  - a:
      set: { k: 1 }
`, {}), LoadError);
});

test('load requires for.each and for.in', () => {
  assert.throws(() => runWorkflow(HDR + `
do:
  - scan:
      for:
        in: "\${ .input.items }"
      do:
        - ping:
            set: { ok: true }
`, {}), LoadError);
  assert.throws(() => runWorkflow(HDR + `
do:
  - scan:
      for:
        each: item
      do:
        - ping:
            set: { ok: true }
`, {}), LoadError);
});

test('load requires a non-empty do block on for tasks', () => {
  assert.throws(() => runWorkflow(HDR + `
do:
  - scan:
      for:
        each: item
        in: "\${ .input.items }"
`, {}), LoadError);
  assert.throws(() => runWorkflow(HDR + `
do:
  - scan:
      for:
        each: item
        in: "\${ .input.items }"
      do: []
`, {}), LoadError);
});

test('load rejects then targets other than continue/end', () => {
  // jump targets belong to the sequencing layer, not this engine
  assert.throws(() => runWorkflow(HDR + `
do:
  - a:
      set: { k: 1 }
      then: b
  - b:
      set: { k: 2 }
`, {}), LoadError);
});

test('load rejects bad wait durations', () => {
  for (const bad of ['PT', '5m', 'PT1.5S', 'P T1S']) {
    assert.throws(() => runWorkflow(HDR + `
do:
  - pause:
      wait: "${bad}"
`, {}), LoadError, `expected LoadError for duration ${bad}`);
  }
});

test('load rejects duplicate task names within one do block', () => {
  assert.throws(() => runWorkflow(HDR + `
do:
  - twice:
      set: { k: 1 }
  - twice:
      set: { k: 2 }
`, {}), LoadError);
});

test('load reserves the input task name', () => {
  assert.throws(() => runWorkflow(HDR + `
do:
  - input:
      set: { k: 1 }
`, {}), LoadError);
});

// -------------------------------------------------------------------- for:

test('for iterates in order with the loop variable visible', () => {
  const { calls, handler } = recorder();
  const res = runWorkflow(HDR + `
do:
  - scan:
      for:
        each: region
        in: "\${ .input.regions }"
      do:
        - ping:
            call: probe
            with: { r: "\${ .region }" }
`, { input: { regions: ['us-east', 'eu-west', 'ap-south'] }, handlers: { probe: handler } });
  assert.deepEqual(calls, [{ r: 'us-east' }, { r: 'eu-west' }, { r: 'ap-south' }]);
  assert.equal(res.status, 'completed');
  assert.deepEqual(res.context.scan, [
    { ping: { n: 1 } },
    { ping: { n: 2 } },
    { ping: { n: 3 } },
  ]);
});

test('for exposes the index through at', () => {
  const { calls, handler } = recorder();
  runWorkflow(HDR + `
do:
  - scan:
      for:
        each: item
        in: "\${ .input.items }"
        at: idx
      do:
        - ping:
            call: probe
            with: { i: "\${ .idx }", v: "\${ .item }" }
`, { input: { items: ['a', 'b'] }, handlers: { probe: handler } });
  assert.deepEqual(calls, [{ i: 0, v: 'a' }, { i: 1, v: 'b' }]);
});

test('loop variables and inner results are not visible after the loop', () => {
  const res = runWorkflow(HDR + `
do:
  - scan:
      for:
        each: item
        in: "\${ .input.items }"
        at: idx
      do:
        - ping:
            set: { seen: "\${ .item }" }
`, { input: { items: ['a'] } });
  for (const leaked of ['item', 'idx', 'ping']) {
    assert.equal(leaked in res.context, false,
      `${leaked} leaked into the outer context`);
  }
  assert.deepEqual(res.context.scan, [{ ping: { seen: 'a' } }]);
});

test('inner results are visible to later tasks in the same iteration', () => {
  const res = runWorkflow(HDR + `
do:
  - scan:
      for:
        each: item
        in: "\${ .input.items }"
      do:
        - first:
            set: { doubled: "\${ .item }" }
        - second:
            set: { copy: "\${ .first.doubled }" }
`, { input: { items: ['x'] } });
  assert.deepEqual(res.context.scan, [
    { first: { doubled: 'x' }, second: { copy: 'x' } },
  ]);
});

test('the loop variable shadows an outer context key only inside the body', () => {
  const res = runWorkflow(HDR + `
do:
  - item:
      set: { fixed: true }
  - scan:
      for:
        each: item
        in: "\${ .input.items }"
      do:
        - probe:
            set: { got: "\${ .item }" }
  - after:
      set: { restored: "\${ .item.fixed }" }
`, { input: { items: ['loopval'] } });
  assert.deepEqual(res.context.scan, [{ probe: { got: 'loopval' } }]);
  assert.deepEqual(res.context.after, { restored: true });
});

test('an empty collection runs zero iterations and stores an empty array', () => {
  const { calls, handler } = recorder();
  const res = runWorkflow(HDR + `
do:
  - scan:
      for:
        each: item
        in: "\${ .input.items }"
      do:
        - ping:
            call: probe
`, { input: { items: [] }, handlers: { probe: handler } });
  assert.deepEqual(calls, []);
  assert.deepEqual(res.context.scan, []);
});

test('a non-array collection is a TypeError', () => {
  assert.throws(() => runWorkflow(HDR + `
do:
  - scan:
      for:
        each: item
        in: "\${ .input.notalist }"
      do:
        - ping:
            set: { ok: true }
`, { input: { notalist: 7 } }), TypeError);
});

test('nested loops see both loop variables and nest their records', () => {
  const { calls, handler } = recorder();
  const res = runWorkflow(HDR + `
do:
  - outer:
      for:
        each: row
        in: "\${ .input.rows }"
        at: ri
      do:
        - inner:
            for:
              each: col
              in: "\${ .input.cols }"
              at: ci
            do:
              - cell:
                  call: probe
                  with: { r: "\${ .row }", c: "\${ .col }", ri: "\${ .ri }", ci: "\${ .ci }" }
`, { input: { rows: ['r1', 'r2'], cols: ['c1', 'c2'] }, handlers: { probe: handler } });
  assert.deepEqual(calls, [
    { r: 'r1', c: 'c1', ri: 0, ci: 0 },
    { r: 'r1', c: 'c2', ri: 0, ci: 1 },
    { r: 'r2', c: 'c1', ri: 1, ci: 0 },
    { r: 'r2', c: 'c2', ri: 1, ci: 1 },
  ]);
  assert.deepEqual(res.context.outer, [
    { inner: [{ cell: { n: 1 } }, { cell: { n: 2 } }] },
    { inner: [{ cell: { n: 3 } }, { cell: { n: 4 } }] },
  ]);
});

test('tasks after a loop can read namespaced per-iteration results', () => {
  const { handler } = recorder();
  const res = runWorkflow(HDR + `
do:
  - scan:
      for:
        each: item
        in: "\${ .input.items }"
      do:
        - ping:
            call: probe
  - pick:
      set: { second: "\${ .scan[1].ping.n }" }
`, { input: { items: ['a', 'b'] }, handlers: { probe: handler } });
  assert.deepEqual(res.context.pick, { second: 2 });
});

// ------------------------------------------------------------------- wait:

test('wait advances the injected clock in order', () => {
  const clock = makeClock();
  const res = runWorkflow(HDR + `
do:
  - short:
      wait: PT2S
  - long:
      wait: PT1M30S
`, { clock });
  assert.deepEqual(clock.waits, [2000, 90000]);
  assert.equal(clock.now(), 92000);
  assert.equal('short' in res.context, false, 'wait tasks store no result');
});

test('composite durations with days parse to milliseconds', () => {
  const clock = makeClock();
  runWorkflow(HDR + `
do:
  - pause:
      wait: P1DT2H3M4S
`, { clock });
  assert.deepEqual(clock.waits, [86400000 + 7200000 + 180000 + 4000]);
});

test('wait inside a loop advances once per iteration', () => {
  const clock = makeClock();
  runWorkflow(HDR + `
do:
  - drain:
      for:
        each: batch
        in: "\${ .input.batches }"
      do:
        - pace:
            wait: PT2S
`, { input: { batches: [1, 2, 3] }, clock });
  assert.deepEqual(clock.waits, [2000, 2000, 2000]);
  assert.equal(clock.now(), 6000);
});

test('a workflow without waits runs without a clock', () => {
  const res = runWorkflow(HDR + `
do:
  - a:
      set: { ok: true }
`, {});
  assert.equal(res.status, 'completed');
});

test('executing a wait with no clock is an error that names the clock', () => {
  assert.throws(() => runWorkflow(HDR + `
do:
  - pause:
      wait: PT1S
`, {}), /clock/i);
});

// ------------------------------------------------------------------- then:

test('then end on a top-level task stops the workflow', () => {
  const res = runWorkflow(HDR + `
do:
  - first:
      set: { ran: true }
      then: end
  - second:
      set: { ran: true }
`, {});
  assert.equal(res.status, 'completed');
  assert.equal('second' in res.context, false);
});

test('then end inside a loop body ends the whole workflow', () => {
  const { calls, handler } = recorder();
  const res = runWorkflow(HDR + `
do:
  - scan:
      for:
        each: item
        in: "\${ .input.items }"
      do:
        - mark:
            call: probe
            with: { v: "\${ .item }" }
        - bail:
            set: { stopped: true }
            then: end
        - unreached:
            set: { ran: true }
  - after:
      set: { ran: true }
`, { input: { items: ['a', 'b', 'c'] }, handlers: { probe: handler } });
  assert.equal(res.status, 'completed');
  // only the first iteration started, and it stopped at `bail`
  assert.deepEqual(calls, [{ v: 'a' }]);
  assert.deepEqual(res.context.scan, [
    { mark: { n: 1 }, bail: { stopped: true } },
  ]);
  assert.equal('after' in res.context, false,
    'then: end must end the workflow, not just the iteration');
});

test('then end in a nested inner loop ends the outer loop too', () => {
  const { calls, handler } = recorder();
  const res = runWorkflow(HDR + `
do:
  - outer:
      for:
        each: row
        in: "\${ .input.rows }"
      do:
        - inner:
            for:
              each: col
              in: "\${ .input.cols }"
            do:
              - cell:
                  call: probe
                  with: { r: "\${ .row }", c: "\${ .col }" }
                  then: end
  - after:
      set: { ran: true }
`, { input: { rows: ['r1', 'r2'], cols: ['c1', 'c2'] }, handlers: { probe: handler } });
  assert.deepEqual(calls, [{ r: 'r1', c: 'c1' }]);
  assert.deepEqual(res.context.outer, [{ inner: [{ cell: { n: 1 } }] }]);
  assert.equal('after' in res.context, false);
});

// --------------------------------------------------------- set/call basics

test('set and call results accumulate under task names', () => {
  const res = runWorkflow(HDR + `
do:
  - base:
      set: { region: "\${ .input.region }", limit: 5 }
  - fetch:
      call: probe
      with: { where: "\${ .base.region }" }
`, {
    input: { region: 'us-east' },
    handlers: { probe: (args: Args) => ({ got: args.where }) },
  });
  assert.deepEqual(res.context, {
    input: { region: 'us-east' },
    base: { region: 'us-east', limit: 5 },
    fetch: { got: 'us-east' },
  });
});

test('an unknown handler is an error that names it', () => {
  assert.throws(() => runWorkflow(HDR + `
do:
  - fetch:
      call: nowhere
`, {}), /nowhere/);
});
