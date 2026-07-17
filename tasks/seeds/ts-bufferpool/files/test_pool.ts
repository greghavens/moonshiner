import { test } from 'node:test';
import assert from 'node:assert/strict';
import { BufferPool, MemoryDisk, PageError, PoolFullError } from './pool.ts';

// Pool mechanics: pinning, dirty write-back, flush ordering, stats, misuse.
// The eviction-policy scripts live in test_lru2.ts.

test('MemoryDisk allocates sequential ids and hands out copies', () => {
  const disk = new MemoryDisk(8);
  assert.equal(disk.pageSize, 8);
  assert.equal(disk.allocate(), 1);
  assert.equal(disk.allocate(), 2);
  assert.equal(disk.allocate(), 3);

  const zero = disk.read(2);
  assert.deepEqual(Array.from(zero), [0, 0, 0, 0, 0, 0, 0, 0]);
  zero[0] = 99; // scribbling on a read result must not touch the disk
  assert.equal(disk.read(2)[0], 0);

  const buf = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8]);
  disk.write(2, buf);
  buf[1] = 99; // the disk stored a copy, not our buffer
  assert.deepEqual(Array.from(disk.read(2)), [1, 2, 3, 4, 5, 6, 7, 8]);

  assert.throws(() => disk.write(2, new Uint8Array(3)), /expects 8 bytes, got 3/);
  assert.throws(() => disk.read(99), (e: unknown) => e instanceof PageError && /unknown page 99/.test((e as Error).message));
  assert.throws(() => disk.write(99, new Uint8Array(8)), /unknown page 99/);

  // journal records reads and writes, in order (allocation is not I/O)
  assert.deepEqual(disk.journal, ['read 2', 'read 2', 'write 2', 'read 2']);
});

test('dirty pages are written back on eviction, clean ones are not', () => {
  const disk = new MemoryDisk(8);
  const pool = new BufferPool(disk, { capacity: 2, correlatedPeriod: 0 });

  const p1 = pool.newPage(); // tick 1
  p1.buffer.set([7, 7, 7]);
  pool.unpin(p1.id, true);

  const p2 = pool.newPage(); // tick 2
  pool.unpin(p2.id, false);
  assert.deepEqual(disk.journal, []); // nothing has touched the disk yet

  pool.newPage(); // tick 3: evicts p1 (dirty -> write-back)
  assert.equal(pool.contains(p1.id), false);
  assert.deepEqual(disk.journal, ['write 1']);

  // tick 4: reload p1 — evicts p2, which is clean, so NO write precedes
  // the read. The journal pins the exact I/O order.
  const again = pool.fetch(p1.id);
  assert.equal(pool.contains(p2.id), false);
  assert.deepEqual(disk.journal, ['write 1', 'read 1']);
  assert.deepEqual(Array.from(again), [7, 7, 7, 0, 0, 0, 0, 0]);
  pool.unpin(p1.id, false);
});

test('flushAll writes dirty pages in ascending page-id order and cleans them', () => {
  const disk = new MemoryDisk(4);
  const pool = new BufferPool(disk, { capacity: 4, correlatedPeriod: 0 });

  const a = pool.newPage(); // id 1, tick 1
  const b = pool.newPage(); // id 2, tick 2
  const c = pool.newPage(); // id 3, tick 3
  // dirty them in the order c, a, b — flush order must still be by id
  pool.unpin(c.id, true);
  pool.unpin(a.id, true);
  pool.unpin(b.id, true);

  pool.flushAll();
  assert.deepEqual(disk.journal, ['write 1', 'write 2', 'write 3']);
  assert.equal(pool.stats().dirty, 0);

  pool.flushAll(); // everything is clean: no I/O
  assert.deepEqual(disk.journal, ['write 1', 'write 2', 'write 3']);

  // flush(id) on a re-dirtied page writes it and clears the flag...
  const bb = pool.fetch(b.id); // tick 4 (hit)
  bb[0] = 9;
  pool.unpin(b.id, true);
  pool.flush(b.id);
  assert.deepEqual(disk.journal, ['write 1', 'write 2', 'write 3', 'write 2']);

  // ...so a later eviction of it must not write a second time.
  pool.newPage(); // id 4, tick 5, fills the pool
  pool.newPage(); // id 5, tick 6: evicts a (single-ref, oldest first touch)
  assert.equal(pool.contains(a.id), false);
  assert.deepEqual(disk.journal, ['write 1', 'write 2', 'write 3', 'write 2']);
});

