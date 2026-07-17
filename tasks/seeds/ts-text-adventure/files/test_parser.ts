import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parse } from './parser.ts';

test('empty and whitespace-only input', () => {
  assert.deepEqual(parse(''), { kind: 'empty' });
  assert.deepEqual(parse('   '), { kind: 'empty' });
});

test('bare compass words and their single letters move the player', () => {
  assert.deepEqual(parse('n'), { kind: 'go', direction: 'north' });
  assert.deepEqual(parse('S'), { kind: 'go', direction: 'south' });
  assert.deepEqual(parse('east'), { kind: 'go', direction: 'east' });
  assert.deepEqual(parse('W'), { kind: 'go', direction: 'west' });
  assert.deepEqual(parse('NORTH'), { kind: 'go', direction: 'north' });
});

test('only the eight compass forms get the bare treatment', () => {
  assert.deepEqual(parse('down'), { kind: 'unknown', input: 'down' });
  assert.deepEqual(parse('up'), { kind: 'unknown', input: 'up' });
});

test('go takes an explicit direction', () => {
  assert.deepEqual(parse('go north'), { kind: 'go', direction: 'north' });
  assert.deepEqual(parse('GO   Down '), { kind: 'go', direction: 'down' });
  assert.deepEqual(parse('go'), { kind: 'unknown', input: 'go' });
});

test('look and its alias', () => {
  assert.deepEqual(parse('look'), { kind: 'look' });
  assert.deepEqual(parse(' L '), { kind: 'look' });
});

test('inventory and its aliases', () => {
  assert.deepEqual(parse('inventory'), { kind: 'inventory' });
  assert.deepEqual(parse('inv'), { kind: 'inventory' });
  assert.deepEqual(parse('I'), { kind: 'inventory' });
});

test('take, get and pick up are synonyms and keep multi-word items', () => {
  assert.deepEqual(parse('take lantern'), { kind: 'take', item: 'lantern' });
  assert.deepEqual(parse('get brass key'), { kind: 'take', item: 'brass key' });
  assert.deepEqual(parse('PICK UP dusty  tome'), { kind: 'take', item: 'dusty tome' });
});

test('take without an item is not a command', () => {
  assert.deepEqual(parse('take'), { kind: 'unknown', input: 'take' });
  assert.deepEqual(parse('get  '), { kind: 'unknown', input: 'get' });
});

test('drop', () => {
  assert.deepEqual(parse('drop rope'), { kind: 'drop', item: 'rope' });
  assert.deepEqual(parse('drop'), { kind: 'unknown', input: 'drop' });
});

test('pick needs its up', () => {
  assert.deepEqual(parse('pick the lock'), { kind: 'unknown', input: 'pick the lock' });
});

test('unknown input is echoed back normalized', () => {
  assert.deepEqual(parse('  Frobnicate  the   THING '), {
    kind: 'unknown',
    input: 'frobnicate the thing',
  });
});
