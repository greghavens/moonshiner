import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Database } from './db.ts';
import { Query } from './query.ts';

function fixture(): Database {
  const db = new Database();
  db.table('sales', ['id', 'region', 'product', 'amount'], [
    { id: 1, region: 'north', product: 'anvil', amount: 120 },
    { id: 2, region: 'south', product: 'rope', amount: 30 },
    { id: 3, region: 'north', product: 'rope', amount: 60 },
    { id: 4, region: 'south', product: 'anvil', amount: 90 },
    { id: 5, region: 'north', product: 'anvil', amount: 20 },
  ]);
  return db;
}

test('groupBy + count/sum, groups in first-seen order', () => {
  const rows = new Query(fixture(), 'sales')
    .groupBy('region')
    .agg('n', 'count')
    .agg('total', 'sum', 'amount')
    .run();
  assert.deepEqual(rows, [
    { region: 'north', n: 3, total: 200 },
    { region: 'south', n: 2, total: 120 },
  ]);
});

test('avg, min and max', () => {
  const rows = new Query(fixture(), 'sales')
    .groupBy('region')
    .agg('mean', 'avg', 'amount')
    .agg('lo', 'min', 'amount')
    .agg('hi', 'max', 'amount')
    .run();
  assert.deepEqual(rows, [
    { region: 'north', mean: 200 / 3, lo: 20, hi: 120 },
    { region: 'south', mean: 60, lo: 30, hi: 90 },
  ]);
});

test('min and max also work on strings', () => {
  const rows = new Query(fixture(), 'sales')
    .groupBy('region')
    .agg('first', 'min', 'product')
    .run();
  assert.deepEqual(rows, [
    { region: 'north', first: 'anvil' },
    { region: 'south', first: 'anvil' },
  ]);
});

test('grouping by two fields makes one row per combination', () => {
  const rows = new Query(fixture(), 'sales')
    .groupBy('region', 'product')
    .agg('total', 'sum', 'amount')
    .run();
  assert.deepEqual(rows, [
    { region: 'north', product: 'anvil', total: 140 },
    { region: 'south', product: 'rope', total: 30 },
    { region: 'north', product: 'rope', total: 60 },
    { region: 'south', product: 'anvil', total: 90 },
  ]);
});

test('where filters the rows before grouping sees them', () => {
  const rows = new Query(fixture(), 'sales')
    .where('product', 'eq', 'anvil')
    .groupBy('region')
    .agg('total', 'sum', 'amount')
    .run();
  assert.deepEqual(rows, [
    { region: 'north', total: 140 },
    { region: 'south', total: 90 },
  ]);
});

test('having filters the aggregated rows', () => {
  const rows = new Query(fixture(), 'sales')
    .groupBy('region')
    .agg('total', 'sum', 'amount')
    .having('total', 'gt', 150)
    .run();
  assert.deepEqual(rows, [{ region: 'north', total: 200 }]);
});

test('orderBy and select apply to the aggregated output', () => {
  const rows = new Query(fixture(), 'sales')
    .groupBy('product')
    .agg('total', 'sum', 'amount')
    .orderBy('total', 'desc')
    .select('product')
    .run();
  assert.deepEqual(rows, [{ product: 'anvil' }, { product: 'rope' }]);
});

test('grouping an empty result set yields no rows', () => {
  const rows = new Query(fixture(), 'sales')
    .where('amount', 'gt', 10_000)
    .groupBy('region')
    .agg('n', 'count')
    .run();
  assert.deepEqual(rows, []);
});

test('grouped queries also work after a join', () => {
  const db = fixture();
  db.table('reps', ['region', 'rep'], [
    { region: 'north', rep: 'nils' },
    { region: 'south', rep: 'siri' },
  ]);
  const rows = new Query(db, 'sales')
    .join('reps', 'region', 'region')
    .groupBy('rep')
    .agg('total', 'sum', 'amount')
    .run();
  assert.deepEqual(rows, [
    { rep: 'nils', total: 200 },
    { rep: 'siri', total: 120 },
  ]);
});

test('sum and avg over a non-numeric column name the column', () => {
  const q = new Query(fixture(), 'sales').groupBy('region').agg('t', 'sum', 'product');
  assert.throws(() => q.run(), /product/);
});

test('aggregating an unknown field or function is refused by name', () => {
  assert.throws(
    () => new Query(fixture(), 'sales').groupBy('region').agg('t', 'sum', 'weight').run(),
    /weight/,
  );
  assert.throws(
    () => new Query(fixture(), 'sales').groupBy('region').agg('t', 'median' as never, 'amount').run(),
    /median/,
  );
});

test('aliases may not collide with group fields or each other', () => {
  assert.throws(
    () => new Query(fixture(), 'sales').groupBy('region').agg('region', 'count').run(),
    /region/,
  );
  assert.throws(
    () =>
      new Query(fixture(), 'sales')
        .groupBy('region')
        .agg('t', 'count')
        .agg('t', 'sum', 'amount')
        .run(),
    /t/,
  );
});

test('selecting a bare (non-grouped, non-alias) column from a grouped query fails', () => {
  assert.throws(
    () => new Query(fixture(), 'sales').groupBy('region').agg('n', 'count').select('amount').run(),
    /amount/,
  );
});

test('groupBy of an unknown field is refused by name', () => {
  assert.throws(() => new Query(fixture(), 'sales').groupBy('planet').agg('n', 'count').run(), /planet/);
});
