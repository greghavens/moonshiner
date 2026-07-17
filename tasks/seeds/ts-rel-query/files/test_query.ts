import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Database } from './db.ts';
import { Query } from './query.ts';

function fixture(): Database {
  const db = new Database();
  db.table('users', ['id', 'name', 'age', 'city'], [
    { id: 1, name: 'ada', age: 36, city: 'Oslo' },
    { id: 2, name: 'bo', age: 28, city: 'Bergen' },
    { id: 3, name: 'cy', age: 36, city: 'Oslo' },
    { id: 4, name: 'di', age: 41, city: 'Tromsø' },
  ]);
  db.table('orders', ['id', 'userId', 'amount'], [
    { id: 100, userId: 1, amount: 50 },
    { id: 101, userId: 2, amount: 75 },
    { id: 102, userId: 1, amount: 20 },
    { id: 103, userId: 9, amount: 999 },
  ]);
  return db;
}

test('a bare query returns every row in table order', () => {
  const rows = new Query(fixture(), 'users').run();
  assert.deepEqual(rows.map((r) => r.id), [1, 2, 3, 4]);
});

test('querying an unknown table names it', () => {
  assert.throws(() => new Query(fixture(), 'ghosts'), /ghosts/);
});

test('results are copies — mutating them never leaks into the table', () => {
  const db = fixture();
  const first = new Query(db, 'users').run()[0];
  first.name = 'HACKED';
  assert.equal(new Query(db, 'users').run()[0].name, 'ada');
});

test('ingested rows are copied too — mutating source objects changes nothing', () => {
  const db = new Database();
  const src = [{ id: 1, v: 'a' }];
  db.table('t', ['id', 'v'], src);
  src[0].v = 'zzz';
  assert.equal(new Query(db, 't').run()[0].v, 'a');
});

test('select projects columns in the order asked', () => {
  const rows = new Query(fixture(), 'users').select('name', 'id').run();
  assert.deepEqual(Object.keys(rows[0]), ['name', 'id']);
  assert.deepEqual(rows[0], { name: 'ada', id: 1 });
});

test('select of an unknown column names it', () => {
  assert.throws(() => new Query(fixture(), 'users').select('salary').run(), /salary/);
});

test('where supports eq ne lt lte gt gte', () => {
  const q = () => new Query(fixture(), 'users');
  assert.deepEqual(q().where('city', 'eq', 'Oslo').run().map((r) => r.id), [1, 3]);
  assert.deepEqual(q().where('city', 'ne', 'Oslo').run().map((r) => r.id), [2, 4]);
  assert.deepEqual(q().where('age', 'lt', 36).run().map((r) => r.id), [2]);
  assert.deepEqual(q().where('age', 'lte', 36).run().map((r) => r.id), [1, 2, 3]);
  assert.deepEqual(q().where('age', 'gt', 36).run().map((r) => r.id), [4]);
  assert.deepEqual(q().where('age', 'gte', 41).run().map((r) => r.id), [4]);
});

test('where in takes an array of candidates', () => {
  const rows = new Query(fixture(), 'users').where('city', 'in', ['Bergen', 'Tromsø']).run();
  assert.deepEqual(rows.map((r) => r.id), [2, 4]);
  assert.throws(() => new Query(fixture(), 'users').where('city', 'in', 'Bergen').run(), /in/);
});

test('stacked where clauses AND together', () => {
  const rows = new Query(fixture(), 'users')
    .where('city', 'eq', 'Oslo')
    .where('age', 'gte', 36)
    .where('name', 'ne', 'cy')
    .run();
  assert.deepEqual(rows.map((r) => r.id), [1]);
});

test('where rejects unknown operators and unknown fields by name', () => {
  assert.throws(() => new Query(fixture(), 'users').where('name', 'like', 'a%').run(), /like/);
  assert.throws(() => new Query(fixture(), 'users').where('salary', 'eq', 1).run(), /salary/);
});

