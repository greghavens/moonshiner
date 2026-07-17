import { test } from 'node:test';
import assert from 'node:assert/strict';
import { UndoManager } from './undo.ts';

function makeDoc() {
  const state = { text: '' };
  const insert = (s: string) => ({
    label: `insert ${s}`,
    apply: () => {
      state.text += s;
    },
    revert: () => {
      state.text = state.text.slice(0, state.text.length - s.length);
    },
  });
  return { state, insert };
}

// --- coalescing ---

test('consecutive edits with one coalesceKey collapse into one undo step', () => {
  const { state, insert } = makeDoc();
  const m = new UndoManager();
  m.execute(insert('h'), { coalesceKey: 'typing' });
  m.execute(insert('e'), { coalesceKey: 'typing' });
  m.execute(insert('y'), { coalesceKey: 'typing' });
  assert.equal(state.text, 'hey');
  assert.deepEqual(m.historyLabels(), ['insert h']); // one entry, first label wins
  assert.equal(m.undo(), true);
  assert.equal(state.text, '');
  assert.equal(m.canUndo(), false);
});

test('a different key starts a new entry', () => {
  const { state, insert } = makeDoc();
  const m = new UndoManager();
  m.execute(insert('a'), { coalesceKey: 'field:name' });
  m.execute(insert('b'), { coalesceKey: 'field:email' });
  assert.deepEqual(m.historyLabels(), ['insert a', 'insert b']);
  m.undo();
  assert.equal(state.text, 'a');
});

test('edits without a key never merge, even back to back', () => {
  const { insert } = makeDoc();
  const m = new UndoManager();
  m.execute(insert('a'));
  m.execute(insert('b'));
  assert.deepEqual(m.historyLabels(), ['insert a', 'insert b']);
});

test('undo closes a coalescing run: same key afterwards is a new entry', () => {
  const { state, insert } = makeDoc();
  const m = new UndoManager();
  m.execute(insert('a'), { coalesceKey: 'typing' });
  m.undo();
  m.execute(insert('b'), { coalesceKey: 'typing' });
  m.execute(insert('c'), { coalesceKey: 'typing' });
  m.undo();
  assert.equal(state.text, ''); // only the b+c run came off, a was already undone
  assert.equal(m.canUndo(), false);
});

test('redo replays a coalesced entry as one step', () => {
  const { state, insert } = makeDoc();
  const m = new UndoManager();
  m.execute(insert('h'), { coalesceKey: 'typing' });
  m.execute(insert('i'), { coalesceKey: 'typing' });
  m.undo();
  assert.equal(state.text, '');
  assert.equal(m.redo(), true);
  assert.equal(state.text, 'hi');
});

// --- named checkpoints ---

test('revertTo unwinds every step back to the checkpoint and reports the count', () => {
  const { state, insert } = makeDoc();
  const m = new UndoManager();
  m.execute(insert('a'));
  m.checkpoint('before-import');
  m.execute(insert('b'));
  m.execute(insert('c'));
  assert.equal(m.revertTo('before-import'), 2);
  assert.equal(state.text, 'a');
  // the unwound steps are redoable
  assert.equal(m.redo(), true);
  assert.equal(m.redo(), true);
  assert.equal(state.text, 'abc');
});

test('revertTo at the checkpoint itself undoes nothing', () => {
  const { state, insert } = makeDoc();
  const m = new UndoManager();
  m.execute(insert('a'));
  m.checkpoint('cp');
  assert.equal(m.revertTo('cp'), 0);
  assert.equal(state.text, 'a');
});

test('an unknown checkpoint name throws', () => {
  const m = new UndoManager();
  assert.throws(() => m.revertTo('nope'), /unknown|no such/i);
});

test('a checkpoint you have undone past is unreachable', () => {
  const { insert } = makeDoc();
  const m = new UndoManager();
  m.execute(insert('a'));
  m.execute(insert('b'));
  m.checkpoint('cp');
  m.undo();
  m.undo();
  assert.throws(() => m.revertTo('cp'), /unreachable/i);
});

test('re-declaring a checkpoint name moves it to the current position', () => {
  const { state, insert } = makeDoc();
  const m = new UndoManager();
  m.execute(insert('a'));
  m.checkpoint('cp');
  m.execute(insert('b'));
  m.checkpoint('cp');
  m.execute(insert('c'));
  m.revertTo('cp');
  assert.equal(state.text, 'ab');
});

test('checkpoints() lists declared names', () => {
  const { insert } = makeDoc();
  const m = new UndoManager();
  m.execute(insert('a'));
  m.checkpoint('one');
  m.checkpoint('two');
  assert.deepEqual(m.checkpoints().sort(), ['one', 'two']);
});

test('a checkpoint interrupts coalescing: same key does not merge across it', () => {
  const { state, insert } = makeDoc();
  const m = new UndoManager();
  m.execute(insert('a'), { coalesceKey: 'typing' });
  m.checkpoint('mid');
  m.execute(insert('b'), { coalesceKey: 'typing' });
  assert.equal(m.revertTo('mid'), 1); // only 'b' comes off
  assert.equal(state.text, 'a');
});
