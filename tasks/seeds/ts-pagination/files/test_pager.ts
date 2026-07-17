import { test } from 'node:test';
import assert from 'node:assert/strict';
import { paginate, pageCount } from './pager.ts';

const items = ['a', 'b', 'c', 'd', 'e', 'f', 'g'];

test('full first page', () => {
  assert.deepEqual(paginate(items, 0, 3), ['a', 'b', 'c']);
});

test('middle page keeps its last row', () => {
  assert.deepEqual(paginate(items, 1, 3), ['d', 'e', 'f']);
});

test('final partial page', () => {
  assert.deepEqual(paginate(items, 2, 3), ['g']);
});

test('page count includes the partial page', () => {
  assert.equal(pageCount(7, 3), 3);
});

test('page count on an exact multiple', () => {
  assert.equal(pageCount(6, 3), 2);
});

test('page count of an empty set', () => {
  assert.equal(pageCount(0, 3), 0);
});
