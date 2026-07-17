import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  applyCode,
  createSession,
  markPaid,
  refundLedger,
  refundedTotal,
  requestRefund,
  setQuantity,
} from './session.ts';
import type { Item } from './session.ts';
import { orderTotalCents } from './totals.ts';

function cart(...items: Item[]): Item[] {
  return items;
}

function gateway(failures = 0) {
  const captured: number[] = [];
  let calls = 0;
  const capture = async (amountCents: number) => {
    calls++;
    if (calls <= failures) throw new Error('card network unavailable');
    captured.push(amountCents);
    return { id: 'rcpt-' + calls };
  };
  return { captured, capture };
}

test('an undiscounted cart totals subtotal plus tax plus flat shipping', () => {
  const s = createSession('ord-1', cart(
    { sku: 'alpha', name: 'Alpha widget', unitCents: 1099, qty: 2 },
    { sku: 'bravo', name: 'Bravo cable', unitCents: 1500, qty: 1 },
  ));
  // subtotal 3698, tax 259, shipping 599
  const total = orderTotalCents(s);
  assert.equal(total, 4556);
  assert.ok(Number.isInteger(total), `total must be whole cents, got ${total}`);
});

test('a percent promo prices the discounted cart in exact whole cents', () => {
  const s = createSession('ord-2', cart(
    { sku: 'alpha', name: 'Alpha widget', unitCents: 1099, qty: 2 },
    { sku: 'bravo', name: 'Bravo cable', unitCents: 1500, qty: 1 },
  ));
  applyCode(s, 'SAVE10');
  // subtotal 3698, minus 370, tax 233, shipping 599
  const total = orderTotalCents(s);
  assert.ok(Number.isInteger(total), `total must be whole cents, got ${total}`);
  assert.equal(total, 4160);
});

test('carts at the free-shipping threshold ship free', () => {
  const s = createSession('ord-3', cart(
    { sku: 'desk', name: 'Desk riser', unitCents: 6000, qty: 1 },
  ));
  assert.equal(orderTotalCents(s), 6420); // 6000 + 420 tax + 0 shipping
});

test('editing a quantity after applying a promo reprices the discount', () => {
  const s = createSession('ord-4', cart(
    { sku: 'kit', name: 'Starter kit', unitCents: 1500, qty: 3 },
  ));
  applyCode(s, 'WELCOME5');
  const before = orderTotalCents(s);
  assert.equal(before, 4879); // 4500 - 500 + 280 tax + 599 shipping
  assert.ok(Number.isInteger(before), `total must be whole cents, got ${before}`);

  setQuantity(s, 'kit', 2); // subtotal 3000: below the promo minimum now
  const after = orderTotalCents(s);
  assert.equal(after, 3809, 'a cart edit must reprice the promo against the new cart');
});

test('a promo below its minimum subtotal grants nothing', () => {
  const s = createSession('ord-5', cart(
    { sku: 'kit', name: 'Starter kit', unitCents: 1500, qty: 2 },
  ));
  applyCode(s, 'WELCOME5'); // minimum is 4000, cart is 3000
  assert.equal(orderTotalCents(s), 3809);
});

test("the same code on a different cart gets that cart's own discount", () => {
  const a = createSession('ord-6a', cart(
    { sku: 'p1', name: 'Pocket light', unitCents: 1099, qty: 1 },
  ));
  applyCode(a, 'SAVE10');
  assert.equal(orderTotalCents(a), 1657); // 1099 - 110 + 69 tax + 599 shipping

  const b = createSession('ord-6b', cart(
    { sku: 'p2', name: 'Bench supply', unitCents: 5000, qty: 1 },
  ));
  applyCode(b, 'SAVE10');
  assert.equal(orderTotalCents(b), 5414, 'ten percent of 5000 is 500, priced per cart');
});

test('unknown promo codes are refused', () => {
  const s = createSession('ord-7', cart(
    { sku: 'p1', name: 'Pocket light', unitCents: 1099, qty: 1 },
  ));
  assert.throws(() => applyCode(s, 'SAVE99'), /unknown promo code/);
});

test('overlapping refunds cannot exceed the amount paid', async () => {
  const s = createSession('ord-8', cart(
    { sku: 'desk', name: 'Desk riser', unitCents: 5000, qty: 1 },
  ));
  markPaid(s, 5000);
  const gw = gateway();
  const results = await Promise.allSettled([
    requestRefund(s, 3000, gw.capture),
    requestRefund(s, 3000, gw.capture),
  ]);
  const fulfilled = results.filter((r) => r.status === 'fulfilled');
  assert.equal(fulfilled.length, 1, 'exactly one of two overlapping 3000-cent refunds may go through on a 5000-cent order');
  assert.deepEqual(gw.captured, [3000], 'the gateway must be asked to move 3000 cents, once');
  assert.deepEqual(refundLedger(s), [3000]);
  assert.equal(refundedTotal(s), 3000);
  for (const r of results) {
    if (r.status === 'rejected') assert.match(String(r.reason), /exceeds/);
  }
});

test('overlapping refunds that fit are both recorded', async () => {
  const s = createSession('ord-9', cart(
    { sku: 'desk', name: 'Desk riser', unitCents: 5000, qty: 1 },
  ));
  markPaid(s, 5000);
  const gw = gateway();
  await Promise.all([
    requestRefund(s, 1000, gw.capture),
    requestRefund(s, 2000, gw.capture),
  ]);
  assert.deepEqual(refundLedger(s), [1000, 2000]);
  assert.equal(refundedTotal(s), 3000);
});

test('a refund of the full remaining balance is the last one allowed', async () => {
  const s = createSession('ord-10', cart(
    { sku: 'desk', name: 'Desk riser', unitCents: 5000, qty: 1 },
  ));
  markPaid(s, 5000);
  const gw = gateway();
  await requestRefund(s, 5000, gw.capture);
  await assert.rejects(() => requestRefund(s, 1, gw.capture), /exceeds/);
  assert.deepEqual(refundLedger(s), [5000]);
});

test('a failed capture leaves the ledger untouched and can be retried', async () => {
  const s = createSession('ord-11', cart(
    { sku: 'desk', name: 'Desk riser', unitCents: 5000, qty: 1 },
  ));
  markPaid(s, 5000);
  const flaky = gateway(1); // first capture attempt fails
  await assert.rejects(() => requestRefund(s, 2000, flaky.capture), /unavailable/);
  assert.deepEqual(refundLedger(s), [], 'nothing may be recorded for a refund the gateway refused');
  assert.equal(refundedTotal(s), 0);
  await requestRefund(s, 2000, flaky.capture);
  assert.deepEqual(refundLedger(s), [2000]);
  assert.deepEqual(flaky.captured, [2000]);
});

test('checkout end to end: promo, payment, refunds', async () => {
  const s = createSession('ord-12', cart(
    { sku: 'alpha', name: 'Alpha widget', unitCents: 1099, qty: 2 },
    { sku: 'bravo', name: 'Bravo cable', unitCents: 1500, qty: 1 },
  ));
  applyCode(s, 'SAVE10');
  const total = orderTotalCents(s);
  assert.equal(total, 4160);
  markPaid(s, total);

  const gw = gateway();
  await requestRefund(s, 1000, gw.capture);
  await requestRefund(s, 2000, gw.capture);
  assert.deepEqual(refundLedger(s), [1000, 2000]);
  assert.equal(refundedTotal(s), 3000);
  await assert.rejects(() => requestRefund(s, 1200, gw.capture), /exceeds/);
  assert.deepEqual(gw.captured, [1000, 2000]);
});
