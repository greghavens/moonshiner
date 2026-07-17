import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mapRows, resolveColumns } from './colmap.ts';

const specs = [
  { header: 'Email', key: 'email', required: true },
  { header: 'Full Name', key: 'name' },
];

test('maps header text to object keys, row by row', () => {
  const rows = [
    ['Email', 'Full Name'],
    ['a@x.io', 'Ada'],
    ['b@x.io', 'Bob'],
  ];
  assert.deepEqual(mapRows(rows, specs), [
    { email: 'a@x.io', name: 'Ada' },
    { email: 'b@x.io', name: 'Bob' },
  ]);
});

test('header matching trims whitespace and ignores case', () => {
  const rows = [
    ['  EMAIL ', 'full name'],
    ['a@x.io', 'Ada'],
  ];
  assert.deepEqual(mapRows(rows, specs), [{ email: 'a@x.io', name: 'Ada' }]);
});

test('a missing required column throws, naming the column', () => {
  assert.throws(() => mapRows([['Full Name'], ['Ada']], specs), /Email/);
});

test('a missing optional column just omits the key', () => {
  const rows = [['Email'], ['a@x.io']];
  assert.deepEqual(mapRows(rows, specs), [{ email: 'a@x.io' }]);
});

test('rows shorter than the header are padded with empty strings', () => {
  const rows = [
    ['Email', 'Full Name'],
    ['a@x.io'],
  ];
  assert.deepEqual(mapRows(rows, specs), [{ email: 'a@x.io', name: '' }]);
});

test('columns nobody asked about are ignored', () => {
  const rows = [
    ['Legacy Id', 'Email', 'Full Name'],
    ['991', 'a@x.io', 'Ada'],
  ];
  assert.deepEqual(mapRows(rows, specs), [{ email: 'a@x.io', name: 'Ada' }]);
});

test('an empty file (no header row) throws', () => {
  assert.throws(() => mapRows([], specs), /header/);
});

test('resolveColumns reports indices, -1 for absent optional columns', () => {
  assert.deepEqual(resolveColumns(['Full Name', 'Email'], specs), [1, 0]);
  assert.deepEqual(resolveColumns(['Email'], specs), [0, -1]);
});
