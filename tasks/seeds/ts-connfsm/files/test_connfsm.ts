// Acceptance tests for the uplink connection state machine (connfsm.ts).
//
// The machine is pure and event-driven: the host feeds it events (app calls
// and transport notifications) and acts on the Effect[] each event returns.
// No sockets, no timers, no clocks in here — which is exactly why it is
// testable line by line.
//
// Run: node --test test_connfsm.ts

import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  ConnectionMachine,
  IllegalEventError,
  QueueFullError,
} from './connfsm.ts';

function machine(over: Partial<{ backoff: number[]; maxAttempts: number; maxQueue: number }> = {}) {
  return new ConnectionMachine({
    backoff: [100, 200, 400],
    maxAttempts: 4,
    maxQueue: 8,
    ...over,
  });
}

function illegal(fn: () => unknown): IllegalEventError {
  try {
    fn();
  } catch (err) {
    assert.ok(err instanceof IllegalEventError, `expected IllegalEventError, got ${err}`);
    return err;
  }
  throw new Error('expected IllegalEventError, nothing was thrown');
}

test('options are validated', () => {
  assert.throws(() => machine({ backoff: [] }), RangeError);
  assert.throws(() => machine({ backoff: [100, -5] }), RangeError);
  assert.throws(() => machine({ maxAttempts: 0 }), RangeError);
  assert.throws(() => machine({ maxQueue: 0 }), RangeError);
});

test('starts idle; connect dials attempt 1', () => {
  const m = machine();
  assert.equal(m.state, 'idle');
  assert.deepEqual(m.pending(), []);
  assert.deepEqual(m.connect(), [{ kind: 'dial', attempt: 1 }]);
  assert.equal(m.state, 'connecting');
});

test('up with nothing queued produces no effects', () => {
  const m = machine();
  m.connect();
  assert.deepEqual(m.up(), []);
  assert.equal(m.state, 'connected');
});

test('ops queued while connecting flush FIFO in one send on up', () => {
  const m = machine();
  m.connect();
  assert.deepEqual(m.enqueue('scan#1'), []);
  assert.deepEqual(m.enqueue('scan#2'), []);
  assert.deepEqual(m.enqueue('scan#3'), []);
  assert.deepEqual(m.pending(), ['scan#1', 'scan#2', 'scan#3']);
  assert.deepEqual(m.up(), [{ kind: 'send', ops: ['scan#1', 'scan#2', 'scan#3'] }]);
  assert.deepEqual(m.pending(), []);
  assert.equal(m.state, 'connected');
});

test('enqueue while connected sends immediately, nothing queues', () => {
  const m = machine();
  m.connect();
  m.up();
  assert.deepEqual(m.enqueue('scan#9'), [{ kind: 'send', ops: ['scan#9'] }]);
  assert.deepEqual(m.pending(), []);
  assert.equal(m.state, 'connected');
});

test('illegal events are rejected with the state\'s legal-event list', () => {
  const m = machine();

  let err = illegal(() => m.up());
  assert.equal(err.state, 'idle');
  assert.equal(err.event, 'up');
  assert.deepEqual(err.legal, ['close', 'connect']);
  assert.match(err.message, /up/);
  assert.match(err.message, /idle/);
  assert.equal(m.state, 'idle');

  m.connect();
  m.enqueue('held');
  err = illegal(() => m.drain());
  assert.equal(err.state, 'connecting');
  assert.deepEqual(err.legal, ['close', 'down', 'enqueue', 'up']);
  assert.equal(m.state, 'connecting', 'a rejected event changes nothing');
  assert.deepEqual(m.pending(), ['held'], 'a rejected event drops nothing');

  m.up();
  err = illegal(() => m.connect());
  assert.deepEqual(err.legal, ['close', 'down', 'drain', 'enqueue']);
  err = illegal(() => m.timer());
  assert.equal(err.state, 'connected');
});

test('failed dials walk the backoff schedule and give up at maxAttempts', () => {
  const m = machine(); // backoff [100, 200, 400], maxAttempts 4
  assert.deepEqual(m.connect(), [{ kind: 'dial', attempt: 1 }]);
  assert.deepEqual(m.down('refused'), [{ kind: 'wait', delayMs: 100 }]);
  assert.equal(m.state, 'reconnecting');
  assert.deepEqual(m.timer(), [{ kind: 'dial', attempt: 2 }]);
  assert.equal(m.state, 'connecting');
  assert.deepEqual(m.down('refused'), [{ kind: 'wait', delayMs: 200 }]);
  assert.deepEqual(m.timer(), [{ kind: 'dial', attempt: 3 }]);
  assert.deepEqual(m.down('refused'), [{ kind: 'wait', delayMs: 400 }]);
  assert.deepEqual(m.timer(), [{ kind: 'dial', attempt: 4 }]);
  assert.deepEqual(m.down('refused'), [], 'gave up: nothing queued, no effects');
  assert.equal(m.state, 'closed');
});

