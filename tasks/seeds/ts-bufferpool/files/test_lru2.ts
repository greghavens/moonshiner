import { test } from 'node:test';
import assert from 'node:assert/strict';
import { BufferPool, MemoryDisk, PoolFullError } from './pool.ts';

/**
 * LRU-2 eviction policy — the exact semantics these scripts pin down.
 *
 * TIME. The pool keeps one global tick counter starting at 0. Every access
 * — newPage() or fetch(), hit or miss — first increments it, so the first
 * access happens at tick 1. Nothing else advances time; in particular
 * contains(), unpin(), flush(), flushAll() and stats() are not accesses.
 *
 * HISTORY. Each RESIDENT page tracks:
 *   last  — the tick of its most recent access (any access), and
 *   hist  — the ticks of its most recent DISTINCT references, newest
 *           first, at most two kept.
 * On an access at tick t:
 *   - if the page already has history and t - last <= correlatedPeriod,
 *     the access is CORRELATED: it extends the current reference burst
 *     (last = t) but adds nothing to hist. Bursts chain: each access only
 *     needs to be within the period of the PREVIOUS access, so a long
 *     burst may span far more than one period end to end.
 *   - otherwise it is a DISTINCT reference: t is pushed onto hist (only
 *     the two newest are kept) and last = t.
 * History is per-residency: evicting a page discards its history, and a
 * page loaded again later starts fresh.
 *
 * VICTIM. When a frame is needed and the pool is full, only unpinned
 * pages are candidates. Pages with fewer than two distinct references
 * (infinite backward 2-distance) are evicted before any page with two;
 * among those, the smallest hist[0] (oldest first distinct reference)
 * loses. Among pages with two distinct references, the smallest hist[1]
 * (oldest SECOND-most-recent distinct reference) loses. Ticks are unique,
 * so there are never ties. If every frame is pinned, the pool throws
 * PoolFullError and — for newPage() — must NOT have allocated a disk page
 * for the failed attempt. A failed access (PoolFullError, unknown page)
 * consumes no tick and records no history.
 */

// Convenience: one access = newPage or fetch; unpin immediately, clean.
function touch(pool: BufferPool, id: number): void {
  pool.fetch(id);
  pool.unpin(id, false);
}

function fresh(pool: BufferPool): number {
  const { id } = pool.newPage();
  pool.unpin(id, false);
  return id;
}

test('single-reference pages evict in order of first touch; a two-reference page outlives them all', () => {
  const pool = new BufferPool(new MemoryDisk(4), { capacity: 3, correlatedPeriod: 0 });
  const a = fresh(pool); // tick 1: hist a=[1]
  const b = fresh(pool); // tick 2: hist b=[2]
  const c = fresh(pool); // tick 3: hist c=[3]
  touch(pool, a);        // tick 4: hist a=[4,1] — a now has two references

  const d = fresh(pool); // tick 5: b and c have one ref each; b touched first
  assert.equal(pool.contains(b), false, 'b must be the first victim');
  assert.equal(pool.contains(a), true);
  assert.equal(pool.contains(c), true);

  const e = fresh(pool); // tick 6: victims c=[3] vs d=[5] -> c
  assert.equal(pool.contains(c), false, 'c must be the second victim');

  fresh(pool);           // tick 7: victims d=[5] vs e=[6] -> d
  assert.equal(pool.contains(d), false, 'd must be the third victim');

  fresh(pool);           // tick 8: victims e=[6] vs page from tick 7 -> e
  assert.equal(pool.contains(e), false, 'e must be the fourth victim');

  // Scan resistance: four single-touch pages streamed through, and the
  // twice-referenced page a never left the pool.
  assert.equal(pool.contains(a), true, 'a must survive the whole scan');
  assert.equal(pool.stats().evictions, 4);
});

test('the victim is chosen by second-most-recent reference, not by last use', () => {
  const pool = new BufferPool(new MemoryDisk(4), { capacity: 2, correlatedPeriod: 0 });
  const b = fresh(pool); // tick 1: b=[1]
  const a = fresh(pool); // tick 2: a=[2]
  touch(pool, a);        // tick 3: a=[3,2]
  touch(pool, b);        // tick 4: b=[4,1] — b is the most recently USED

  fresh(pool);           // tick 5: hist[1] — a: 2, b: 1 -> b loses
  assert.equal(pool.contains(b), false,
    'plain LRU would evict a (last used 3 < 4); LRU-2 must evict b (second ref 1 < 2)');
  assert.equal(pool.contains(a), true);
});

