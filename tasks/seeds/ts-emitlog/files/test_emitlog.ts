// Acceptance tests for the linear workflow engine — protected file.
//
// The first block pins down the behavior the engine already has (linear
// set/call execution). The "observability feature" blocks cover the emit /
// output / status work: emitted events, the workflow-level output
// transform, and the completed/failed lifecycle around `raise:`.
//
// Run: node --test test_emitlog.ts

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { runWorkflow, LoadError } from './emitlog.ts';

const HDR = `document:
  dsl: "1.0"
  namespace: ops
  name: unit-flow
`;

type Args = Record<string, unknown>;

// ------------------------------------------------- existing engine behavior

test('load rejects wrong dsl versions and missing header fields', () => {
  assert.throws(() => runWorkflow(`document:
  dsl: "2.0"
  namespace: ops
  name: x
do:
  - a:
      set: { k: 1 }
`, {}), LoadError);
  assert.throws(() => runWorkflow(`document:
  dsl: "1.0"
  namespace: ops
do:
  - a:
      set: { k: 1 }
`, {}), LoadError);
});

test('load rejects duplicate task names and the reserved input name', () => {
  assert.throws(() => runWorkflow(HDR + `
do:
  - twice:
      set: { k: 1 }
  - twice:
      set: { k: 2 }
`, {}), LoadError);
  assert.throws(() => runWorkflow(HDR + `
do:
  - input:
      set: { k: 1 }
`, {}), LoadError);
});

test('load requires exactly one task type key', () => {
  assert.throws(() => runWorkflow(HDR + `
do:
  - both:
      set: { k: 1 }
      call: probe
`, {}), LoadError);
  assert.throws(() => runWorkflow(HDR + `
do:
  - neither: {}
`, {}), LoadError);
});

test('set results accumulate and single expressions keep their types', () => {
  const res = runWorkflow(HDR + `
do:
  - base:
      set: { region: "\${ .input.region }", limit: 5 }
  - more:
      set: { again: "\${ .base.limit }", items: "\${ .input.items }" }
`, { input: { region: 'us-east', items: [1, 2] } });
  assert.deepEqual(res.context, {
    input: { region: 'us-east', items: [1, 2] },
    base: { region: 'us-east', limit: 5 },
    more: { again: 5, items: [1, 2] },
  });
});

test('call gets evaluated args and stores the raw return value', () => {
  const calls: Args[] = [];
  const res = runWorkflow(HDR + `
do:
  - fetch:
      call: probe
      with: { where: "\${ .input.region }", n: 3 }
`, {
    input: { region: 'eu-west' },
    handlers: { probe: (args: Args) => { calls.push(args); return { rows: [7, 8] }; } },
  });
  assert.deepEqual(calls, [{ where: 'eu-west', n: 3 }]);
  assert.deepEqual(res.context.fetch, { rows: [7, 8] });
});

test('an unknown handler throws an error that names it', () => {
  assert.throws(() => runWorkflow(HDR + `
do:
  - fetch:
      call: nowhere
`, {}), /nowhere/);
});

test('handler exceptions propagate to the caller untouched', () => {
  assert.throws(() => runWorkflow(HDR + `
do:
  - fetch:
      call: probe
`, { handlers: { probe: () => { throw new Error('upstream said no'); } } }),
    /upstream said no/);
});

test('index paths drill into arrays', () => {
  const res = runWorkflow(HDR + `
do:
  - pick:
      set: { second: "\${ .input.items[1].sku }", beyond: "\${ .input.items[9] }" }
`, { input: { items: [{ sku: 'A' }, { sku: 'B' }] } });
  assert.deepEqual(res.context.pick, { second: 'B', beyond: undefined });
});

// --------------------------------------- observability feature: lifecycle

test('a clean run reports status completed', () => {
  const res = runWorkflow(HDR + `
do:
  - a:
      set: { ok: true }
`, {});
  assert.equal(res.status, 'completed');
});

// -------------------------------------------- observability feature: emit

test('emit appends type/source/data events in execution order', () => {
  const res = runWorkflow(HDR + `
do:
  - started:
      emit:
        event:
          type: run.started
          data: { region: "\${ .input.region }" }
  - work:
      set: { rows: 3 }
  - finished:
      emit:
        event:
          type: run.finished
          data: { rows: "\${ .work.rows }" }
`, { input: { region: 'us-east' } });
  assert.equal(res.status, 'completed');
  assert.deepEqual(res.events, [
    { type: 'run.started', source: 'ops/unit-flow', data: { region: 'us-east' } },
    { type: 'run.finished', source: 'ops/unit-flow', data: { rows: 3 } },
  ]);
});