test('stats reports exactly capacity/resident/pinned/dirty/hits/misses/evictions', () => {
  const disk = new MemoryDisk(4);
  const pool = new BufferPool(disk, { capacity: 3, correlatedPeriod: 0 });
  assert.deepEqual(pool.stats(), {
    capacity: 3, resident: 0, pinned: 0, dirty: 0,
    hits: 0, misses: 0, evictions: 0,
  });

  const p1 = pool.newPage(); // tick 1; newPage is neither a hit nor a miss
  assert.deepEqual(pool.stats(), {
    capacity: 3, resident: 1, pinned: 1, dirty: 0,
    hits: 0, misses: 0, evictions: 0,
  });

  pool.unpin(p1.id, true);
  pool.fetch(p1.id); // tick 2: hit
  assert.deepEqual(pool.stats(), {
    capacity: 3, resident: 1, pinned: 1, dirty: 1,
    hits: 1, misses: 0, evictions: 0,
  });
  pool.unpin(p1.id, false); // unpin(false) never cleans a dirty page

  const p2 = pool.newPage(); // tick 3
  pool.unpin(p2.id, false);
  const p3 = pool.newPage(); // tick 4
  pool.unpin(p3.id, false);
  const p4 = pool.newPage(); // tick 5: evicts p2 (p1 has 2 refs; p2 before p3)
  pool.unpin(p4.id, false);
  pool.fetch(p2.id); // tick 6: miss; evicts p3
  assert.deepEqual(pool.stats(), {
    capacity: 3, resident: 3, pinned: 1, dirty: 1,
    hits: 1, misses: 1, evictions: 2,
  });
  assert.equal(pool.contains(p3.id), false);
});

test('misuse is rejected with PageError', () => {
  const disk = new MemoryDisk(4);
  assert.throws(() => new BufferPool(disk, { capacity: 0 }), (e: unknown) =>
    e instanceof PageError && /capacity must be a positive integer/.test((e as Error).message));

  const pool = new BufferPool(disk, { capacity: 2 });
  assert.throws(() => pool.unpin(42, false), /unpin: page 42 is not resident/);
  assert.throws(() => pool.flush(42), /flush: page 42 is not resident/);

  const p = pool.newPage();
  pool.unpin(p.id, false);
  assert.throws(() => pool.unpin(p.id, false), /unpin: page 1 is not pinned/);

  // fetching a page the disk has never heard of fails without leaking a frame
  const before = pool.stats().resident;
  assert.throws(() => pool.fetch(99), /unknown page 99/);
  assert.equal(pool.contains(99), false);
  assert.equal(pool.stats().resident, before);
});

test('contains() is a pure observer: it never refreshes history', () => {
  const disk = new MemoryDisk(4);
  const pool = new BufferPool(disk, { capacity: 2, correlatedPeriod: 0 });
  const a = pool.newPage(); // tick 1
  pool.unpin(a.id, false);
  const b = pool.newPage(); // tick 2
  pool.unpin(b.id, false);
  for (let i = 0; i < 10; i++) assert.equal(pool.contains(a.id), true);
  pool.newPage(); // tick 3: victim must still be a — contains() is not an access
  assert.equal(pool.contains(a.id), false);
  assert.equal(pool.contains(b.id), true);
});