test('correlated accesses collapse into one reference; the same script with period 0 picks the opposite victim', () => {
  // Identical ten-access script, two pools differing only in the period.
  const run = (correlatedPeriod: number) => {
    const pool = new BufferPool(new MemoryDisk(4), { capacity: 2, correlatedPeriod });
    const m = fresh(pool);                    // tick 1
    const n = fresh(pool);                    // tick 2
    for (let i = 0; i < 6; i++) touch(pool, m); // ticks 3..8, every gap = 1
    touch(pool, n);                           // tick 9, gap since tick 2 = 7
    fresh(pool);                              // tick 10: forces one eviction
    return { pool, m, n };
  };

  // period 5: m's six fetches all land within 5 of its previous access, so
  // they are one burst — m keeps ONE distinct reference, hist=[1], and is
  // infinite-distance. n's fetch at tick 9 is 7 > 5 after tick 2: distinct,
  // n=[9,2]. Victim: m, despite seven raw accesses and last use at tick 8.
  const p5 = run(5);
  assert.equal(p5.pool.contains(p5.m), false, 'period 5: the bursty page m must be evicted');
  assert.equal(p5.pool.contains(p5.n), true);

  // period 0: every access is distinct (gaps are >= 1 > 0). m=[8,7],
  // n=[9,2]; hist[1] — m: 7, n: 2 -> n loses. Opposite victim.
  const p0 = run(0);
  assert.equal(p0.pool.contains(p0.m), true);
  assert.equal(p0.pool.contains(p0.n), false, 'period 0: n must be evicted instead');
});

test('a burst chains: each access within the period of the previous one keeps absorbing', () => {
  const pool = new BufferPool(new MemoryDisk(4), { capacity: 2, correlatedPeriod: 2 });
  const m = fresh(pool); // tick 1: m hist=[1], last=1
  const w = fresh(pool); // tick 2: w hist=[2]
  touch(pool, m);        // tick 3: 3-1=2 <= 2, correlated; last=3
  touch(pool, w);        // tick 4: 4-2=2 <= 2, correlated; w still [2]
  touch(pool, m);        // tick 5: 5-3=2, correlated — even though 5-1 > 2
  touch(pool, m);        // tick 6: correlated; m still hist=[1] after 4 accesses
  touch(pool, w);        // tick 7: 7-4=3 > 2, DISTINCT: w=[7,2]

  fresh(pool);           // tick 8: m has one distinct ref, w has two -> m loses
  assert.equal(pool.contains(m), false, 'the whole burst counts as one reference');
  assert.equal(pool.contains(w), true);
});

test('pinned pages are never victims; a fully pinned pool refuses without burning a page id', () => {
  const pool = new BufferPool(new MemoryDisk(4), { capacity: 2, correlatedPeriod: 0 });
  const a = pool.newPage(); // tick 1, stays pinned
  const b = pool.newPage(); // tick 2, stays pinned

  assert.throws(() => pool.newPage(), (e: unknown) =>
    e instanceof PoolFullError && /pool is full: all 2 frames are pinned/.test((e as Error).message));

  pool.unpin(b.id, false);
  const c = pool.newPage(); // tick 3: only b is unpinned -> b evicted, a kept
  assert.equal(c.id, 3, 'the failed newPage must not have consumed a page id');
  assert.equal(pool.contains(a.id), true);
  assert.equal(pool.contains(b.id), false);

  // a and c are both pinned again: fetching the evicted b must also refuse
  assert.throws(() => pool.fetch(b.id), PoolFullError);
  pool.unpin(c.id, false);
  pool.fetch(b.id); // now there is a victim (c) and the reload succeeds
  assert.equal(pool.contains(b.id), true);
  assert.equal(pool.contains(c.id), false);
});

test('a page pinned twice needs two unpins before it can be evicted', () => {
  const pool = new BufferPool(new MemoryDisk(4), { capacity: 2, correlatedPeriod: 0 });
  const a = pool.newPage();  // tick 1, pin count 1
  pool.fetch(a.id);          // tick 2, pin count 2
  pool.unpin(a.id, false);   // pin count 1: still not evictable
  const b = fresh(pool);     // tick 3

  const c = pool.newPage();  // tick 4: a is pinned, so b loses despite a's older history
  assert.equal(pool.contains(b), false);
  assert.equal(pool.contains(a.id), true);
  pool.unpin(c.id, false);

  pool.unpin(a.id, false);   // pin count 0: a is fair game now
  fresh(pool);               // tick 5: victims a=[2,1] vs c=[4] — c is infinite-distance
  assert.equal(pool.contains(c.id), false, 'single-reference c goes before twice-referenced a');

  fresh(pool);               // tick 6: victims a=[2,1] vs [5] -> the tick-5 page is infinite too
  assert.equal(pool.contains(a.id), true);

  // Only pages with two references left? Then hist[1] decides: a=[2,1] loses.
  touch(pool, 5);            // tick 7: page id 5 (the tick-6 newPage) -> [7,6]
  fresh(pool);               // tick 8: a hist[1]=1 vs 6 -> a finally goes
  assert.equal(pool.contains(a.id), false);
});
