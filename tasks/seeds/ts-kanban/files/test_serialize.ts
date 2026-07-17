import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Board } from './board.ts';
import { serialize, deserialize } from './serialize.ts';

function busyBoard(): Board {
  const b = new Board({
    columns: [{ id: 'todo' }, { id: 'doing', wipLimit: 2 }, { id: 'done' }],
    lanes: ['standard', 'expedite'],
  });
  b.addCard({ id: 'c1', title: 'spec the api' });
  b.addCard({ id: 'c2', title: 'hotfix', column: 'doing', lane: 'expedite' });
  b.addCard({ id: 'c3', title: 'write docs' });
  b.move('c1', { column: 'doing' });
  b.move('c1', { column: 'done' });
  return b;
}

test('serialize produces a stable JSON string', () => {
  const b = busyBoard();
  const first = serialize(b);
  assert.equal(typeof first, 'string');
  JSON.parse(first); // must be valid JSON
  assert.equal(serialize(b), first);
});

test('serialize -> deserialize -> serialize is a fixed point', () => {
  const b = busyBoard();
  const text = serialize(b);
  assert.equal(serialize(deserialize(text)), text);
});

test('cards, positions and order survive the round trip', () => {
  const r = deserialize(serialize(busyBoard()));
  assert.deepEqual(r.find('c1'), { id: 'c1', title: 'spec the api', column: 'done', lane: 'standard' });
  assert.deepEqual(r.cards().map((c) => c.id), ['c3', 'c2', 'c1']);
  assert.equal(r.count('doing'), 1);
});

test('history survives the round trip', () => {
  const original = busyBoard();
  const restored = deserialize(serialize(original));
  assert.deepEqual(restored.history('c1'), original.history('c1'));
  assert.deepEqual(restored.history('c2'), original.history('c2'));
});

test('the restored board keeps enforcing wip limits', () => {
  const r = deserialize(serialize(busyBoard()));
  r.addCard({ id: 'c4', title: 'fill', column: 'doing' });
  assert.throws(() => r.move('c3', { column: 'doing' }), /doing/);
});

test('the event sequence continues where it left off', () => {
  const b = busyBoard(); // seqs 1..5 consumed
  const r = deserialize(serialize(b));
  r.move('c3', { column: 'doing' });
  const events = r.history('c3');
  assert.equal(events[events.length - 1].seq, 6);
});

test('config validation still applies after restore', () => {
  const r = deserialize(serialize(busyBoard()));
  assert.throws(() => r.addCard({ id: 'c1', title: 'dup' }), /c1/);
  assert.throws(() => r.addCard({ id: 'c9', title: 't', column: 'qa' }), /qa/);
});

test('garbage input is rejected as bad JSON', () => {
  assert.throws(() => deserialize('this is not json'), /JSON/);
});

test('structurally wrong documents are rejected', () => {
  assert.throws(() => deserialize('{}'));
  assert.throws(() => deserialize('{"columns": []}'));
  assert.throws(() => deserialize(JSON.stringify({ columns: [{ id: 'a' }], lanes: ['l'], seq: 0, cards: 'nope' })));
});

test('a card pointing at a column the board does not have is rejected', () => {
  const doc = JSON.parse(serialize(busyBoard()));
  doc.cards[0].column = 'vanished';
  assert.throws(() => deserialize(JSON.stringify(doc)), /vanished/);
});