test('where sees the full row even when select drops the column', () => {
  const rows = new Query(fixture(), 'users').select('name').where('age', 'gt', 30).run();
  assert.deepEqual(rows, [{ name: 'ada' }, { name: 'cy' }, { name: 'di' }]);
});

test('orderBy sorts asc by default, desc on request', () => {
  const q = () => new Query(fixture(), 'users');
  assert.deepEqual(q().orderBy('age').run().map((r) => r.id), [2, 1, 3, 4]);
  assert.deepEqual(q().orderBy('age', 'desc').run().map((r) => r.id), [4, 1, 3, 2]);
});

test('orderBy is stable and later calls break ties', () => {
  // ada and cy tie on age; stable sort keeps table order
  const stable = new Query(fixture(), 'users').orderBy('age').run();
  assert.deepEqual(stable.map((r) => r.name), ['bo', 'ada', 'cy', 'di']);
  const tied = new Query(fixture(), 'users').orderBy('age', 'desc').orderBy('name', 'desc').run();
  assert.deepEqual(tied.map((r) => r.name), ['di', 'cy', 'ada', 'bo']);
});

test('orderBy rejects bad directions and unknown fields', () => {
  assert.throws(() => new Query(fixture(), 'users').orderBy('age', 'sideways' as never).run(), /sideways/);
  assert.throws(() => new Query(fixture(), 'users').orderBy('salary').run(), /salary/);
});

test('offset skips before limit trims', () => {
  const rows = new Query(fixture(), 'users').orderBy('id').offset(1).limit(2).run();
  assert.deepEqual(rows.map((r) => r.id), [2, 3]);
});

test('limit past the end and offset past the end are harmless', () => {
  assert.equal(new Query(fixture(), 'users').limit(99).run().length, 4);
  assert.deepEqual(new Query(fixture(), 'users').offset(99).run(), []);
});

test('limit and offset must be non-negative integers', () => {
  assert.throws(() => new Query(fixture(), 'users').limit(-1), /limit/);
  assert.throws(() => new Query(fixture(), 'users').limit(1.5), /limit/);
  assert.throws(() => new Query(fixture(), 'users').offset(-2), /offset/);
});

test('inner join keeps only matching pairs and merges columns', () => {
  const rows = new Query(fixture(), 'orders').join('users', 'userId', 'id').orderBy('id').run();
  // order 103 has no user; user 3 and 4 have no orders
  assert.equal(rows.length, 3);
  assert.deepEqual(rows.map((r) => r.id), [100, 101, 102]);
  assert.equal(rows[0].name, 'ada');
  assert.equal(rows[1].name, 'bo');
});

test('one row on the left matches many on the right and vice versa', () => {
  const rows = new Query(fixture(), 'users').join('orders', 'id', 'userId').run();
  // ada has two orders, bo one, cy/di none
  assert.equal(rows.length, 3);
  assert.deepEqual(rows.filter((r) => r.name === 'ada').map((r) => r.amount).sort(), [20, 50]);
});

test('colliding right-hand columns are prefixed with their table name', () => {
  const rows = new Query(fixture(), 'orders').join('users', 'userId', 'id').orderBy('id').run();
  assert.equal(rows[0].id, 100);
  assert.equal(rows[0]['users.id'], 1);
});

test('joined columns are queryable under their prefixed names', () => {
  const rows = new Query(fixture(), 'orders')
    .join('users', 'userId', 'id')
    .where('city', 'eq', 'Oslo')
    .select('users.id', 'amount')
    .orderBy('amount')
    .run();
  assert.deepEqual(rows, [{ 'users.id': 1, amount: 20 }, { 'users.id': 1, amount: 50 }]);
});

test('join validates the table and both key fields', () => {
  assert.throws(() => new Query(fixture(), 'orders').join('ghosts', 'userId', 'id').run(), /ghosts/);
  assert.throws(() => new Query(fixture(), 'orders').join('users', 'buyer', 'id').run(), /buyer/);
  assert.throws(() => new Query(fixture(), 'orders').join('users', 'userId', 'uid').run(), /uid/);
});
