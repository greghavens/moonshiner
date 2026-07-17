import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Inventory } from './inventory.ts';
import { OrderEngine } from './orders.ts';

test('the audit log tells the whole story in order, seq from 1', () => {
  const eng = new OrderEngine(new Inventory());
  eng.receive('widget', 3);
  eng.place({ id: 'o1', lines: [{ sku: 'widget', qty: 5 }] });
  eng.ship('o1');
  eng.receive('widget', 1);
  eng.cancel('o1');
  assert.deepEqual(eng.audit(), [
    { seq: 1, type: 'stock-received', sku: 'widget', qty: 3 },
    { seq: 2, type: 'order-placed', order: 'o1' },
    { seq: 3, type: 'reserved', order: 'o1', sku: 'widget', qty: 3 },
    { seq: 4, type: 'backordered', order: 'o1', sku: 'widget', qty: 2 },
    { seq: 5, type: 'shipped', order: 'o1', lines: [{ sku: 'widget', qty: 3 }] },
    { seq: 6, type: 'stock-received', sku: 'widget', qty: 1 },
    { seq: 7, type: 'reserved', order: 'o1', sku: 'widget', qty: 1 },
    { seq: 8, type: 'cancelled', order: 'o1', released: [{ sku: 'widget', qty: 1 }] },
  ]);
});

test('a fully covered line emits no backordered entry; an empty reservation emits no reserved entry', () => {
  const eng = new OrderEngine(new Inventory());
  eng.receive('widget', 5);
  eng.place({ id: 'o1', lines: [{ sku: 'widget', qty: 2 }] });
  eng.place({ id: 'o2', lines: [{ sku: 'gizmo', qty: 1 }] });
  assert.deepEqual(
    eng.audit().map((e) => e.type),
    ['stock-received', 'order-placed', 'reserved', 'order-placed', 'backordered'],
  );
});

test('rejected operations leave no trace', () => {
  const eng = new OrderEngine(new Inventory());
  eng.receive('widget', 5);
  eng.place({ id: 'o1', lines: [{ sku: 'widget', qty: 1 }] });
  const before = eng.audit().length;
  assert.throws(() => eng.place({ id: 'o1', lines: [{ sku: 'widget', qty: 1 }] }));
  assert.throws(() => eng.place({ id: 'o2', lines: [] }));
  assert.throws(() => eng.cancel('ghost'));
  assert.equal(eng.audit().length, before);
});

test('cancellation logs the release, then the reallocations it triggered', () => {
  const eng = new OrderEngine(new Inventory());
  eng.receive('widget', 2);
  eng.place({ id: 'o1', lines: [{ sku: 'widget', qty: 2 }] });
  eng.place({ id: 'o2', lines: [{ sku: 'widget', qty: 3 }] });
  eng.cancel('o1');
  const tail = eng.audit().slice(-2);
  assert.deepEqual(tail[0], {
    seq: tail[0].seq,
    type: 'cancelled',
    order: 'o1',
    released: [{ sku: 'widget', qty: 2 }],
  });
  assert.deepEqual(tail[1], {
    seq: tail[0].seq + 1,
    type: 'reserved',
    order: 'o2',
    sku: 'widget',
    qty: 2,
  });
});

test('the audit log hands out copies', () => {
  const eng = new OrderEngine(new Inventory());
  eng.receive('widget', 1);
  const log = eng.audit();
  log.pop();
  assert.equal(eng.audit().length, 1);
  const entry = eng.audit()[0] as { qty: number };
  entry.qty = 99;
  assert.equal((eng.audit()[0] as { qty: number }).qty, 1);
});
