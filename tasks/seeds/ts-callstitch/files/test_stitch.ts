// Acceptance suite for stitch.ts -- RPC audit-log stitching.
//
// Pinned behavior:
// - a close consumes the MOST RECENT still-unconsumed open with its id
//   (same-id opens nest like a stack; retries rely on this)
// - calls come out in close order; unmatchedOpens in open (input) order;
//   orphanCloses in close (input) order
// - durationMs = closeTs - openTs; method/status carried through
// - onProbe(candidate.id) fires once per stored open entry examined while
//   resolving a close; the scale test budgets total probes at 3x entries.
//
// Run: node --test test_stitch.ts

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { stitch } from './stitch.ts';
import type { Call, Entry, StitchResult } from './stitch.ts';

function lcg(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s;
  };
}

const METHODS = ['GetUser', 'ListOrders', 'PutItem', 'Sync', 'Ping', 'Export'];
const STATUSES = ['ok', 'error', 'timeout', 'partial'];

// Deterministic stream generator that tracks the expected result as it
// emits entries, applying the most-recent-unconsumed-open rule itself
// (independently of the module).
function makeStream(n: number, seed: number): { entries: Entry[]; expected: StitchResult } {
  type Rec = { id: string; ts: number; method: string; consumed: boolean };
  const rng = lcg(seed);
  const entries: Entry[] = [];
  const openLog: Rec[] = []; // every open, in input order
  const byId = new Map<string, Rec[]>(); // unconsumed opens per id (top = newest)
  const calls: Call[] = [];
  const orphanCloses: string[] = [];
  let alive = 0;
  let head = 0; // index into openLog: everything before it is consumed
  let nextId = 1;
  let nextGhost = 1;
  let ts = 1000;

  const oldestAliveId = () => {
    while (openLog[head].consumed) head++;
    return openLog[head].id;
  };
  const newestAliveId = () => {
    let j = openLog.length - 1;
    while (openLog[j].consumed) j--;
    return openLog[j].id;
  };
  const consume = (id: string, status: string) => {
    const stack = byId.get(id)!;
    const rec = stack.pop()!;
    rec.consumed = true;
    alive--;
    entries.push({ kind: 'close', id, ts, status });
    calls.push({
      id, method: rec.method, openTs: rec.ts, closeTs: ts,
      durationMs: ts - rec.ts, status,
    });
  };

  for (let i = 0; i < n; i++) {
    ts += 1 + (rng() % 7);
    const r = rng() % 100;
    if (alive === 0 || r < 53) {
      // open -- occasionally re-opening an id that is still in flight
      const id = alive > 0 && r < 6 ? newestAliveId() : 'call-' + nextId++;
      const method = METHODS[rng() % METHODS.length];
      const rec: Rec = { id, ts, method, consumed: false };
      openLog.push(rec);
      let stack = byId.get(id);
      if (!stack) {
        stack = [];
        byId.set(id, stack);
      }
      stack.push(rec);
      alive++;
      entries.push({ kind: 'open', id, ts, method });
    } else if (r < 96) {
      // close: mostly the longest-running call, sometimes the newest
      const id = rng() % 100 < 70 ? oldestAliveId() : newestAliveId();
      consume(id, STATUSES[rng() % STATUSES.length]);
    } else {
      const id = 'ghost-' + nextGhost++;
      entries.push({ kind: 'close', id, ts, status: 'error' });
      orphanCloses.push(id);
    }
  }

  const unmatchedOpens = openLog.filter((o) => !o.consumed).map((o) => o.id);
  return { entries, expected: { calls, unmatchedOpens, orphanCloses } };
}

const callKey = (c: Call) =>
  `${c.id}|${c.method}|${c.openTs}|${c.closeTs}|${c.durationMs}|${c.status}`;

test('empty stream stitches to empty results', () => {
  assert.deepEqual(stitch([]), { calls: [], unmatchedOpens: [], orphanCloses: [] });
});

test('one call round-trips with its duration, method and status', () => {
  const got = stitch([
    { kind: 'open', id: 'call-1', ts: 100, method: 'GetUser' },
    { kind: 'close', id: 'call-1', ts: 130, status: 'ok' },
  ]);
  assert.deepEqual(got, {
    calls: [{ id: 'call-1', method: 'GetUser', openTs: 100, closeTs: 130, durationMs: 30, status: 'ok' }],
    unmatchedOpens: [],
    orphanCloses: [],
  });
});

