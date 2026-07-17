import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Site } from './site.ts';
import { compareIds, idKey } from './ids.ts';

// Single-site behavior, op wire shapes, ordering, LWW visibility, undo.
// Cross-site convergence scripts live in test_converge.ts.

// Ops travel as JSON between sites; every exchange in the suite round-trips
// them to keep implementations honest about plain-data ops.
const ship = (ops: any) => JSON.parse(JSON.stringify(ops));

test('id ordering and key format', () => {
  assert.ok(compareIds({ clock: 1, site: 1 }, { clock: 1, site: 2 }) < 0);
  assert.ok(compareIds({ clock: 2, site: 1 }, { clock: 1, site: 9 }) > 0);
  assert.equal(compareIds({ clock: 3, site: 2 }, { clock: 3, site: 2 }), 0);
  assert.equal(idKey({ clock: 3, site: 2 }), '3@2');
});

test('site id must be a positive integer', () => {
  assert.throws(() => new Site(0), (e: unknown) => e instanceof RangeError && /site id/.test((e as Error).message));
  assert.throws(() => new Site(1.5), RangeError);
  assert.throws(() => new Site(-3), RangeError);
});

test('local typing: per-char ops with chained anchors', () => {
  const s = new Site(1);
  const ops = s.insert(0, 'hey');
  assert.equal(s.text(), 'hey');
  assert.equal(ops.length, 3);
  assert.deepEqual(ops[0], { kind: 'insert', id: { clock: 1, site: 1 }, after: null, char: 'h' });
  assert.deepEqual(ops[1], { kind: 'insert', id: { clock: 2, site: 1 }, after: { clock: 1, site: 1 }, char: 'e' });
  assert.deepEqual(ops[2], { kind: 'insert', id: { clock: 3, site: 1 }, after: { clock: 2, site: 1 }, char: 'y' });

  const bang = s.insert(3, '!');
  assert.deepEqual(bang[0], { kind: 'insert', id: { clock: 4, site: 1 }, after: { clock: 3, site: 1 }, char: '!' });
  assert.equal(s.text(), 'hey!');

  s.insert(1, 'X');
  assert.equal(s.text(), 'hXey!');
  const del = s.delete(1, 1);
  assert.deepEqual(del[0], { kind: 'delete', id: { clock: 6, site: 1 }, target: { clock: 5, site: 1 } });
  assert.equal(s.text(), 'hey!');

  // snapshot keeps the tombstone in document order
  assert.deepEqual(s.snapshot(), [
    { id: '1@1', char: 'h', visible: true },
    { id: '5@1', char: 'X', visible: false },
    { id: '2@1', char: 'e', visible: true },
    { id: '3@1', char: 'y', visible: true },
    { id: '4@1', char: '!', visible: true },
  ]);
  assert.equal(s.log().length, 6);
});

test('index validation and empty edits', () => {
  const s = new Site(5);
  assert.throws(() => s.insert(1, 'x'), (e: unknown) => e instanceof RangeError && /insert index out of range/.test((e as Error).message));
  assert.deepEqual(s.insert(0, ''), []);
  assert.equal(s.undo(), null); // an empty insert is not an undoable action
  s.insert(0, 'abcd');
  assert.throws(() => s.delete(2, 10), (e: unknown) => e instanceof RangeError && /delete range out of range/.test((e as Error).message));
  assert.throws(() => s.insert(9, 'x'), RangeError);
  assert.deepEqual(s.delete(1, 0), []);
  assert.equal(s.text(), 'abcd');
});

test('concurrent inserts at the same spot: higher id first, ties by site', () => {
  const a = new Site(1);
  const b = new Site(2);
  const opA = a.insert(0, 'a'); // (1@1)
  const opB = b.insert(0, 'b'); // (1@2)
  a.apply(ship(opB));
  b.apply(ship(opA));
  assert.equal(a.text(), 'ba'); // clock tie -> site 2 wins the spot next to ROOT
  assert.equal(b.text(), 'ba');
  assert.deepEqual(a.snapshot(), b.snapshot());
});

