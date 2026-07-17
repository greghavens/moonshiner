import { test } from 'node:test';
import assert from 'node:assert/strict';
import { stringify, parse } from './csv.ts';

test('stringify joins plain cells with commas and rows with newlines', () => {
  assert.equal(
    stringify([
      ['date', 'payee', 'amount'],
      ['2026-07-01', 'Coffee Cart', '4.50'],
    ]),
    'date,payee,amount\n2026-07-01,Coffee Cart,4.50',
  );
});

test('stringify leaves clean cells unquoted', () => {
  assert.equal(stringify([['plain', 'also plain', 'a-b_c.d']]), 'plain,also plain,a-b_c.d');
});

test('stringify quotes cells containing the delimiter', () => {
  assert.equal(stringify([['Acme, Inc.', '100']]), '"Acme, Inc.",100');
});

test('stringify doubles quotes and wraps cells containing them', () => {
  assert.equal(stringify([['He said "hi"']]), '"He said ""hi"""');
});

test('stringify quotes cells containing newlines', () => {
  assert.equal(stringify([['line1\nline2', 'x']]), '"line1\nline2",x');
});

test('stringify coerces non-string cells with String()', () => {
  assert.equal(stringify([[42, true, null]]), '42,true,null');
});

test('parse splits simple rows and fields', () => {
  assert.deepEqual(parse('a,b,c\nd,e,f'), [
    ['a', 'b', 'c'],
    ['d', 'e', 'f'],
  ]);
});

test('parse keeps delimiters inside quoted fields', () => {
  assert.deepEqual(parse('"Acme, Inc.",100'), [['Acme, Inc.', '100']]);
});

test('parse collapses doubled quotes inside quoted fields', () => {
  assert.deepEqual(parse('"He said ""hi""",ok'), [['He said "hi"', 'ok']]);
});

test('parse keeps real newlines inside quoted fields', () => {
  assert.deepEqual(parse('"line1\nline2",x\ny,z'), [
    ['line1\nline2', 'x'],
    ['y', 'z'],
  ]);
});

test('parse accepts CRLF line endings', () => {
  assert.deepEqual(parse('a,b\r\nc,d\r\n'), [
    ['a', 'b'],
    ['c', 'd'],
  ]);
});

test('a trailing newline is not an extra row and empty input has no rows', () => {
  assert.deepEqual(parse('a,b\n'), [['a', 'b']]);
  assert.deepEqual(parse(''), []);
});

test('a blank interior line is a row with one empty field', () => {
  assert.deepEqual(parse('a\n\nb'), [['a'], [''], ['b']]);
});

test('empty fields survive at every position', () => {
  assert.deepEqual(parse('a,,c'), [['a', '', 'c']]);
  assert.deepEqual(parse(',b,'), [['', 'b', '']]);
  assert.deepEqual(parse('a,'), [['a', '']]);
});

test('an unterminated quoted field is a SyntaxError', () => {
  assert.throws(() => parse('"never closed,x'), SyntaxError);
});

test('text between a closing quote and the next delimiter is a SyntaxError', () => {
  assert.throws(() => parse('"ok"junk,x'), SyntaxError);
});

test('a custom delimiter works for both directions', () => {
  assert.equal(stringify([['a', 'b;c']], { delimiter: ';' }), 'a;"b;c"');
  assert.deepEqual(parse('a;b;c', { delimiter: ';' }), [['a', 'b', 'c']]);
  assert.deepEqual(parse('"x;y";z', { delimiter: ';' }), [['x;y', 'z']]);
});

test('header mode returns one object per data row', () => {
  const text = 'date,payee,amount\n2026-07-01,"Acme, Inc.",100\n2026-07-02,Cafe,4.50';
  assert.deepEqual(parse(text, { header: true }), [
    { date: '2026-07-01', payee: 'Acme, Inc.', amount: '100' },
    { date: '2026-07-02', payee: 'Cafe', amount: '4.50' },
  ]);
});

test('parse(stringify(rows)) round-trips hostile content exactly', () => {
  const rows = [
    ['id', 'note', 'quote'],
    ['1', 'contains, commas, lots', 'she said "why?"'],
    ['2', 'multi\nline\ncell', '""already quoted""'],
    ['3', '', ','],
    ['4', 'trailing space ', ' leading space'],
  ];
  assert.deepEqual(parse(stringify(rows)), rows);
});