test('calls come out in close order, not open order', () => {
  const got = stitch([
    { kind: 'open', id: 'a', ts: 1, method: 'Sync' },
    { kind: 'open', id: 'b', ts: 2, method: 'Ping' },
    { kind: 'close', id: 'b', ts: 5, status: 'ok' },
    { kind: 'close', id: 'a', ts: 9, status: 'error' },
  ]);
  assert.deepEqual(got.calls.map((c) => c.id), ['b', 'a']);
  assert.deepEqual(got.calls.map((c) => c.durationMs), [3, 8]);
});

test('same-id opens nest: a close takes the most recent unconsumed open', () => {
  const got = stitch([
    { kind: 'open', id: 'r', ts: 10, method: 'Export' },
    { kind: 'open', id: 'r', ts: 30, method: 'Export' },
    { kind: 'close', id: 'r', ts: 31, status: 'timeout' },
    { kind: 'close', id: 'r', ts: 70, status: 'ok' },
  ]);
  assert.deepEqual(got.calls.map((c) => [c.openTs, c.closeTs, c.durationMs, c.status]), [
    [30, 31, 1, 'timeout'],
    [10, 70, 60, 'ok'],
  ]);
});

test('orphan closes and leftover opens are reported in input order', () => {
  const got = stitch([
    { kind: 'close', id: 'nope', ts: 1, status: 'error' },
    { kind: 'open', id: 'x', ts: 2, method: 'Ping' },
    { kind: 'open', id: 'y', ts: 3, method: 'Sync' },
    { kind: 'close', id: 'x', ts: 4, status: 'ok' },
    { kind: 'close', id: 'x', ts: 5, status: 'ok' }, // already consumed
    { kind: 'open', id: 'z', ts: 6, method: 'Ping' },
  ]);
  assert.deepEqual(got.calls.map((c) => c.id), ['x']);
  assert.deepEqual(got.orphanCloses, ['nope', 'x']);
  assert.deepEqual(got.unmatchedOpens, ['y', 'z']);
});

test('onProbe is optional, gets candidate ids, and never changes the result', () => {
  const { entries } = makeStream(120, 7);
  const probes: string[] = [];
  const hooked = stitch(entries, { onProbe: (id) => probes.push(id) });
  const plain = stitch(entries);
  assert.deepEqual(hooked, plain, 'hook presence changed the result');
  assert.ok(probes.every((id) => typeof id === 'string'));
});

test('medium stream matches the rule oracle exactly', () => {
  const { entries, expected } = makeStream(600, 20260713);
  assert.deepEqual(stitch(entries), expected);
});

test('a full day of entries stitches within the probe budget', () => {
  // Scale gate arithmetic (perf-seed policy: document the margin):
  //   n = 160_000 entries (~69k matched calls). Probe budget: 3n =
  //   480_000. Resolving each close by rescanning the stored open log
  //   costs roughly the age of the call being closed; with this stream's
  //   mix (70% of closes take the longest-running call) that shape needs
  //   on the order of 7e8 probes ≈ 1_500x the budget, so the counter
  //   trips it within the first few thousand entries -- milliseconds --
  //   instead of letting it grind for minutes. An id-indexed resolver
  //   examines at most one stored open per close (~69k probes total,
  //   85% under budget) and the whole file finishes in a few seconds.
  const N = 160_000;
  const BUDGET = 3 * N;
  const { entries, expected } = makeStream(N, 424242);

  let probes = 0;
  const got = stitch(entries, {
    onProbe: () => {
      probes++;
      if (probes > BUDGET) {
        throw new Error(
          `probe budget exceeded: more than ${BUDGET} open-log examinations ` +
          `for ${N} entries -- close resolution is rescanning the log`);
      }
    },
  });

  assert.equal(got.calls.length, expected.calls.length);
  assert.ok(
    got.calls.map(callKey).join('\n') === expected.calls.map(callKey).join('\n'),
    'call records or their order diverged from the oracle at scale');
  assert.ok(
    got.unmatchedOpens.join('\n') === expected.unmatchedOpens.join('\n'),
    'unmatched opens diverged (content or order)');
  assert.ok(
    got.orphanCloses.join('\n') === expected.orphanCloses.join('\n'),
    'orphan closes diverged (content or order)');
  assert.deepEqual(got.calls[0], expected.calls[0]);
  assert.deepEqual(got.calls[got.calls.length - 1], expected.calls[expected.calls.length - 1]);
  assert.ok(probes <= BUDGET);
});
