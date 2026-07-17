import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Inventory } from './inventory.ts';

test('unknown skus read as zero', () => {
  const inv = new Inventory();
  assert.equal(inv.onHand('widget'), 0);
  assert.equal(inv.available('widget'), 0);
});

test('receiving stock raises on-hand and available together', () => {
  const inv = new Inventory();
  inv.receive('widget', 5);
  inv.receive('widget', 3);
  assert.equal(inv.onHand('widget'), 8);
  assert.equal(inv.available('widget'), 8);
});

test('reserving holds stock without removing it', () => {
  const inv = new Inventory();
  inv.receive('widget', 5);
  inv.reserve('widget', 3);
  assert.equal(inv.onHand('widget'), 5);
  assert.equal(inv.available('widget'), 2);
});

test('reserve is strict: more than available is refused, naming the sku', () => {
  const inv = new Inventory();
  inv.receive('widget', 2);
  assert.throws(() => inv.reserve('widget', 3), /widget/);
  assert.equal(inv.available('widget'), 2);
});

test('reserveUpTo takes what it can and reports how much', () => {
  const inv = new Inventory();
  inv.receive('widget', 2);
  assert.equal(inv.reserveUpTo('widget', 5), 2);
  assert.equal(inv.available('widget'), 0);
  assert.equal(inv.reserveUpTo('widget', 1), 0);
});

test('release puts held stock back; over-release is refused', () => {
  const inv = new Inventory();
  inv.receive('widget', 5);
  inv.reserve('widget', 4);
  inv.release('widget', 3);
  assert.equal(inv.available('widget'), 4);
  assert.throws(() => inv.release('widget', 2), /widget/);
});

test('commit ships held stock: on-hand drops, reservation is consumed', () => {
  const inv = new Inventory();
  inv.receive('widget', 5);
  inv.reserve('widget', 3);
  inv.commit('widget', 3);
  assert.equal(inv.onHand('widget'), 2);
  assert.equal(inv.available('widget'), 2);
  assert.throws(() => inv.commit('widget', 1), /widget/);
});

test('quantities must be positive integers everywhere', () => {
  const inv = new Inventory();
  inv.receive('widget', 5);
  for (const bad of [0, -1, 1.5]) {
    assert.throws(() => inv.receive('widget', bad), /quantity|qty/i);
    assert.throws(() => inv.reserve('widget', bad), /quantity|qty/i);
    assert.throws(() => inv.reserveUpTo('widget', bad), /quantity|qty/i);
    assert.throws(() => inv.release('widget', bad), /quantity|qty/i);
    assert.throws(() => inv.commit('widget', bad), /quantity|qty/i);
  }
});