test('emit stores nothing in the context and defaults data to {}', () => {
  const res = runWorkflow(HDR + `
do:
  - beat:
      emit:
        event:
          type: run.heartbeat
`, {});
  assert.deepEqual(res.events, [
    { type: 'run.heartbeat', source: 'ops/unit-flow', data: {} },
  ]);
  assert.equal('beat' in res.context, false);
});

test('a run with no emits still has an empty events log', () => {
  const res = runWorkflow(HDR + `
do:
  - a:
      set: { ok: true }
`, {});
  assert.deepEqual(res.events, []);
});

test('emit shape is validated at load time', () => {
  // event.type is required
  assert.throws(() => runWorkflow(HDR + `
do:
  - bad:
      emit:
        event:
          data: { k: 1 }
`, {}), LoadError);
  // unknown keys under event are rejected
  assert.throws(() => runWorkflow(HDR + `
do:
  - bad:
      emit:
        event:
          type: run.started
          severity: high
`, {}), LoadError);
});

// ------------------------------------------ observability feature: output

test('output.from reshapes the run output and leaves the context whole', () => {
  const res = runWorkflow(HDR + `
do:
  - fetch:
      set: { rows: 3, junk: true }
output:
  from:
    total: "\${ .fetch.rows }"
    where: "\${ .input.region }"
`, { input: { region: 'us-east' } });
  assert.deepEqual(res.output, { total: 3, where: 'us-east' });
  assert.deepEqual(res.context.fetch, { rows: 3, junk: true });
});

test('a single-expression output.from yields the raw value', () => {
  const res = runWorkflow(HDR + `
do:
  - fetch:
      set: { rows: [5, 6] }
output:
  from: "\${ .fetch.rows }"
`, {});
  assert.deepEqual(res.output, [5, 6]);
});

test('without an output declaration the output is the context', () => {
  const res = runWorkflow(HDR + `
do:
  - fetch:
      set: { rows: 3 }
`, { input: { seq: 1 } });
  assert.deepEqual(res.output, res.context);
  assert.deepEqual(res.output, { input: { seq: 1 }, fetch: { rows: 3 } });
});

test('output must be declared as output.from', () => {
  assert.throws(() => runWorkflow(HDR + `
do:
  - a:
      set: { ok: true }
output:
  total: "\${ .a.ok }"
`, {}), LoadError);
});

// ------------------------------------------- observability feature: raise

test('raise fails the run with the evaluated error object', () => {
  const res = runWorkflow(HDR + `
do:
  - fetch:
      set: { reason: "quota exhausted" }
  - stop:
      raise:
        error:
          type: quota.exceeded
          title: nightly sync aborted
          detail: "\${ .fetch.reason }"
  - never:
      set: { ran: true }
`, {});
  assert.equal(res.status, 'failed');
  assert.deepEqual(res.error, {
    type: 'quota.exceeded',
    title: 'nightly sync aborted',
    detail: 'quota exhausted',
  });
  assert.deepEqual(res.context.fetch, { reason: 'quota exhausted' });
  assert.equal('never' in res.context, false);
  assert.equal('stop' in res.context, false);
});

test('a failed run has null output even with an output declaration', () => {
  const res = runWorkflow(HDR + `
do:
  - stop:
      raise:
        error:
          type: halt.now
output:
  from: { done: true }
`, {});
  assert.equal(res.status, 'failed');
  assert.equal(res.output, null);
});

test('events emitted before a raise are kept', () => {
  const res = runWorkflow(HDR + `
do:
  - started:
      emit:
        event:
          type: run.started
  - stop:
      raise:
        error:
          type: halt.now
  - after:
      emit:
        event:
          type: run.finished
`, {});
  assert.equal(res.status, 'failed');
  assert.deepEqual(res.events, [
    { type: 'run.started', source: 'ops/unit-flow', data: {} },
  ]);
});

test('a completed run has no error field', () => {
  const res = runWorkflow(HDR + `
do:
  - a:
      set: { ok: true }
`, {});
  assert.equal(res.status, 'completed');
  assert.equal(res.error, undefined);
});

test('raise shape is validated at load time', () => {
  // error.type is required
  assert.throws(() => runWorkflow(HDR + `
do:
  - stop:
      raise:
        error:
          title: no type here
`, {}), LoadError);
  // unknown keys under error are rejected
  assert.throws(() => runWorkflow(HDR + `
do:
  - stop:
      raise:
        error:
          type: halt.now
          code: 500
`, {}), LoadError);
});
