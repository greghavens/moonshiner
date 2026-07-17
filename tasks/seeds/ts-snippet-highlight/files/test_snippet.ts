import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildSnippet } from './snippet.ts';

test('wraps the match in <mark> when the text fits the window', () => {
  assert.equal(
    buildSnippet('The quick brown fox', 'brown'),
    'The quick <mark>brown</mark> fox',
  );
});

test('cuts a window around the match and marks both cut edges', () => {
  assert.equal(
    buildSnippet('aaaa bbbb cccc dddd eeee', 'cccc', { radius: 5 }),
    '…bbbb <mark>cccc</mark> dddd…',
  );
});

test('matching is case-insensitive but output keeps document casing', () => {
  assert.equal(buildSnippet('Say Hello World', 'hello'), 'Say <mark>Hello</mark> World');
});

test('no ellipsis on a side that reaches the document edge', () => {
  assert.equal(
    buildSnippet('start middle end', 'start', { radius: 4 }),
    '<mark>start</mark> mid…',
  );
  assert.equal(
    buildSnippet('start middle end', 'end', { radius: 4 }),
    '…dle <mark>end</mark>',
  );
});

test('a term that is not found yields the truncated head, unmarked', () => {
  assert.equal(buildSnippet('abcdefghij', 'zz', { radius: 3 }), 'abcdef…');
  assert.equal(buildSnippet('abcd', 'zz', { radius: 3 }), 'abcd');
});

test('an empty term behaves like not-found', () => {
  assert.equal(buildSnippet('abcdefghij', '', { radius: 3 }), 'abcdef…');
});

test('a custom ellipsis string is honored', () => {
  assert.equal(
    buildSnippet('aaaa bbbb cccc dddd eeee', 'cccc', { radius: 5, ellipsis: '...' }),
    '...bbbb <mark>cccc</mark> dddd...',
  );
});
