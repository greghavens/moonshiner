// Acceptance tests for the Server-Sent-Events consumer.
//
// The parser works at the TEXT LINE level — it receives plain string segments
// (potentially splitting across lines or joining multiple lines), parses the
// SSE field protocol, and dispatches events. An injectable transport delivers
// the text segments; reconnects are scripted. Nothing here uses raw Buffers,
// real sockets, or real timers.
//
// Run: node --test test_ssefeed.ts

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SseFeed, SseEvent } from './ssefeed.ts';

// ------------------------------------------------------------------ helpers

/** A scripted async transport.
 *
 * segments: arrays of text string arrays; each outer array is one
 * "connection attempt" (call to the transport function). The inner array
 * is the sequence of text chunks delivered for that connection.
 * An empty inner array simulates a connection that immediately closes.
 */
function makeTransport(sessions: string[][]): {
  fn: (lastEventId: string | null) => AsyncGenerator<string>;
  calls: Array<string | null>;  // recorded lastEventId per call
} {
  const calls: Array<string | null> = [];
  let pos = 0;
  const fn = async function* (lastEventId: string | null): AsyncGenerator<string> {
    calls.push(lastEventId);
    const session = sessions[pos++] ?? [];
    for (const chunk of session) {
      yield chunk;
    }
  };
  return { fn, calls };
}

// ------------------------------------------------------------------ tests

test('single complete event with event, data, id fields', async () => {
  const { fn } = makeTransport([[
    'event: dashboard.update\n',
    'data: {"metric":"cpu","value":42}\n',
    'id: evt-1\n',
    '\n',
  ]]);
  const feed = new SseFeed(fn);
  const events: SseEvent[] = [];
  for await (const ev of feed.events()) {
    events.push(ev);
    break; // take exactly one
  }
  assert.equal(events.length, 1);
  assert.equal(events[0].type, 'dashboard.update');
  assert.equal(events[0].data, '{"metric":"cpu","value":42}');
  assert.equal(events[0].id, 'evt-1');
});

test('default event type is "message" when no event field', async () => {
  const { fn } = makeTransport([[
    'data: hello\n',
    '\n',
  ]]);
  const feed = new SseFeed(fn);
  const events: SseEvent[] = [];
  for await (const ev of feed.events()) {
    events.push(ev);
    break;
  }
  assert.equal(events[0].type, 'message');
  assert.equal(events[0].data, 'hello');
});

test('multi-line data fields are joined with \\n', async () => {
  const { fn } = makeTransport([[
    'data: line one\n',
    'data: line two\n',
    'data: line three\n',
    '\n',
  ]]);
  const feed = new SseFeed(fn);
  const events: SseEvent[] = [];
  for await (const ev of feed.events()) {
    events.push(ev);
    break;
  }
  assert.equal(events[0].data, 'line one\nline two\nline three');
});

test('comment lines (colon-prefixed with no field name) are ignored', async () => {
  const { fn } = makeTransport([[
    ': keep-alive ping\n',
    'data: real event\n',
    '\n',
  ]]);
  const feed = new SseFeed(fn);
  const events: SseEvent[] = [];
  for await (const ev of feed.events()) {
    events.push(ev);
    break;
  }
  assert.equal(events.length, 1);
  assert.equal(events[0].data, 'real event');
});

test('retry field sets the reconnect interval (does not dispatch an event)', async () => {
  const { fn } = makeTransport([[
    'retry: 5000\n',
    'data: payload\n',
    '\n',
  ]]);
  const feed = new SseFeed(fn);
  const events: SseEvent[] = [];
  for await (const ev of feed.events()) {
    events.push(ev);
    break;
  }
  assert.equal(events.length, 1, 'retry line must not produce an event');
  assert.equal(events[0].data, 'payload');
  assert.equal(feed.retryMs, 5000, 'retry field must update the retryMs property');
});

