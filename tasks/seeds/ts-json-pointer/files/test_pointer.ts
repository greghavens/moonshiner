import { test } from 'node:test';
import assert from 'node:assert/strict';
import { get, has, applyPatch } from './pointer.ts';

// RFC 6901's own example document.
const rfcDoc = {
  foo: ['bar', 'baz'],
  '': 0,
  'a/b': 1,
  'c%d': 2,
  'e^f': 3,
  'g|h': 4,
  'i\\j': 5,
  'k"l': 6,
  ' ': 7,
  'm~n': 8,
};

// -- get ----------------------------------------------------------------------

test('the empty pointer resolves to the whole document', () => {
  assert.equal(get(rfcDoc, ''), rfcDoc);
});

test('resolves the RFC 6901 example pointers', () => {
  assert.deepEqual(get(rfcDoc, '/foo'), ['bar', 'baz']);
  assert.equal(get(rfcDoc, '/foo/0'), 'bar');
  assert.equal(get(rfcDoc, '/'), 0);
  assert.equal(get(rfcDoc, '/a~1b'), 1);
  assert.equal(get(rfcDoc, '/c%d'), 2);
  assert.equal(get(rfcDoc, '/m~0n'), 8);
  assert.equal(get(rfcDoc, '/ '), 7);
});

test('~01 unescapes to the literal key ~1 (unescape order matters)', () => {
  const doc = { '~1': 'tilde-one', '/': 'slash' };
  assert.equal(get(doc, '/~01'), 'tilde-one');
  assert.equal(get(doc, '/~1'), 'slash');
});

test('digs through nested objects and arrays', () => {
  const doc = { servers: [{ tags: { env: 'prod' } }, { tags: { env: 'dev' } }] };
  assert.equal(get(doc, '/servers/1/tags/env'), 'dev');
});

test('a pointer that does not start with / is malformed', () => {
  assert.throws(() => get(rfcDoc, 'foo'), Error);
  assert.throws(() => get(rfcDoc, 'foo/0'), Error);
});

test('missing keys and bad array indices are unresolvable', () => {
  assert.throws(() => get(rfcDoc, '/nope'));
  assert.throws(() => get(rfcDoc, '/foo/2')); // out of range
  assert.throws(() => get(rfcDoc, '/foo/-')); // '-' never resolves on read
  assert.throws(() => get(rfcDoc, '/foo/01')); // leading zero
  assert.throws(() => get(rfcDoc, '/foo/x')); // not an index
  assert.throws(() => get(rfcDoc, '/foo/0/deeper')); // descending into a string
});

// -- has ----------------------------------------------------------------------

test('has() answers instead of throwing for unresolvable paths', () => {
  assert.equal(has(rfcDoc, '/foo/1'), true);
  assert.equal(has(rfcDoc, '/foo/2'), false);
  assert.equal(has(rfcDoc, '/nope'), false);
  assert.equal(has(rfcDoc, ''), true);
});

test('has() still rejects malformed pointers', () => {
  assert.throws(() => has(rfcDoc, 'not-a-pointer'));
});

// -- applyPatch: add ----------------------------------------------------------

test('add sets a new object member and overwrites an existing one', () => {
  const doc = { a: 1 };
  const out = applyPatch(doc, [
    { op: 'add', path: '/b', value: 2 },
    { op: 'add', path: '/a', value: 99 },
  ]) as Record<string, unknown>;
  assert.deepEqual(out, { a: 99, b: 2 });
});

test('add into an array inserts and shifts the tail', () => {
  const doc = { list: ['a', 'c'] };
  const out = applyPatch(doc, [{ op: 'add', path: '/list/1', value: 'b' }]);
  assert.deepEqual(out, { list: ['a', 'b', 'c'] });
});

test('add with - appends to an array', () => {
  const doc = { list: [1, 2] };
  const out = applyPatch(doc, [{ op: 'add', path: '/list/-', value: 3 }]);
  assert.deepEqual(out, { list: [1, 2, 3] });
});

