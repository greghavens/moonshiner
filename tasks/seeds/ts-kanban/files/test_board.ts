import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Board } from './board.ts';

const mk = () =>
  new Board({
    columns: [{ id: 'todo' }, { id: 'doing', wipLimit: 2 }, { id: 'done' }],
    lanes: ['standard', 'expedite'],
  });

test('a board needs at least one column', () => {
  assert.throws(() => new Board({ columns: [] }), /column/);
});

test('duplicate column ids and lanes are refused by name', () => {
  assert.throws(() => new Board({ columns: [{ id: 'a' }, { id: 'a' }] }), /a/);
  assert.throws(
    () => new Board({ columns: [{ id: 'a' }], lanes: ['x', 'x'] }),
    /x/,
  );
});

test('wip limits must be positive integers', () => {
  assert.throws(() => new Board({ columns: [{ id: 'a', wipLimit: 0 }] }), /a/);
  assert.throws(() => new Board({ columns: [{ id: 'a', wipLimit: 1.5 }] }), /a/);
});

test('lanes default to a single default lane', () => {
  const b = new Board({ columns: [{ id: 'todo' }] });
  b.addCard({ id: 'c1', title: 'first' });
  assert.equal(b.find('c1').lane, 'default');
});

test('new cards land in the first column and first lane', () => {
  const b = mk();
  b.addCard({ id: 'c1', title: 'write spec' });
  assert.deepEqual(b.find('c1'), { id: 'c1', title: 'write spec', column: 'todo', lane: 'standard' });
});

test('cards can be added to an explicit column and lane', () => {
  const b = mk();
  b.addCard({ id: 'c1', title: 'hotfix', column: 'doing', lane: 'expedite' });
  assert.equal(b.find('c1').column, 'doing');
  assert.equal(b.find('c1').lane, 'expedite');
});

test('find returns a copy, not a live handle', () => {
  const b = mk();
  b.addCard({ id: 'c1', title: 't' });
  const snap = b.find('c1');
  snap.column = 'done';
  assert.equal(b.find('c1').column, 'todo');
});

test('duplicate card ids, unknown columns and unknown lanes are named', () => {
  const b = mk();
  b.addCard({ id: 'c1', title: 't' });
  assert.throws(() => b.addCard({ id: 'c1', title: 'again' }), /c1/);
  assert.throws(() => b.addCard({ id: 'c2', title: 't', column: 'qa' }), /qa/);
  assert.throws(() => b.addCard({ id: 'c2', title: 't', lane: 'vip' }), /vip/);
});

test('adding beyond a wip limit is refused, counting across lanes', () => {
  const b = mk();
  b.addCard({ id: 'c1', title: 't', column: 'doing', lane: 'standard' });
  b.addCard({ id: 'c2', title: 't', column: 'doing', lane: 'expedite' });
  assert.throws(() => b.addCard({ id: 'c3', title: 't', column: 'doing' }), /doing/);
});

test('move changes column, lane, or both', () => {
  const b = mk();
  b.addCard({ id: 'c1', title: 't' });
  b.move('c1', { column: 'doing' });
  assert.equal(b.find('c1').column, 'doing');
  b.move('c1', { lane: 'expedite' });
  assert.deepEqual([b.find('c1').column, b.find('c1').lane], ['doing', 'expedite']);
  b.move('c1', { column: 'done', lane: 'standard' });
  assert.deepEqual([b.find('c1').column, b.find('c1').lane], ['done', 'standard']);
});

test('a move must actually move', () => {
  const b = mk();
  b.addCard({ id: 'c1', title: 't' });
  assert.throws(() => b.move('c1', {}), Error);
  assert.throws(() => b.move('c1', { column: 'todo', lane: 'standard' }), /already/);
});

test('moving into a full column is refused until space frees up', () => {
  const b = mk();
  b.addCard({ id: 'c1', title: 't', column: 'doing' });
  b.addCard({ id: 'c2', title: 't', column: 'doing' });
  b.addCard({ id: 'c3', title: 't' });
  assert.throws(() => b.move('c3', { column: 'doing' }), /doing/);
  b.move('c1', { column: 'done' });
  b.move('c3', { column: 'doing' });
  assert.equal(b.find('c3').column, 'doing');
});