test('chunks may split across line boundaries', async () => {
  // Deliver the same event as split-up byte chunks
  const { fn } = makeTransport([[
    'event: upd',
    'ate\ndata: hello',
    '\n\n',
  ]]);
  const feed = new SseFeed(fn);
  const events: SseEvent[] = [];
  for await (const ev of feed.events()) {
    events.push(ev);
    break;
  }
  assert.equal(events[0].type, 'update');
  assert.equal(events[0].data, 'hello');
});

test('multiple events in one session are dispatched in order', async () => {
  const { fn } = makeTransport([[
    'data: first\n\ndata: second\n\ndata: third\n\n',
  ]]);
  const feed = new SseFeed(fn);
  const events: SseEvent[] = [];
  for await (const ev of feed.events()) {
    events.push(ev);
    if (events.length === 3) break;
  }
  assert.deepEqual(events.map((e) => e.data), ['first', 'second', 'third']);
});

test('blank data field value is preserved and dispatches an event', async () => {
  const { fn } = makeTransport([[
    'data:\n',
    '\n',
  ]]);
  const feed = new SseFeed(fn);
  const events: SseEvent[] = [];
  for await (const ev of feed.events()) {
    events.push(ev);
    break;
  }
  assert.equal(events.length, 1, 'a data: line with empty value still dispatches');
  assert.equal(events[0].data, '');
});

test('event with no data field is NOT dispatched', async () => {
  const { fn } = makeTransport([[
    'event: heartbeat\n',
    'id: hb-1\n',
    '\n',  // blank line with no data → no dispatch
    'data: real\n',
    '\n',
  ]]);
  const feed = new SseFeed(fn);
  const events: SseEvent[] = [];
  for await (const ev of feed.events()) {
    events.push(ev);
    break;
  }
  assert.equal(events.length, 1);
  assert.equal(events[0].data, 'real');
});

test('Last-Event-ID is sent on reconnect and advances with each event id', async () => {
  const { fn, calls } = makeTransport([
    // Session 1: one event with id, then stream ends
    ['data: msg-1\nid: e-100\n\n'],
    // Session 2: reconnect (should carry last id); one more event
    ['data: msg-2\nid: e-101\n\n'],
  ]);
  const feed = new SseFeed(fn);
  const events: SseEvent[] = [];
  for await (const ev of feed.events()) {
    events.push(ev);
    if (events.length === 2) break;
  }
  assert.equal(events.length, 2);
  assert.equal(calls[0], null, 'first connection has no Last-Event-ID');
  assert.equal(calls[1], 'e-100', 'reconnect must send the last received event id');
});

test('reconnect uses the most recent id even if set by an intermediate event', async () => {
  const { fn, calls } = makeTransport([
    [
      'data: a\nid: id-1\n\n',
      'data: b\nid: id-2\n\n',
    ],
    // reconnect should carry id-2
    ['data: c\n\n'],
  ]);
  const feed = new SseFeed(fn);
  const events: SseEvent[] = [];
  for await (const ev of feed.events()) {
    events.push(ev);
    if (events.length === 3) break;
  }
  assert.equal(calls[1], 'id-2', 'reconnect must use the id of the LAST event received');
});

test('a field value with a leading space has that space stripped', async () => {
  // SSE spec: if field value starts with a single space, it is stripped
  const { fn } = makeTransport([[
    'data: has space stripped\n',
    '\n',
  ]]);
  const feed = new SseFeed(fn);
  const events: SseEvent[] = [];
  for await (const ev of feed.events()) {
    events.push(ev);
    break;
  }
  assert.equal(events[0].data, 'has space stripped');
});

test('a line with no colon is treated as a field name with empty value', async () => {
  // SSE spec: a line with no colon uses the whole line as field name, empty value
  // "data" field with empty value = dispatch with data=''
  const { fn } = makeTransport([[
    'data\n',
    '\n',
  ]]);
  const feed = new SseFeed(fn);
  const events: SseEvent[] = [];
  for await (const ev of feed.events()) {
    events.push(ev);
    break;
  }
  assert.equal(events.length, 1);
  assert.equal(events[0].data, '');
});
