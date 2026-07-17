import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildSnippet, findMatches } from './snippet.ts';

// --- findMatches: merged match ranges ---

test('adjacent term hits merge into one range', () => {
  assert.deepEqual(findMatches('the javascript handbook', ['java', 'script']), [
    { start: 4, end: 14 },
  ]);
  assert.deepEqual(findMatches('database', ['data', 'base']), [{ start: 0, end: 8 }]);
});

test('properly overlapping hits merge too', () => {
  assert.deepEqual(findMatches('abcde', ['abc', 'cde']), [{ start: 0, end: 5 }]);
});

test('disjoint hits stay separate and come back sorted by position', () => {
  assert.deepEqual(findMatches('alpha beta', ['beta', 'alpha']), [
    { start: 0, end: 5 },
    { start: 6, end: 10 },
  ]);
});

test('every occurrence of a term is found, case-insensitively', () => {
  assert.deepEqual(findMatches('Cat and cAt', ['cat']), [
    { start: 0, end: 3 },
    { start: 8, end: 11 },
  ]);
});

test('duplicate and empty terms are ignored', () => {
  assert.deepEqual(findMatches('abc', ['b', 'b', '']), [{ start: 1, end: 2 }]);
  assert.deepEqual(findMatches('abc', []), []);
});

// --- buildSnippet with multiple terms ---

test('accepts an array of terms and marks each hit', () => {
  assert.equal(
    buildSnippet('Rust and Java for systems', ['java', 'rust']),
    '<mark>Rust</mark> and <mark>Java</mark> for systems',
  );
});

test('overlapping hits render as ONE mark, never nested tags', () => {
  assert.equal(
    buildSnippet('the javascript handbook', ['java', 'script']),
    'the <mark>javascript</mark> handbook',
  );
});

test('repeated hits of one term are all marked', () => {
  assert.equal(buildSnippet('cat and cat', ['cat']), '<mark>cat</mark> and <mark>cat</mark>');
});

test('the window centers on the earliest hit and stretches to cover a straddling one', () => {
  const text = 'alpha 123456789 beta tail here';
  assert.equal(
    buildSnippet(text, ['alpha', 'beta'], { radius: 12 }),
    '<mark>alpha</mark> 123456789 <mark>beta</mark>…',
  );
});

test('hits entirely beyond the window are not dragged in', () => {
  const text = 'alpha 123456789 beta tail here';
  assert.equal(
    buildSnippet(text, ['alpha', 'beta'], { radius: 5 }),
    '<mark>alpha</mark> 1234…',
  );
});

test('a one-element array behaves exactly like the string form', () => {
  const text = 'aaaa bbbb cccc dddd eeee';
  assert.equal(
    buildSnippet(text, ['cccc'], { radius: 5 }),
    buildSnippet(text, 'cccc', { radius: 5 }),
  );
});

test('an empty array or all-miss terms fall back to the unmarked head', () => {
  assert.equal(buildSnippet('abcdefghij', [], { radius: 3 }), 'abcdef…');
  assert.equal(buildSnippet('abcdefghij', ['zz', 'yy'], { radius: 3 }), 'abcdef…');
});
