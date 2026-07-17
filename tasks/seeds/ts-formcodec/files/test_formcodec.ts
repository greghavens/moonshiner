// Acceptance tests for the quote-request form codec.
//
// The serializer half (collectPairs / encodePairs / serialize) has been in
// production for a quarter powering draft autosave — its tests below are
// EXISTING BEHAVIOR and must stay green. The new draft-restore feature adds
// parse() and FormDecodeError; those tests follow.
//
// Run: node --test test_formcodec.ts

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { collectPairs, encodePairs, serialize, type Field } from './formcodec.ts';
import * as codec from './formcodec.ts';

// ---------------------------------------------------------------------------
// EXISTING BEHAVIOR — draft autosave has shipped on this. Do not change it.
// ---------------------------------------------------------------------------

test('existing: fields serialize in document order', () => {
  const fields: Field[] = [
    { kind: 'text', name: 'a', value: '1' },
    { kind: 'textarea', name: 'b', value: 'two words' },
  ];
  assert.equal(serialize(fields), 'a=1&b=two+words');
});

test('existing: disabled fields and empty names contribute nothing', () => {
  const fields: Field[] = [
    { kind: 'text', name: 'kept', value: 'x' },
    { kind: 'text', name: 'off', value: 'y', disabled: true },
    { kind: 'text', name: '', value: 'z' },
    { kind: 'checkbox', name: 'gone', value: 'v', checked: true, disabled: true },
  ];
  assert.deepEqual(collectPairs(fields), [['kept', 'x']]);
});

test('existing: checkboxes and radios submit only when checked', () => {
  const fields: Field[] = [
    { kind: 'checkbox', name: 'tos', value: 'yes', checked: true },
    { kind: 'checkbox', name: 'news', value: 'yes', checked: false },
    { kind: 'radio', name: 'size', value: 's', checked: false },
    { kind: 'radio', name: 'size', value: 'm', checked: true },
    { kind: 'radio', name: 'size', value: 'l', checked: false },
  ];
  assert.deepEqual(collectPairs(fields), [
    ['tos', 'yes'],
    ['size', 'm'],
  ]);
});

test('existing: multi-select emits one pair per selected option, in option order', () => {
  const fields: Field[] = [
    {
      kind: 'select',
      name: 'color',
      multiple: true,
      options: [
        { value: 'red', selected: true },
        { value: 'green', selected: false },
        { value: 'blue', selected: true },
      ],
    },
  ];
  assert.equal(serialize(fields), 'color=red&color=blue');
});

test('existing: single select takes the first selected option, or nothing', () => {
  const two: Field[] = [
    {
      kind: 'select',
      name: 'tier',
      multiple: false,
      options: [
        { value: 'basic', selected: true },
        { value: 'pro', selected: true },
      ],
    },
  ];
  assert.deepEqual(collectPairs(two), [['tier', 'basic']]);

  const none: Field[] = [
    {
      kind: 'select',
      name: 'tier',
      multiple: false,
      options: [{ value: 'basic', selected: false }],
    },
  ];
  assert.deepEqual(collectPairs(none), []);
});

test('existing: the urlencoded alphabet', () => {
  // A-Za-z0-9 * - . _ stay literal, space is '+', the rest is %XX (uppercase).
  assert.equal(encodePairs([['q', 'Az09*-._']]), 'q=Az09*-._');
  assert.equal(encodePairs([['q', 'a b']]), 'q=a+b');
  assert.equal(encodePairs([['x&y', 'x=y']]), 'x%26y=x%3Dy');
  assert.equal(encodePairs([['note', 'line1\nline2']]), 'note=line1%0Aline2');
  assert.equal(encodePairs([['home', '~venom']]), 'home=%7Evenom');
});

test('existing: non-ascii encodes as utf-8 bytes', () => {
  assert.equal(encodePairs([['city', 'Zürich']]), 'city=Z%C3%BCrich');
  assert.equal(encodePairs([['name', 'café']]), 'name=caf%C3%A9');
});

