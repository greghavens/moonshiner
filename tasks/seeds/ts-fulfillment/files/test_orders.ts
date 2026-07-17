import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Inventory } from './inventory.ts';
import { OrderEngine } from './orders.ts';

function setup(stock: Record<string, number> = {}) {
  const inv = new Inventory();
  const eng = new OrderEngine(inv);
  for (const [sku, qty] of Object.entries(stock)) eng.receive(sku, qty);
  return { inv, eng };
}

test('a fully stocked order reserves everything and sits open', () => {
  const { inv, eng } = setup({ widget: 10 });
  eng.place({ id: 'o1', lines: [{ sku: 'widget', qty: 4 }] });
  assert.deepEqual(eng.order('o1'), {
    id: 'o1',
    status: 'open',
    lines: [{ sku: 'widget', ordered: 4, reserved: 4, shipped: 0, backordered: 0 }],
  });
  assert.equal(inv.available('widget'), 6);
});

test('a short line splits into reserved and backordered', () => {
  const { eng } = setup({ widget: 3 });
  eng.place({ id: 'o1', lines: [{ sku: 'widget', qty: 5 }] });
  assert.deepEqual(eng.order('o1').lines, [
    { sku: 'widget', ordered: 5, reserved: 3, shipped: 0, backordered: 2 },
  ]);
});

test('no stock at all means a fully backordered line', () => {
  const { eng } = setup();
  eng.place({ id: 'o1', lines: [{ sku: 'gizmo', qty: 2 }] });
  assert.deepEqual(eng.order('o1').lines, [
    { sku: 'gizmo', ordered: 2, reserved: 0, shipped: 0, backordered: 2 },
  ]);
});

test('bad orders are rejected by name', () => {
  const { eng } = setup({ widget: 10 });
  eng.place({ id: 'o1', lines: [{ sku: 'widget', qty: 1 }] });
  assert.throws(() => eng.place({ id: 'o1', lines: [{ sku: 'widget', qty: 1 }] }), /o1/);
  assert.throws(() => eng.place({ id: 'o2', lines: [] }), /line/i);
  assert.throws(
    () =>
      eng.place({
        id: 'o3',
        lines: [
          { sku: 'widget', qty: 1 },
          { sku: 'widget', qty: 2 },
        ],
      }),
    /widget/,
  );
  assert.throws(() => eng.place({ id: 'o4', lines: [{ sku: 'widget', qty: 0 }] }), /quantity|qty/i);
  assert.throws(() => eng.order('ghost'), /ghost/);
  assert.throws(() => eng.ship('ghost'), /ghost/);
  assert.throws(() => eng.cancel('ghost'), /ghost/);
});

test('ship sends whatever is reserved and leaves the rest on backorder', () => {
  const { inv, eng } = setup({ widget: 3, gizmo: 1 });
  eng.place({
    id: 'o1',
    lines: [
      { sku: 'widget', qty: 5 },
      { sku: 'gizmo', qty: 1 },
    ],
  });
  const shipment = eng.ship('o1');
  assert.deepEqual(shipment, {
    order: 'o1',
    lines: [
      { sku: 'widget', qty: 3 },
      { sku: 'gizmo', qty: 1 },
    ],
  });
  assert.equal(eng.order('o1').status, 'partial');
  assert.deepEqual(eng.order('o1').lines[0], {
    sku: 'widget',
    ordered: 5,
    reserved: 0,
    shipped: 3,
    backordered: 2,
  });
  assert.equal(inv.onHand('widget'), 0);
});

test('shipping with nothing reserved is an error', () => {
  const { eng } = setup({ widget: 3 });
  eng.place({ id: 'o1', lines: [{ sku: 'widget', qty: 5 }] });
  eng.ship('o1');
  assert.throws(() => eng.ship('o1'), /nothing/i);
});

test('arriving stock fills backorders oldest order first', () => {
  const { eng } = setup({ widget: 2 });
  eng.place({ id: 'o1', lines: [{ sku: 'widget', qty: 5 }] });
  eng.place({ id: 'o2', lines: [{ sku: 'widget', qty: 4 }] });
  eng.receive('widget', 5);
  assert.deepEqual(eng.order('o1').lines[0], {
    sku: 'widget',
    ordered: 5,
    reserved: 5,
    shipped: 0,
    backordered: 0,
  });
  assert.deepEqual(eng.order('o2').lines[0], {
    sku: 'widget',
    ordered: 4,
    reserved: 2,
    shipped: 0,
    backordered: 2,
  });
});

test('an order becomes fulfilled once every unit has shipped', () => {
  const { eng } = setup({ widget: 3 });
  eng.place({ id: 'o1', lines: [{ sku: 'widget', qty: 5 }] });
  eng.ship('o1');
  eng.receive('widget', 10);
  eng.ship('o1');
  assert.equal(eng.order('o1').status, 'fulfilled');
  assert.deepEqual(eng.order('o1').lines[0], {
    sku: 'widget',
    ordered: 5,
    reserved: 0,
    shipped: 5,
    backordered: 0,
  });
});

test('cancel releases holds, drops backorders and sticks', () => {
  const { inv, eng } = setup({ widget: 3 });
  eng.place({ id: 'o1', lines: [{ sku: 'widget', qty: 5 }] });
  eng.cancel('o1');
  assert.equal(eng.order('o1').status, 'cancelled');
  assert.equal(inv.available('widget'), 3);
  eng.receive('widget', 5);
  assert.deepEqual(eng.order('o1').lines[0], {
    sku: 'widget',
    ordered: 5,
    reserved: 0,
    shipped: 0,
    backordered: 0,
  });
  assert.throws(() => eng.cancel('o1'), /o1/);
  assert.throws(() => eng.ship('o1'), /o1/);
});

test('cancelling one order hands its stock to waiting backorders immediately', () => {
  const { eng } = setup({ widget: 2 });
  eng.place({ id: 'o1', lines: [{ sku: 'widget', qty: 2 }] });
  eng.place({ id: 'o2', lines: [{ sku: 'widget', qty: 3 }] });
  eng.cancel('o1');
  assert.deepEqual(eng.order('o2').lines[0], {
    sku: 'widget',
    ordered: 3,
    reserved: 2,
    shipped: 0,
    backordered: 1,
  });
});

test('a fully shipped order cannot be cancelled', () => {
  const { eng } = setup({ widget: 5 });
  eng.place({ id: 'o1', lines: [{ sku: 'widget', qty: 5 }] });
  eng.ship('o1');
  assert.throws(() => eng.cancel('o1'), /o1/);
});

test('a partially shipped order can still be cancelled; shipped units stay shipped', () => {
  const { inv, eng } = setup({ widget: 2 });
  eng.place({ id: 'o1', lines: [{ sku: 'widget', qty: 5 }] });
  eng.ship('o1');
  eng.cancel('o1');
  assert.equal(eng.order('o1').status, 'cancelled');
  assert.equal(eng.order('o1').lines[0].shipped, 2);
  assert.equal(inv.onHand('widget'), 0);
});

test('order snapshots are copies', () => {
  const { eng } = setup({ widget: 5 });
  eng.place({ id: 'o1', lines: [{ sku: 'widget', qty: 2 }] });
  const snap = eng.order('o1');
  snap.status = 'fulfilled';
  snap.lines[0].reserved = 99;
  assert.equal(eng.order('o1').status, 'open');
  assert.equal(eng.order('o1').lines[0].reserved, 2);
});
