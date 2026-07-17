import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Site } from './site.ts';

// Convergence scripts across three sites: out-of-order delivery buffering,
// full permutations of delivery order, idempotent re-delivery, and undo in
// flight. Sites exchange ops only through explicit apply() calls.

const ship = (ops: any) => JSON.parse(JSON.stringify(ops));

function permutations<T>(items: T[]): T[][] {
  if (items.length <= 1) return [items];
  const out: T[][] = [];
  for (let i = 0; i < items.length; i++) {
    const rest = items.slice(0, i).concat(items.slice(i + 1));
    for (const tail of permutations(rest)) out.push([items[i], ...tail]);
  }
  return out;
}

test('ops wait for their dependencies and cascade in', () => {
  const a = new Site(1);
  const [i1, i2] = a.insert(0, 'hi');
  const [d1, d2] = a.delete(0, 2);
  assert.equal(a.text(), '');

  const c = new Site(3);
  c.apply(ship([d1])); // delete of a char c has never seen
  assert.equal(c.pending(), 1);
  assert.equal(c.text(), '');
  c.apply(ship([d1])); // duplicate of a buffered op stays one op
  assert.equal(c.pending(), 1);

  c.apply(ship([i2])); // anchor still missing
  assert.equal(c.pending(), 2);
  assert.equal(c.text(), '');

  c.apply(ship([i1])); // everything cascades
  assert.equal(c.pending(), 0);
  assert.equal(c.text(), 'i');

  c.apply(ship([d2]));
  assert.equal(c.text(), '');
  assert.deepEqual(c.snapshot(), a.snapshot());
});

test('three sites converge under every delivery order', () => {
  const s1 = new Site(1);
  const opsA = s1.insert(0, 'ab');          // a:(1@1) b:(2@1)

  const s2 = new Site(2);
  s2.apply(ship(opsA));
  const opsB = [...s2.delete(0, 1), ...s2.insert(1, 'x')]; // hide a, add x after b
  assert.equal(s2.text(), 'bx');

  const s3 = new Site(3);
  s3.apply(ship(opsA));
  const opsC = s3.insert(2, 'z');           // z after b, older than x
  assert.equal(s3.text(), 'abz');

  // the sites themselves converge
  s1.apply(ship(opsB)); s1.apply(ship(opsC));
  s2.apply(ship(opsC));
  s3.apply(ship(opsB));
  assert.equal(s1.text(), 'bxz');
  assert.equal(s2.text(), 'bxz');
  assert.equal(s3.text(), 'bxz');
  assert.deepEqual(s2.snapshot(), s1.snapshot());
  assert.deepEqual(s3.snapshot(), s1.snapshot());

  // every permutation of single-op delivery lands on the same document
  const allOps = [...ship(opsA), ...ship(opsB), ...ship(opsC)];
  assert.equal(allOps.length, 5);
  for (const order of permutations(allOps)) {
    const observer = new Site(9);
    for (const op of order) observer.apply(ship([op]));
    assert.equal(observer.text(), 'bxz');
    assert.equal(observer.pending(), 0);
    assert.deepEqual(observer.snapshot(), s1.snapshot());
  }
});

test('undo travels like any other op and converges', () => {
  const a = new Site(1);
  const base = a.insert(0, 'abc');
  const b = new Site(2);
  const c = new Site(3);
  b.apply(ship(base));
  c.apply(ship(base));

  const delB = b.delete(0, 1);   // b hides 'a'
  const insC = c.insert(3, 'd'); // c appends 'd'
  const undoA = a.undo();        // a takes back the whole insert
  assert.ok(undoA);
  assert.equal((undoA as any).length, 3);
  assert.equal(a.text(), '');

  const batches = [ship(base), ship(delB), ship(insC), ship(undoA)];
  for (const order of permutations([0, 1, 2, 3])) {
    const observer = new Site(9);
    for (const idx of order) observer.apply(ship(batches[idx]));
    assert.equal(observer.text(), 'd');
    assert.equal(observer.pending(), 0);
  }

  a.apply(ship(delB)); a.apply(ship(insC));
  b.apply(ship(insC)); b.apply(ship(undoA));
  c.apply(ship(delB)); c.apply(ship(undoA));
  assert.equal(a.text(), 'd');
  assert.equal(b.text(), 'd');
  assert.equal(c.text(), 'd');
  assert.deepEqual(b.snapshot(), a.snapshot());
  assert.deepEqual(c.snapshot(), a.snapshot());

  // tombstone state is part of convergence: a, b, c all hidden, d visible
  const visible = a.snapshot().filter((e: any) => e.visible).map((e: any) => e.char);
  assert.deepEqual(visible, ['d']);
  assert.equal(a.snapshot().length, 4);
});

test('re-delivering every batch twice changes nothing', () => {
  const a = new Site(1);
  const b = new Site(2);
  const one = a.insert(0, 'one ');
  const two = b.insert(0, 'two ');
  const sites = [a, b];
  for (const s of sites) {
    for (const batch of [one, two, one, two]) s.apply(ship(batch));
  }
  assert.equal(a.text(), b.text());
  assert.deepEqual(a.snapshot(), b.snapshot());
  assert.equal(a.pending(), 0);
  assert.equal(b.pending(), 0);

  // and a third site fed everything twice in reverse batch order agrees
  const c = new Site(3);
  for (const batch of [two, one, two, one]) c.apply(ship(batch));
  assert.equal(c.text(), a.text());
  assert.deepEqual(c.snapshot(), a.snapshot());
});
