import { test } from 'node:test';
import assert from 'node:assert/strict';
import { merge3 } from './merge.ts';
import { renderConflicts } from './markers.ts';
import { resolve } from './resolve.ts';

test('a clean merge renders an empty report', () => {
  const r = merge3({ a: 1 }, { a: 2 }, { a: 1 });
  assert.equal(renderConflicts(r), '');
});

test('an edit-edit conflict renders a diff3-style block', () => {
  const r = merge3({ server: { port: 8080 } }, { server: { port: 9090 } }, { server: { port: 3000 } });
  assert.equal(
    renderConflicts(r),
    [
      '# server.port: edit-edit',
      '<<<<<<< ours',
      '9090',
      '||||||| base',
      '8080',
      '=======',
      '3000',
      '>>>>>>> theirs',
      '',
    ].join('\n'),
  );
});

test('missing sides render as (missing)', () => {
  const r = merge3({ ttl: 60 }, {}, { ttl: 300 });
  assert.equal(
    renderConflicts(r),
    [
      '# ttl: delete-edit',
      '<<<<<<< ours',
      '(missing)',
      '||||||| base',
      '60',
      '=======',
      '300',
      '>>>>>>> theirs',
      '',
    ].join('\n'),
  );
});

test('object values are pretty-printed with two-space indent', () => {
  const r = merge3({ db: 5 }, { db: { host: 'a' } }, { db: 9 });
  assert.equal(
    renderConflicts(r),
    [
      '# db: edit-edit',
      '<<<<<<< ours',
      '{',
      '  "host": "a"',
      '}',
      '||||||| base',
      '5',
      '=======',
      '9',
      '>>>>>>> theirs',
      '',
    ].join('\n'),
  );
});

test('multiple conflicts are separated by a blank line, in conflict order', () => {
  const r = merge3({ x: 1, y: 1 }, { x: 2, y: 3 }, { x: 9, y: 4 });
  const text = renderConflicts(r);
  assert.match(text, /^# x: edit-edit\n/);
  assert.match(text, /\n>>>>>>> theirs\n\n# y: edit-edit\n/);
  assert.match(text, /\n>>>>>>> theirs\n$/);
});

test('a root conflict is labeled (root)', () => {
  const r = merge3('a', 'b', 'c');
  assert.match(renderConflicts(r), /^# \(root\): edit-edit\n/);
});

test('resolve picks a side and clears the conflict', () => {
  const r = merge3({ port: 8080 }, { port: 9090 }, { port: 3000 });
  const done = resolve(r, { port: 'theirs' });
  assert.deepEqual(done.merged, { port: 3000 });
  assert.deepEqual(done.conflicts, []);
});

test('resolve leaves unresolved conflicts in place and does not mutate its input', () => {
  const r = merge3({ x: 1, y: 1 }, { x: 2, y: 3 }, { x: 9, y: 4 });
  const done = resolve(r, { x: 'ours' });
  assert.deepEqual(done.merged, { x: 2, y: 1 });
  assert.deepEqual(done.conflicts.map((c) => c.path), ['y']);
  assert.deepEqual(r.merged, { x: 1, y: 1 });
  assert.equal(r.conflicts.length, 2);
});

test('resolving toward a missing side deletes the key', () => {
  const r = merge3({ cache: { ttl: 60 } }, { cache: {} }, { cache: { ttl: 300 } });
  const done = resolve(r, { 'cache.ttl': 'ours' });
  assert.deepEqual(done.merged, { cache: {} });
  assert.deepEqual(done.conflicts, []);
});

test('resolve can keep base explicitly', () => {
  const r = merge3({ port: 8080 }, { port: 9090 }, { port: 3000 });
  const done = resolve(r, { port: 'base' });
  assert.deepEqual(done.merged, { port: 8080 });
  assert.deepEqual(done.conflicts, []);
});

test('a root conflict resolves through the empty path', () => {
  const r = merge3(1, 2, 3);
  assert.equal(resolve(r, { '': 'theirs' }).merged, 3);
});

test('unknown paths and unknown choices are refused by name', () => {
  const r = merge3({ port: 8080 }, { port: 9090 }, { port: 3000 });
  assert.throws(() => resolve(r, { host: 'ours' }), /host/);
  assert.throws(() => resolve(r, { port: 'mine' as never }), /mine/);
});
