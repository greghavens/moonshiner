import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Sheet } from './sheet.ts';

test('literals: numbers, numeric strings, text, empties', () => {
  const s = new Sheet();
  s.set('A1', 5);
  s.set('a2', '7');
  s.set('A3', ' -3.5 ');
  s.set('A4', 'hello');
  assert.equal(s.get('A1'), 5);
  assert.equal(s.get('A2'), 7);
  assert.equal(s.get('A3'), -3.5);
  assert.equal(s.get('A4'), 'hello');
  assert.equal(s.get('B9'), '');
});

test('raw returns exactly what was set', () => {
  const s = new Sheet();
  s.set('A1', 5);
  s.set('A2', '7');
  s.set('A3', 'hello');
  s.set('A4', '=A1+1');
  assert.equal(s.raw('A1'), 5);
  assert.equal(s.raw('A2'), '7');
  assert.equal(s.raw('A3'), 'hello');
  assert.equal(s.raw('A4'), '=A1+1');
  assert.equal(s.raw('Z9'), '');
});

test('setting the empty string or calling clear empties a cell', () => {
  const s = new Sheet();
  s.set('A1', 42);
  s.set('A1', '');
  assert.equal(s.get('A1'), '');
  s.set('B1', 'text');
  s.clear('b1');
  assert.equal(s.get('B1'), '');
  assert.deepEqual(s.cells(), []);
});

test('cells() lists non-empty refs by row then column', () => {
  const s = new Sheet();
  s.set('C1', 1);
  s.set('A2', 2);
  s.set('B1', 3);
  assert.deepEqual(s.cells(), ['B1', 'C1', 'A2']);
});

test('arithmetic honors precedence, parens and unary minus', () => {
  const s = new Sheet();
  s.set('A1', 4);
  s.set('B1', '=2+3*4');
  s.set('B2', '=(2+3)*4');
  s.set('B3', '=2*-3');
  s.set('B4', '=-A1+10');
  s.set('B5', '=10/4');
  assert.equal(s.get('B1'), 14);
  assert.equal(s.get('B2'), 20);
  assert.equal(s.get('B3'), -6);
  assert.equal(s.get('B4'), 6);
  assert.equal(s.get('B5'), 2.5);
});

test('a direct ref to an empty cell is zero', () => {
  const s = new Sheet();
  s.set('A1', '=B1+1');
  assert.equal(s.get('A1'), 1);
});

test('a direct ref to a text cell is a #VALUE! error', () => {
  const s = new Sheet();
  s.set('A1', 'hello');
  s.set('B1', '=A1+1');
  assert.equal(s.get('B1'), '#VALUE!');
});

test('division by zero', () => {
  const s = new Sheet();
  s.set('A1', '=1/0');
  assert.equal(s.get('A1'), '#DIV/0!');
  s.set('B1', 6);
  s.set('B2', '=B1/C1'); // C1 empty = 0
  assert.equal(s.get('B2'), '#DIV/0!');
});

test('errors propagate through references', () => {
  const s = new Sheet();
  s.set('A1', 'label');
  s.set('B1', '=A1*2');
  s.set('C1', '=B1+1');
  assert.equal(s.get('B1'), '#VALUE!');
  assert.equal(s.get('C1'), '#VALUE!');
});

test('edits ripple through dependent formulas', () => {
  const s = new Sheet();
  s.set('A1', 3);
  s.set('B1', '=A1+1');
  s.set('C1', '=B1*2');
  assert.equal(s.get('C1'), 8);
  s.set('A1', 10);
  assert.equal(s.get('B1'), 11);
  assert.equal(s.get('C1'), 22);
  s.set('B1', '=A1-1'); // rewiring B1 must update C1 too
  assert.equal(s.get('C1'), 18);
  s.clear('A1'); // empty ref reads as 0
  assert.equal(s.get('C1'), -2);
});

test('SUM adds ranges and expressions, skipping text and empties in ranges', () => {
  const s = new Sheet();
  s.set('A1', 10);
  s.set('A3', 20);
  s.set('B1', 'header');
  s.set('D1', '=SUM(A1:A3)');
  s.set('D2', '=SUM(A1:B1)');
  s.set('D3', '=SUM(A1:A3, 5)');
  s.set('D4', '=SUM(A1, A3)');
  assert.equal(s.get('D1'), 30);
  assert.equal(s.get('D2'), 10);
  assert.equal(s.get('D3'), 35);
  assert.equal(s.get('D4'), 30);
});

