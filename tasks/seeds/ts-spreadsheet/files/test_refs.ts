import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseRef, formatRef, expandRange } from './refs.ts';

test('single letter columns', () => {
  assert.deepEqual(parseRef('A1'), { col: 1, row: 1 });
  assert.deepEqual(parseRef('C7'), { col: 3, row: 7 });
  assert.deepEqual(parseRef('Z99'), { col: 26, row: 99 });
});

test('multi letter columns are base 26 with A=1', () => {
  assert.deepEqual(parseRef('AA10'), { col: 27, row: 10 });
  assert.deepEqual(parseRef('AB3'), { col: 28, row: 3 });
  assert.deepEqual(parseRef('BA1'), { col: 53, row: 1 });
});

test('parseRef is case-insensitive', () => {
  assert.deepEqual(parseRef('aa10'), { col: 27, row: 10 });
  assert.deepEqual(parseRef('c2'), { col: 3, row: 2 });
});

test('malformed refs throw SyntaxError naming the input', () => {
  for (const bad of ['A0', '1A', 'A', '', '12', 'A1B', 'A-1']) {
    assert.throws(() => parseRef(bad), (e: unknown) => {
      assert.ok(e instanceof SyntaxError, `expected SyntaxError for ${JSON.stringify(bad)}`);
      assert.ok((e as Error).message.includes(bad), `message should name ${JSON.stringify(bad)}`);
      return true;
    });
  }
});

test('formatRef round-trips parseRef', () => {
  assert.equal(formatRef({ col: 1, row: 1 }), 'A1');
  assert.equal(formatRef({ col: 27, row: 10 }), 'AA10');
  assert.equal(formatRef({ col: 53, row: 12 }), 'BA12');
  for (const ref of ['A1', 'Z9', 'AA10', 'AZ4', 'BA1']) {
    assert.equal(formatRef(parseRef(ref)), ref);
  }
});

test('formatRef rejects non-positive or fractional parts', () => {
  assert.throws(() => formatRef({ col: 0, row: 1 }), RangeError);
  assert.throws(() => formatRef({ col: 1, row: 0 }), RangeError);
  assert.throws(() => formatRef({ col: 1.5, row: 2 }), RangeError);
});

test('expandRange walks row-major', () => {
  assert.deepEqual(expandRange('A1:B2'), ['A1', 'B1', 'A2', 'B2']);
  assert.deepEqual(expandRange('A1:A3'), ['A1', 'A2', 'A3']);
  assert.deepEqual(expandRange('B1:D1'), ['B1', 'C1', 'D1']);
});

test('expandRange normalizes reversed corners', () => {
  assert.deepEqual(expandRange('B2:A1'), ['A1', 'B1', 'A2', 'B2']);
  assert.deepEqual(expandRange('a3:a1'), ['A1', 'A2', 'A3']);
});

test('a single cell is a valid range', () => {
  assert.deepEqual(expandRange('C4:C4'), ['C4']);
});

test('malformed ranges throw SyntaxError', () => {
  for (const bad of ['A1', 'A1:', ':B2', 'A1:B2:C3', 'A0:B2']) {
    assert.throws(() => expandRange(bad), SyntaxError);
  }
});
