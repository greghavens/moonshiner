import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { FeedClient } from './feed_client.ts';
import type { Envelope, FeedSink } from './feed_client.ts';

const warnings: Error[] = [];
process.on('warning', (w) => {
  warnings.push(w);
});

class RecordingSink implements FeedSink {
  envelopes: Envelope[] = [];
  faults: string[] = [];
  closedCalls = 0;

  onEnvelope(env: Envelope): void {
    this.envelopes.push(env);
  }

  onFault(message: string): void {
    this.faults.push(message);
  }

  onClosed(): void {
    this.closedCalls += 1;
  }
}

function counts(socket: EventEmitter): Record<string, number> {
  return {
    envelope: socket.listenerCount('envelope'),
    fault: socket.listenerCount('fault'),
    closed: socket.listenerCount('closed'),
  };
}

async function macrotasks(rounds = 3): Promise<void> {
  for (let i = 0; i < rounds; i++) await new Promise((r) => setImmediate(r));
}

test('a connected client delivers envelopes and faults exactly once', () => {
  const socket = new EventEmitter();
  const sink = new RecordingSink();
  const client = new FeedClient(socket, sink);
  client.connect();
  socket.emit('envelope', { seq: 1, body: 'bid 101.2' });
  socket.emit('fault', new Error('heartbeat late'));
  assert.deepEqual(sink.envelopes, [{ seq: 1, body: 'bid 101.2' }]);
  assert.deepEqual(sink.faults, ['heartbeat late']);
});

test('disconnect detaches the client but leaves foreign listeners alone', () => {
  const socket = new EventEmitter();
  const foreign: Envelope[] = [];
  socket.on('envelope', (env: Envelope) => foreign.push(env));
  const baseline = counts(socket);

  const sink = new RecordingSink();
  const client = new FeedClient(socket, sink);
  client.connect();
  assert.deepEqual(counts(socket), {
    envelope: baseline.envelope + 1,
    fault: baseline.fault + 1,
    closed: baseline.closed + 1,
  });

  client.disconnect();
  assert.deepEqual(counts(socket), baseline, 'listener counts did not return to baseline');
  socket.emit('envelope', { seq: 2, body: 'ask 101.4' });
  assert.deepEqual(sink.envelopes, [], 'a detached client still received an envelope');
  assert.deepEqual(foreign, [{ seq: 2, body: 'ask 101.4' }]);
});

test('calling connect twice does not double subscriptions', () => {
  const socket = new EventEmitter();
  const sink = new RecordingSink();
  const client = new FeedClient(socket, sink);
  client.connect();
  client.connect();
  assert.deepEqual(counts(socket), { envelope: 1, fault: 1, closed: 1 });
  socket.emit('envelope', { seq: 3, body: 'trade 101.3' });
  assert.equal(sink.envelopes.length, 1, 'one emit produced duplicate deliveries');
});

test('twelve reconnect cycles leave exactly one live subscription', () => {
  const socket = new EventEmitter();
  const sink = new RecordingSink();
  const client = new FeedClient(socket, sink);
  for (let i = 0; i < 12; i++) {
    client.connect();
    client.disconnect();
  }
  client.connect();
  assert.deepEqual(counts(socket), { envelope: 1, fault: 1, closed: 1 });
  socket.emit('envelope', { seq: 4, body: 'bid 100.9' });
  assert.equal(sink.envelopes.length, 1, 'reconnect churn multiplied deliveries');
});

test('the closed handler is terminal: one callback, then full detach', () => {
  const socket = new EventEmitter();
  const sink = new RecordingSink();
  const client = new FeedClient(socket, sink);
  client.connect();
  socket.emit('closed');
  socket.emit('closed');
  assert.equal(sink.closedCalls, 1, 'closed fired more than once for one connection');
  assert.equal(client.isConnected, false);
  socket.emit('envelope', { seq: 5, body: 'stale frame' });
  assert.deepEqual(sink.envelopes, [], 'a closed client still received an envelope');
  assert.deepEqual(counts(socket), { envelope: 0, fault: 0, closed: 0 });
});

test('disconnect is idempotent and reconnect after it works', () => {
  const socket = new EventEmitter();
  const sink = new RecordingSink();
  const client = new FeedClient(socket, sink);
  client.connect();
  client.disconnect();
  client.disconnect();
  assert.deepEqual(counts(socket), { envelope: 0, fault: 0, closed: 0 });
  client.connect();
  socket.emit('envelope', { seq: 6, body: 'ask 101.0' });
  assert.deepEqual(sink.envelopes, [{ seq: 6, body: 'ask 101.0' }]);
});

test('reconnecting after a close resumes deliveries exactly once', () => {
  const socket = new EventEmitter();
  const sink = new RecordingSink();
  const client = new FeedClient(socket, sink);
  client.connect();
  socket.emit('closed');
  client.connect();
  assert.equal(client.isConnected, true);
  socket.emit('envelope', { seq: 7, body: 'bid 101.1' });
  assert.equal(sink.envelopes.length, 1);
  socket.emit('closed');
  assert.equal(sink.closedCalls, 2, 'each connection should report its own close');
});

test('no max-listener warning is emitted by any of the above', async () => {
  await macrotasks();
  const maxListener = warnings.filter((w) => w.name === 'MaxListenersExceededWarning');
  assert.deepEqual(maxListener, []);
});