test('concurrent runs do not interleave', () => {
  const a = new Site(1);
  const b = new Site(2);
  const opsA = a.insert(0, 'aa');
  const opsB = b.insert(0, 'bb');
  a.apply(ship(opsB));
  b.apply(ship(opsA));
  assert.equal(a.text(), 'bbaa');
  assert.equal(b.text(), 'bbaa');

  // receiving remote ops advances the lamport clock past everything seen
  const bang = a.insert(4, '!');
  assert.deepEqual(bang[0].id, { clock: 3, site: 1 });
  assert.equal(a.text(), 'bbaa!');
});

test('visibility is last-writer-wins: undo of my delete beats an older remote delete', () => {
  const a = new Site(1);
  const b = new Site(2);
  b.apply(ship(a.insert(0, 'x'))); // both see "x"
  const delA = a.delete(0, 1);     // (2@1)
  const delB = b.delete(0, 1);     // (2@2)
  a.apply(ship(delB));
  b.apply(ship(delA));
  assert.equal(a.text(), '');
  assert.equal(b.text(), '');

  const restore = a.undo();        // restore op (3@1) — newest writer wins
  assert.ok(restore && restore.length === 1);
  assert.equal((restore as any)[0].kind, 'restore');
  assert.equal(a.text(), 'x');
  b.apply(ship(restore));
  assert.equal(b.text(), 'x');
  assert.deepEqual(a.snapshot(), b.snapshot());
});

test('undo walks back my own actions one call at a time', () => {
  const s = new Site(1);
  s.insert(0, 'ab');
  s.insert(2, 'cd');
  assert.equal(s.text(), 'abcd');

  const undo1 = s.undo(); // undoes insert "cd" -> two delete ops, original order
  assert.ok(undo1);
  assert.equal((undo1 as any).length, 2);
  assert.deepEqual((undo1 as any).map((o: any) => o.kind), ['delete', 'delete']);
  assert.deepEqual((undo1 as any)[0].target, { clock: 3, site: 1 });
  assert.deepEqual((undo1 as any)[1].target, { clock: 4, site: 1 });
  assert.equal(s.text(), 'ab');

  assert.ok(s.undo());
  assert.equal(s.text(), '');
  assert.equal(s.undo(), null); // nothing left to undo
  assert.equal(s.log().length, 8); // 4 inserts + 4 undo-emitted deletes
});

test('undo of a delete is a restore; undoing further removes the insert', () => {
  const s = new Site(1);
  s.insert(0, 'abc');
  s.delete(1, 1);
  assert.equal(s.text(), 'ac');
  assert.ok(s.undo());
  assert.equal(s.text(), 'abc');
  assert.ok(s.undo());
  assert.equal(s.text(), '');
  assert.equal(s.undo(), null); // undo-emitted ops are not themselves undoable
});

test('undo respects concurrent remote edits', () => {
  const a = new Site(1);
  const b = new Site(2);
  b.apply(ship(a.insert(0, 'ab')));
  const zOps = b.insert(1, 'Z');   // b: "aZb"
  const cOps = a.insert(2, 'c');   // a: "abc"
  a.apply(ship(zOps));
  b.apply(ship(cOps));
  assert.equal(a.text(), 'aZbc');
  assert.equal(b.text(), 'aZbc');

  const u1 = a.undo();             // a's latest action was inserting "c"
  b.apply(ship(u1));
  assert.equal(a.text(), 'aZb');
  assert.equal(b.text(), 'aZb');   // b's Z is untouched

  const u2 = a.undo();             // now the original "ab" goes
  b.apply(ship(u2));
  assert.equal(a.text(), 'Z');     // Z anchors to a tombstone and stays put
  assert.equal(b.text(), 'Z');
  assert.deepEqual(a.snapshot(), b.snapshot());
});

test('re-delivery and self-delivery are no-ops', () => {
  const a = new Site(1);
  const b = new Site(2);
  const ops = a.insert(0, 'hi');
  b.apply(ship(ops));
  b.apply(ship(ops)); // duplicate batch
  assert.equal(b.text(), 'hi');
  assert.equal(b.pending(), 0);
  a.apply(ship(a.log())); // my own ops bounced back
  assert.equal(a.text(), 'hi');
  assert.deepEqual(a.snapshot(), b.snapshot());
});
