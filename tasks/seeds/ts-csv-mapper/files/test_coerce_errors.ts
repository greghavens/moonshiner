import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mapRowsSafe } from './colmap.ts';

const specs = [
  { header: 'Email', key: 'email', required: true },
  { header: 'Age', key: 'age', type: 'number' },
  { header: 'Active', key: 'active', type: 'boolean' },
  { header: 'Joined', key: 'joined', type: 'date' },
];

test('coerces number, boolean, and date columns', () => {
  const { rows, errors } = mapRowsSafe(
    [
      ['Email', 'Age', 'Active', 'Joined'],
      ['a@x.io', '42', 'yes', '2023-04-01'],
      ['b@x.io', ' 3.5 ', 'FALSE', '2024-12-31'],
    ],
    specs,
  );
  assert.deepEqual(errors, []);
  assert.deepEqual(rows, [
    { email: 'a@x.io', age: 42, active: true, joined: new Date('2023-04-01') },
    { email: 'b@x.io', age: 3.5, active: false, joined: new Date('2024-12-31') },
  ]);
});

test('boolean accepts true/false, yes/no, 1/0 in any case', () => {
  const { rows, errors } = mapRowsSafe(
    [
      ['Email', 'Active'],
      ['a@x.io', 'True'],
      ['b@x.io', 'NO'],
      ['c@x.io', '1'],
      ['d@x.io', '0'],
    ],
    specs,
  );
  assert.deepEqual(errors, []);
  assert.deepEqual(rows.map((r: Record<string, unknown>) => r.active), [true, false, true, false]);
});

test('a bad cell yields an error carrying the file line and header, and drops the row', () => {
  const { rows, errors } = mapRowsSafe(
    [
      ['Email', 'Age'],
      ['a@x.io', '30'],
      ['b@x.io', 'thirty'],
      ['c@x.io', '31'],
    ],
    specs,
  );
  assert.deepEqual(rows, [
    { email: 'a@x.io', age: 30 },
    { email: 'c@x.io', age: 31 },
  ]);
  assert.equal(errors.length, 1);
  assert.equal(errors[0].line, 3); // header is line 1
  assert.equal(errors[0].column, 'Age');
  assert.match(errors[0].message, /number/i);
});

test('every bad cell in a row is reported, in spec order', () => {
  const { rows, errors } = mapRowsSafe(
    [
      ['Email', 'Age', 'Active'],
      ['a@x.io', 'NaNsense', 'maybe'],
    ],
    specs,
  );
  assert.deepEqual(rows, []);
  assert.equal(errors.length, 2);
  assert.deepEqual(errors.map((e: { column: string }) => e.column), ['Age', 'Active']);
  assert.equal(errors[0].line, 2);
  assert.equal(errors[1].line, 2);
});

test('dates must be real YYYY-MM-DD calendar dates', () => {
  const { errors } = mapRowsSafe(
    [
      ['Email', 'Joined'],
      ['a@x.io', '2024-02-30'],
      ['b@x.io', '04/01/2023'],
      ['c@x.io', '2024-02-29'],
    ],
    specs,
  );
  assert.deepEqual(errors.map((e: { line: number }) => e.line), [2, 3]);
  assert.match(errors[0].message, /date/i);
});

test('an empty cell in a required column is an error; in an optional one the key is omitted', () => {
  const { rows, errors } = mapRowsSafe(
    [
      ['Email', 'Age'],
      ['', '30'],
      ['b@x.io', ''],
    ],
    specs,
  );
  assert.equal(errors.length, 1);
  assert.equal(errors[0].line, 2);
  assert.equal(errors[0].column, 'Email');
  assert.match(errors[0].message, /missing|required/i);
  assert.deepEqual(rows, [{ email: 'b@x.io' }]);
});

test('untyped columns pass through as strings', () => {
  const { rows } = mapRowsSafe(
    [
      ['Email', 'Full Name'],
      ['a@x.io', 'Ada'],
    ],
    [
      { header: 'Email', key: 'email', required: true },
      { header: 'Full Name', key: 'name' },
    ],
  );
  assert.deepEqual(rows, [{ email: 'a@x.io', name: 'Ada' }]);
});

test('required-column-missing-from-header still throws, like mapRows', () => {
  assert.throws(() => mapRowsSafe([['Age'], ['30']], specs), /Email/);
});
