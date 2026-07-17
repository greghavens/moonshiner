import { test } from 'node:test';
import assert from 'node:assert/strict';
import { merge3 } from './merge.ts';

test('arrays are atomic by default: divergent edits conflict as whole arrays', () => {
  const r = merge3(
    { tags: ['a', 'b'] },
    { tags: ['a', 'b', 'c'] },
    { tags: ['b', 'a'] },
  );
  assert.deepEqual(r.merged, { tags: ['a', 'b'] });
  assert.deepEqual(r.conflicts, [
    {
      path: 'tags',
      kind: 'edit-edit',
      base: ['a', 'b'],
      ours: ['a', 'b', 'c'],
      theirs: ['b', 'a'],
    },
  ]);
});

test('an array edited on only one side wins wholesale, even reorders', () => {
  const r = merge3({ steps: ['lint', 'test'] }, { steps: ['test', 'lint'] }, { steps: ['lint', 'test'] });
  assert.deepEqual(r.merged, { steps: ['test', 'lint'] });
  assert.deepEqual(r.conflicts, []);
});

test('identical array edits on both sides are not a conflict', () => {
  const r = merge3({ tags: ['a'] }, { tags: ['a', 'z'] }, { tags: ['a', 'z'] });
  assert.deepEqual(r.merged, { tags: ['a', 'z'] });
  assert.deepEqual(r.conflicts, []);
});

test('union strategy: ours order first, then unseen elements of theirs', () => {
  const r = merge3(
    { tags: ['a', 'b'] },
    { tags: ['a', 'b', 'c'] },
    { tags: ['b', 'a', 'd'] },
    { arrays: 'union' },
  );
  assert.deepEqual(r.merged, { tags: ['a', 'b', 'c', 'd'] });
  assert.deepEqual(r.conflicts, []);
});

test('union deduplicates by deep equality, not identity', () => {
  const r = merge3(
    { hooks: [{ on: 'push' }] },
    { hooks: [{ on: 'push' }, { on: 'tag' }] },
    { hooks: [{ on: 'tag' }, { on: 'merge' }] },
    { arrays: 'union' },
  );
  assert.deepEqual(r.merged, { hooks: [{ on: 'push' }, { on: 'tag' }, { on: 'merge' }] });
});

test('union only applies when both sides changed; a lone edit still wins wholesale', () => {
  const r = merge3(
    { tags: ['a', 'b'] },
    { tags: ['a', 'b'] },
    { tags: ['b'] },
    { arrays: 'union' },
  );
  assert.deepEqual(r.merged, { tags: ['b'] });
  assert.deepEqual(r.conflicts, []);
});

test('ours and theirs strategies pick a side when both changed', () => {
  const base = { allow: ['gzip'] };
  const ours = { allow: ['gzip', 'br'] };
  const theirs = { allow: ['deflate'] };
  assert.deepEqual(merge3(base, ours, theirs, { arrays: 'ours' }).merged, {
    allow: ['gzip', 'br'],
  });
  assert.deepEqual(merge3(base, ours, theirs, { arrays: 'theirs' }).merged, {
    allow: ['deflate'],
  });
  assert.deepEqual(merge3(base, ours, theirs, { arrays: 'ours' }).conflicts, []);
});

test('per-path overrides beat the global strategy and use dotted paths', () => {
  const r = merge3(
    { build: { steps: ['lint'] }, tags: ['a'] },
    { build: { steps: ['lint', 'test'] }, tags: ['a', 'o'] },
    { build: { steps: ['fmt', 'lint'] }, tags: ['a', 't'] },
    { arrayOverrides: { 'build.steps': 'union' } },
  );
  assert.deepEqual(r.merged.build, { steps: ['lint', 'test', 'fmt'] });
  assert.equal(r.conflicts.length, 1);
  assert.equal(r.conflicts[0].path, 'tags');
});

test('a strategy never applies when one side is not an array', () => {
  const r = merge3({ x: [1] }, { x: [1, 2] }, { x: 5 }, { arrays: 'union' });
  assert.deepEqual(r.conflicts, [
    { path: 'x', kind: 'edit-edit', base: [1], ours: [1, 2], theirs: 5 },
  ]);
});

test('unknown strategy names are refused loudly', () => {
  assert.throws(() => merge3({ a: [1] }, { a: [2] }, { a: [3] }, { arrays: 'zip' as never }), /zip/);
  assert.throws(
    () => merge3({ a: [1] }, { a: [2] }, { a: [3] }, { arrayOverrides: { a: 'splice' as never } }),
    /splice/,
  );
});