test('SUM of an all-empty range is zero', () => {
  const s = new Sheet();
  s.set('A1', '=SUM(F1:F5)');
  assert.equal(s.get('A1'), 0);
});

test('a text cell used as a direct argument is still an error', () => {
  const s = new Sheet();
  s.set('B1', 'header');
  s.set('D1', '=SUM(B1)');
  assert.equal(s.get('D1'), '#VALUE!');
});

test('an error cell inside a range propagates', () => {
  const s = new Sheet();
  s.set('C1', '=1/0');
  s.set('C2', 5);
  s.set('D1', '=SUM(C1:C2)');
  assert.equal(s.get('D1'), '#DIV/0!');
});

test('AVG divides by the count of collected values', () => {
  const s = new Sheet();
  s.set('A1', 10);
  s.set('A3', 20);
  s.set('B1', '=AVG(A1:A3)');
  s.set('B2', '=AVG(A1:A3, 30)');
  s.set('B3', '=AVG(D1:D3)');
  assert.equal(s.get('B1'), 15);
  assert.equal(s.get('B2'), 20);
  assert.equal(s.get('B3'), '#DIV/0!');
});

test('function names and refs in formulas are case-insensitive', () => {
  const s = new Sheet();
  s.set('a1', 2);
  s.set('a2', 4);
  s.set('b1', '=sum(a1:a2)');
  s.set('b2', '=Avg(A1:a2)');
  assert.equal(s.get('B1'), 6);
  assert.equal(s.get('B2'), 3);
});

test('multi-letter columns work in formulas', () => {
  const s = new Sheet();
  s.set('AA1', 5);
  s.set('AB1', '=AA1*2');
  assert.equal(s.get('AB1'), 10);
});

test('a two-cell cycle marks both cells', () => {
  const s = new Sheet();
  s.set('A1', '=B1+1');
  s.set('B1', '=A1+1');
  assert.equal(s.get('A1'), '#CYCLE!');
  assert.equal(s.get('B1'), '#CYCLE!');
});

test('self-reference is a cycle', () => {
  const s = new Sheet();
  s.set('D4', '=D4+1');
  assert.equal(s.get('D4'), '#CYCLE!');
});

test('longer cycles are detected and dependents see the error', () => {
  const s = new Sheet();
  s.set('E1', '=F1+1');
  s.set('F1', '=G1+1');
  s.set('G1', '=E1+1');
  s.set('H1', '=E1*2');
  assert.equal(s.get('E1'), '#CYCLE!');
  assert.equal(s.get('F1'), '#CYCLE!');
  assert.equal(s.get('G1'), '#CYCLE!');
  assert.equal(s.get('H1'), '#CYCLE!');
});

test('fixing a cell clears the cycle', () => {
  const s = new Sheet();
  s.set('A1', '=B1+1');
  s.set('B1', '=A1+1');
  s.set('C1', '=A1*2');
  assert.equal(s.get('C1'), '#CYCLE!');
  s.set('B1', 5);
  assert.equal(s.get('A1'), 6);
  assert.equal(s.get('C1'), 12);
});

test('SUM through a range also participates in cycle detection', () => {
  const s = new Sheet();
  s.set('A1', 1);
  s.set('A2', 2);
  s.set('A3', '=SUM(A1:A3)'); // the range includes A3 itself
  assert.equal(s.get('A3'), '#CYCLE!');
  s.set('A3', '=SUM(A1:A2)');
  assert.equal(s.get('A3'), 3);
});

test('malformed formulas throw at set time and leave the cell alone', () => {
  const s = new Sheet();
  s.set('A1', 5);
  assert.throws(() => s.set('A1', '=1+'), SyntaxError);
  assert.throws(() => s.set('A1', '=(2*3'), SyntaxError);
  assert.throws(() => s.set('A1', '=A1:A2+1'), SyntaxError);
  assert.equal(s.get('A1'), 5);
  assert.equal(s.raw('A1'), 5);
});

test('unknown functions are rejected by name', () => {
  const s = new Sheet();
  assert.throws(() => s.set('A1', '=MAX(A1:A2)'), /MAX/);
  assert.throws(() => s.set('A1', '=MAX(A1:A2)'), SyntaxError);
});

test('bad refs are rejected wherever they appear', () => {
  const s = new Sheet();
  assert.throws(() => s.set('1A', 5), SyntaxError);
  assert.throws(() => s.get(''), SyntaxError);
  assert.throws(() => s.raw('A0'), SyntaxError);
});