test('add at an index just past the end is allowed; further is not', () => {
  const doc = { list: [1] };
  assert.deepEqual(applyPatch(doc, [{ op: 'add', path: '/list/1', value: 2 }]), {
    list: [1, 2],
  });
  assert.throws(() => applyPatch(doc, [{ op: 'add', path: '/list/5', value: 9 }]));
});

test('add with path "" replaces the whole document', () => {
  const out = applyPatch({ old: true }, [{ op: 'add', path: '', value: { fresh: 1 } }]);
  assert.deepEqual(out, { fresh: 1 });
});

test('add requires the parent to exist', () => {
  assert.throws(() => applyPatch({}, [{ op: 'add', path: '/a/b', value: 1 }]));
});

// -- applyPatch: remove and replace --------------------------------------------

test('remove deletes an object member', () => {
  const out = applyPatch({ a: 1, b: 2 }, [{ op: 'remove', path: '/a' }]);
  assert.deepEqual(out, { b: 2 });
});

test('remove on an array index closes the gap', () => {
  const out = applyPatch({ list: ['a', 'b', 'c'] }, [{ op: 'remove', path: '/list/1' }]);
  assert.deepEqual(out, { list: ['a', 'c'] });
});

test('remove of a missing target fails', () => {
  assert.throws(() => applyPatch({ a: 1 }, [{ op: 'remove', path: '/b' }]));
  assert.throws(() => applyPatch({ list: [] }, [{ op: 'remove', path: '/list/0' }]));
});

test('replace swaps an existing value and fails on a missing one', () => {
  assert.deepEqual(applyPatch({ a: 1 }, [{ op: 'replace', path: '/a', value: 2 }]), { a: 2 });
  assert.throws(() => applyPatch({ a: 1 }, [{ op: 'replace', path: '/b', value: 2 }]));
});

// -- applyPatch: test op --------------------------------------------------------

test('test passes on deep equality regardless of key order', () => {
  const doc = { cfg: { retries: 3, verbose: true }, tags: ['a', 'b'] };
  const out = applyPatch(doc, [
    { op: 'test', path: '/cfg', value: { verbose: true, retries: 3 } },
    { op: 'test', path: '/tags', value: ['a', 'b'] },
  ]);
  assert.deepEqual(out, doc);
});

test('test fails on different values or different array order', () => {
  const doc = { tags: ['a', 'b'], n: 1 };
  assert.throws(() => applyPatch(doc, [{ op: 'test', path: '/tags', value: ['b', 'a'] }]));
  assert.throws(() => applyPatch(doc, [{ op: 'test', path: '/n', value: '1' }])); // no coercion
});

// -- applyPatch: sequencing, atomicity, purity -----------------------------------

test('ops apply in order, later ops seeing earlier results', () => {
  const out = applyPatch({ count: 1 }, [
    { op: 'add', path: '/tmp', value: [1] },
    { op: 'add', path: '/tmp/-', value: 2 },
    { op: 'test', path: '/tmp', value: [1, 2] },
    { op: 'replace', path: '/count', value: 2 },
    { op: 'remove', path: '/tmp' },
  ]);
  assert.deepEqual(out, { count: 2 });
});

test('the input document is never mutated on success', () => {
  const doc = { list: [1, 2], meta: { keep: true } };
  const out = applyPatch(doc, [
    { op: 'add', path: '/list/-', value: 3 },
    { op: 'replace', path: '/meta', value: { keep: false } },
  ]);
  assert.deepEqual(doc, { list: [1, 2], meta: { keep: true } });
  assert.deepEqual(out, { list: [1, 2, 3], meta: { keep: false } });
});

test('a failing op mid-sequence throws and leaves the input untouched', () => {
  const doc = { a: 1 };
  assert.throws(() =>
    applyPatch(doc, [
      { op: 'add', path: '/b', value: 2 },
      { op: 'test', path: '/a', value: 999 }, // fails
      { op: 'remove', path: '/a' },
    ]),
  );
  assert.deepEqual(doc, { a: 1 });
});

test('an unknown op is rejected', () => {
  assert.throws(() => applyPatch({}, [{ op: 'move', path: '/a' } as never]));
});