test('a lane change inside a full column does not trip the wip limit', () => {
  const b = mk();
  b.addCard({ id: 'c1', title: 't', column: 'doing', lane: 'standard' });
  b.addCard({ id: 'c2', title: 't', column: 'doing', lane: 'standard' });
  b.move('c1', { lane: 'expedite' });
  assert.equal(b.find('c1').lane, 'expedite');
});

test('unknown cards are named in find, move and history', () => {
  const b = mk();
  assert.throws(() => b.find('ghost'), /ghost/);
  assert.throws(() => b.move('ghost', { column: 'done' }), /ghost/);
  assert.throws(() => b.history('ghost'), /ghost/);
});

test('count tracks column occupancy', () => {
  const b = mk();
  b.addCard({ id: 'c1', title: 't' });
  b.addCard({ id: 'c2', title: 't' });
  assert.equal(b.count('todo'), 2);
  b.move('c1', { column: 'done' });
  assert.equal(b.count('todo'), 1);
  assert.equal(b.count('done'), 1);
  assert.throws(() => b.count('qa'), /qa/);
});

test('cards in a column keep arrival order; moving away and back re-queues at the end', () => {
  const b = mk();
  b.addCard({ id: 'c1', title: 't' });
  b.addCard({ id: 'c2', title: 't' });
  b.addCard({ id: 'c3', title: 't' });
  assert.deepEqual(b.cards('todo').map((c) => c.id), ['c1', 'c2', 'c3']);
  b.move('c1', { column: 'done' });
  b.move('c1', { column: 'todo' });
  assert.deepEqual(b.cards('todo').map((c) => c.id), ['c2', 'c3', 'c1']);
});

test('cards() walks columns in board order and filters by lane', () => {
  const b = mk();
  b.addCard({ id: 'c1', title: 't', column: 'done' });
  b.addCard({ id: 'c2', title: 't', column: 'todo', lane: 'expedite' });
  b.addCard({ id: 'c3', title: 't', column: 'doing' });
  assert.deepEqual(b.cards().map((c) => c.id), ['c2', 'c3', 'c1']);
  assert.deepEqual(b.cards('todo', 'expedite').map((c) => c.id), ['c2']);
  assert.deepEqual(b.cards('todo', 'standard'), []);
  assert.throws(() => b.cards('qa'), /qa/);
  assert.throws(() => b.cards('todo', 'vip'), /vip/);
});

test('history records creation and every move with a global sequence', () => {
  const b = mk();
  b.addCard({ id: 'a', title: 't' });
  b.addCard({ id: 'b', title: 't' });
  b.move('a', { column: 'doing' });
  b.move('b', { column: 'doing' });
  b.move('a', { column: 'done', lane: 'expedite' });

  const ha = b.history('a');
  assert.equal(ha.length, 3);
  assert.deepEqual(ha[0], { seq: 1, event: 'created', to: { column: 'todo', lane: 'standard' } });
  assert.deepEqual(ha[1], {
    seq: 3,
    event: 'moved',
    from: { column: 'todo', lane: 'standard' },
    to: { column: 'doing', lane: 'standard' },
  });
  assert.deepEqual(ha[2], {
    seq: 5,
    event: 'moved',
    from: { column: 'doing', lane: 'standard' },
    to: { column: 'done', lane: 'expedite' },
  });
  assert.deepEqual(b.history('b').map((e) => e.seq), [2, 4]);
});

test('rejected moves leave no trace in history', () => {
  const b = mk();
  b.addCard({ id: 'c1', title: 't', column: 'doing' });
  b.addCard({ id: 'c2', title: 't', column: 'doing' });
  b.addCard({ id: 'c3', title: 't' });
  assert.throws(() => b.move('c3', { column: 'doing' }));
  assert.equal(b.history('c3').length, 1);
  b.move('c3', { column: 'done' });
  assert.equal(b.history('c3')[1].seq, 4);
});

test('history hands out copies', () => {
  const b = mk();
  b.addCard({ id: 'c1', title: 't' });
  const h = b.history('c1');
  h[0].to.column = 'done';
  (h as unknown[]).pop();
  assert.equal(b.history('c1').length, 1);
  assert.equal(b.history('c1')[0].to.column, 'todo');
});
