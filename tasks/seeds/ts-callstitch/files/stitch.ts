// RPC audit-log stitching.
//
// The gateway writes one audit entry when a call opens and one when it
// closes, identified by the call id. This module folds a whole day's
// entry stream back into call records for the latency dashboards.
//
// Matching rule: a close consumes the MOST RECENT still-unconsumed open
// with the same id (retries re-open the same id, so same-id opens nest
// like a stack). A close that finds no unconsumed open is an orphan.
//
// Output shape (order is part of the contract):
//   calls          one record per matched open/close, in CLOSE order:
//                  { id, method, openTs, closeTs, durationMs, status }
//                  with durationMs = closeTs - openTs
//   unmatchedOpens ids of opens never consumed, in OPEN (input) order
//   orphanCloses   ids of orphan closes, in close (input) order
//
// Probe accounting: the perf suite injects onProbe through StitchOpts and
// budgets it. The implementation must call onProbe(candidate.id) once for
// every stored open entry it examines while resolving a close.

export type OpenEntry = { kind: 'open'; id: string; ts: number; method: string };
export type CloseEntry = { kind: 'close'; id: string; ts: number; status: string };
export type Entry = OpenEntry | CloseEntry;

export type Call = {
  id: string;
  method: string;
  openTs: number;
  closeTs: number;
  durationMs: number;
  status: string;
};

export type StitchResult = {
  calls: Call[];
  unmatchedOpens: string[];
  orphanCloses: string[];
};

export type StitchOpts = { onProbe?: (id: string) => void };

type OpenRec = { id: string; ts: number; method: string; used: boolean };

export function stitch(entries: Entry[], opts: StitchOpts = {}): StitchResult {
  const probe = opts.onProbe ?? (() => {});
  const opens: OpenRec[] = [];
  const calls: Call[] = [];
  const orphanCloses: string[] = [];

  for (const e of entries) {
    if (e.kind === 'open') {
      opens.push({ id: e.id, ts: e.ts, method: e.method, used: false });
      continue;
    }
    // Most recent unconsumed open with this id: walk back from the end.
    let hit = -1;
    for (let j = opens.length - 1; j >= 0; j--) {
      probe(opens[j].id);
      if (!opens[j].used && opens[j].id === e.id) {
        hit = j;
        break;
      }
    }
    if (hit === -1) {
      orphanCloses.push(e.id);
      continue;
    }
    const o = opens[hit];
    o.used = true;
    calls.push({
      id: e.id,
      method: o.method,
      openTs: o.ts,
      closeTs: e.ts,
      durationMs: e.ts - o.ts,
      status: e.status,
    });
  }

  const unmatchedOpens = opens.filter((o) => !o.used).map((o) => o.id);
  return { calls, unmatchedOpens, orphanCloses };
}