test('a schedule shorter than the failure run repeats its last delay', () => {
  const m = machine({ backoff: [50, 75], maxAttempts: 6 });
  m.connect();
  const waits: number[] = [];
  for (let i = 0; i < 5; i++) {
    const fx = m.down('unreachable');
    assert.equal(fx.length, 1);
    assert.equal(fx[0].kind, 'wait');
    waits.push((fx[0] as { kind: 'wait'; delayMs: number }).delayMs);
    m.timer();
  }
  assert.deepEqual(waits, [50, 75, 75, 75, 75]);
  assert.deepEqual(m.down('unreachable'), []); // 6th consecutive failure
  assert.equal(m.state, 'closed');
});

test('giving up with queued ops reports them in a discard effect', () => {
  const m = machine({ backoff: [10], maxAttempts: 1 });
  m.connect();
  m.enqueue('scan#1');
  m.enqueue('scan#2');
  assert.deepEqual(m.down('refused'), [{ kind: 'discard', ops: ['scan#1', 'scan#2'] }]);
  assert.equal(m.state, 'closed');
});

test('a successful connection resets the backoff schedule', () => {
  const m = machine();
  m.connect();
  m.down('refused'); // wait 100
  m.timer(); // dial attempt 2
  m.up(); // connected: run of failures is over
  assert.deepEqual(m.down('link lost'), [{ kind: 'wait', delayMs: 100 }],
    'an established connection dropping starts back at the first delay');
  assert.equal(m.state, 'reconnecting');
  assert.deepEqual(m.timer(), [{ kind: 'dial', attempt: 1 }]);
});

test('ops queued while reconnecting survive to the next connection', () => {
  const m = machine();
  m.connect();
  m.up();
  m.down('link lost');
  assert.deepEqual(m.enqueue('a'), []);
  assert.deepEqual(m.enqueue('b'), []);
  m.timer();
  assert.deepEqual(m.up(), [{ kind: 'send', ops: ['a', 'b'] }]);
});

test('the queue cap is enforced with a typed error', () => {
  const m = machine({ maxQueue: 2 });
  m.connect();
  m.enqueue('one');
  m.enqueue('two');
  assert.throws(() => m.enqueue('three'), (err: unknown) => {
    assert.ok(err instanceof QueueFullError);
    assert.equal(err.limit, 2);
    return true;
  });
  assert.deepEqual(m.pending(), ['one', 'two'], 'the overflowing op is not queued');
  assert.equal(m.state, 'connecting');
});

test('drain stops intake and closes once the transport confirms', () => {
  const m = machine();
  m.connect();
  m.up();
  assert.deepEqual(m.drain(), []);
  assert.equal(m.state, 'draining');
  const err = illegal(() => m.enqueue('late'));
  assert.equal(err.state, 'draining');
  assert.deepEqual(err.legal, ['close', 'down']);
  assert.deepEqual(m.down('closed by peer'), []);
  assert.equal(m.state, 'closed');
});

test('close discards whatever is still queued', () => {
  const m = machine();
  m.connect();
  m.up();
  m.down('link lost');
  m.enqueue('a');
  m.enqueue('b');
  assert.deepEqual(m.close(), [{ kind: 'discard', ops: ['a', 'b'] }]);
  assert.equal(m.state, 'closed');

  const fresh = machine();
  assert.deepEqual(fresh.close(), [], 'closing from idle has nothing to report');
  assert.equal(fresh.state, 'closed');
});

test('closed is terminal: every event, even close, is illegal', () => {
  const m = machine();
  m.close();
  for (const poke of [
    () => m.connect(), () => m.enqueue('x'), () => m.drain(),
    () => m.up(), () => m.down('r'), () => m.timer(), () => m.close(),
  ]) {
    const err = illegal(poke);
    assert.equal(err.state, 'closed');
    assert.deepEqual(err.legal, []);
  }
  assert.equal(m.state, 'closed');
});

test('pending() hands out a copy', () => {
  const m = machine();
  m.connect();
  m.enqueue('keep');
  const snapshot = m.pending();
  snapshot.push('injected');
  snapshot.length = 0;
  assert.deepEqual(m.pending(), ['keep']);
});