test('existing: empty values and empty forms', () => {
  assert.equal(encodePairs([['note', '']]), 'note=');
  assert.equal(serialize([]), '');
});

// ---------------------------------------------------------------------------
// NEW: draft restore — parse() and FormDecodeError
// ---------------------------------------------------------------------------

test('parse returns pairs in wire order, duplicates preserved', () => {
  assert.deepEqual(codec.parse('a=1&b=two+words&a=3'), [
    ['a', '1'],
    ['b', 'two words'],
    ['a', '3'],
  ]);
});

test('parse splits each segment on the first equals sign only', () => {
  assert.deepEqual(codec.parse('a=b=c'), [['a', 'b=c']]);
  assert.deepEqual(codec.parse('flag'), [['flag', '']]);
  assert.deepEqual(codec.parse('=v'), [['', 'v']]);
});

test('parse skips empty segments', () => {
  assert.deepEqual(codec.parse(''), []);
  assert.deepEqual(codec.parse('a=1&&b=2'), [
    ['a', '1'],
    ['b', '2'],
  ]);
  assert.deepEqual(codec.parse('&a=1&'), [['a', '1']]);
});

test('parse decodes percent escapes in either hex case', () => {
  assert.deepEqual(codec.parse('path=%2Fa%2fb'), [['path', '/a/b']]);
  assert.deepEqual(codec.parse('sum=1%2B1'), [['sum', '1+1']]);
});

test('parse treats plus as space and leaves other literals alone', () => {
  assert.deepEqual(codec.parse('q=a+b'), [['q', 'a b']]);
  // this is a body codec: '?' has no special meaning and stays put
  assert.deepEqual(codec.parse('q=what%3F&raw=?'), [
    ['q', 'what?'],
    ['raw', '?'],
  ]);
});

test('parse decodes utf-8 byte sequences and accepts literal non-ascii', () => {
  assert.deepEqual(codec.parse('name=caf%C3%A9'), [['name', 'café']]);
  assert.deepEqual(codec.parse('name=café'), [['name', 'café']]);
});

test('malformed input raises FormDecodeError', () => {
  assert.equal(typeof codec.FormDecodeError, 'function');
  assert.ok(new codec.FormDecodeError('x') instanceof Error);
  const bad = [
    'a=%', // percent with nothing after it
    'a=%2', // truncated escape
    'a=%G1', // non-hex digit
    'a=%f', // one hex digit is not enough
    'a=100%', // trailing bare percent
    'a=%FF', // not valid utf-8
    'a=%C3', // truncated utf-8 sequence
  ];
  for (const input of bad) {
    let caught: unknown;
    try {
      codec.parse(input);
    } catch (err) {
      caught = err;
    }
    assert.ok(
      caught instanceof codec.FormDecodeError,
      `expected FormDecodeError for ${JSON.stringify(input)}, got ${String(caught)}`,
    );
  }
});

test('round trip: parse(serialize(fields)) equals collectPairs(fields)', () => {
  const fields: Field[] = [
    { kind: 'text', name: 'company', value: 'Åkerman & Söner AB' },
    { kind: 'textarea', name: 'notes', value: 'line one\r\nline two — ok 😀' },
    { kind: 'text', name: 'discount', value: 'red 40%' },
    { kind: 'checkbox', name: 'rush', value: 'yes', checked: true },
    { kind: 'text', name: 'notes', value: 'second field, same name' },
    { kind: 'text', name: 'internal', value: 'hidden', disabled: true },
    {
      kind: 'select',
      name: 'region',
      multiple: true,
      options: [
        { value: 'eu-north', selected: true },
        { value: 'us-east', selected: true },
      ],
    },
  ];
  assert.deepEqual(codec.parse(serialize(fields)), collectPairs(fields));
});

test('round trip: encodePairs(parse(s)) reproduces a canonical string', () => {
  const wire = 'a=1&b=two+words&note=line1%0Aline2&name=caf%C3%A9&x%26y=x%3Dy';
  assert.equal(encodePairs(codec.parse(wire)), wire);
});
