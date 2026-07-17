import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Database } from './db.ts';

test('registers tables and reports names sorted', () => {
  const db = new Database();
  db.table('users', ['id', 'name'], [{ id: 1, name: 'ada' }]);
  db.table('cities', ['id', 'city'], []);
  assert.deepEqual(db.tables(), ['cities', 'users']);
});

test('columns() echoes the declared column order', () => {
  const db = new Database();
  db.table('users', ['id', 'name', 'city'], []);
  assert.deepEqual(db.columns('users'), ['id', 'name', 'city']);
});

test('rowCount() counts ingested rows, empty tables are fine', () => {
  const db = new Database();
  db.table('empty', ['id'], []);
  db.table('two', ['id'], [{ id: 1 }, { id: 2 }]);
  assert.equal(db.rowCount('empty'), 0);
  assert.equal(db.rowCount('two'), 2);
});

test('registering the same table twice is refused by name', () => {
  const db = new Database();
  db.table('users', ['id'], []);
  assert.throws(() => db.table('users', ['id'], []), /users/);
});

test('asking about a table that does not exist names it', () => {
  const db = new Database();
  assert.throws(() => db.columns('nope'), /nope/);
  assert.throws(() => db.rowCount('nope'), /nope/);
});

test('duplicate column declarations are rejected', () => {
  const db = new Database();
  assert.throws(() => db.table('t', ['id', 'id'], []), /id/);
});

test('a row missing a declared column is rejected by column name', () => {
  const db = new Database();
  assert.throws(
    () => db.table('users', ['id', 'city'], [{ id: 1, city: 'Oslo' }, { id: 2 }]),
    /city/,
  );
});

test('a row with an undeclared column is rejected by column name', () => {
  const db = new Database();
  assert.throws(
    () => db.table('users', ['id'], [{ id: 1, zip: '0150' }]),
    /zip/,
  );
});
