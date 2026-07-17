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

test('execute applies, undo reverts, redo reapplies', () => {
  const { state, insert } = makeDoc();
  const m = new UndoManager();
  m.execute(insert('hello'));
  assert.equal(state.text, 'hello');
  assert.equal(m.undo(), true);
  assert.equal(state.text, '');
  assert.equal(m.redo(), true);
  assert.equal(state.text, 'hello');
});

test('undo pops in LIFO order', () => {
  const { state, insert } = makeDoc();
  const m = new UndoManager();
  m.execute(insert('a'));
  m.execute(insert('b'));
  m.execute(insert('c'));
  m.undo();
  assert.equal(state.text, 'ab');
  m.undo();
  assert.equal(state.text, 'a');
});

test('a fresh edit clears the redo stack', () => {
  const { state, insert } = makeDoc();
  const m = new UndoManager();
  m.execute(insert('a'));
  m.undo();
  assert.equal(m.canRedo(), true);
  m.execute(insert('b'));
  assert.equal(m.canRedo(), false);
  assert.equal(m.redo(), false);
  assert.equal(state.text, 'b');
});

test('undo and redo on empty stacks are safe no-ops', () => {
  const m = new UndoManager();
  assert.equal(m.undo(), false);
  assert.equal(m.redo(), false);
  assert.equal(m.canUndo(), false);
  assert.equal(m.canRedo(), false);
});

test('historyLabels lists undoable entries oldest first', () => {
  const { insert } = makeDoc();
  const m = new UndoManager();
  m.execute(insert('a'));
  m.execute(insert('b'));
  assert.deepEqual(m.historyLabels(), ['insert a', 'insert b']);
  m.undo();
  assert.deepEqual(m.historyLabels(), ['insert a']);
});
