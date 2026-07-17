import { test } from 'node:test';
import assert from 'node:assert/strict';
import { computeTotals } from './totals.ts';

const config = { taxRate: 0.08, shippingFlatCents: 500 };

test('sums line items and applies tax plus flat shipping', () => {
  const totals = computeTotals(
    {
      items: [
        { sku: 'MUG-01', category: 'kitchen', unitPriceCents: 1000, quantity: 2 },
        { sku: 'PEN-07', category: 'office', unitPriceCents: 550, quantity: 1 },
      ],
    },
    config,
  );
  assert.deepEqual(totals, {
    subtotalCents: 2550,
    discountCents: 0,
    taxCents: 204,
    shippingCents: 500,
    totalCents: 3254,
  });
});

test('discount comes off before tax is computed', () => {
  const totals = computeTotals(
    {
      items: [{ sku: 'KB-11', category: 'electronics', unitPriceCents: 2000, quantity: 1 }],
      discountPercent: 10,
    },
    { taxRate: 0.1, shippingFlatCents: 300 },
  );
  assert.equal(totals.discountCents, 200);
  assert.equal(totals.taxCents, 180);
  assert.equal(totals.totalCents, 2280);
});

test('an empty order is all zeros, including shipping', () => {
  const totals = computeTotals({ items: [] }, config);
  assert.deepEqual(totals, {
    subtotalCents: 0,
    discountCents: 0,
    taxCents: 0,
    shippingCents: 0,
    totalCents: 0,
  });
});

test('tax cents are rounded, not truncated', () => {
  const totals = computeTotals(
    { items: [{ sku: 'X', category: 'misc', unitPriceCents: 333, quantity: 1 }] },
    { taxRate: 0.07, shippingFlatCents: 0 },
  );
  assert.equal(totals.taxCents, 23); // 23.31 -> 23
});

test('rejects bad quantities, prices, and discount percents', () => {
  const item = { sku: 'X', category: 'misc', unitPriceCents: 100, quantity: 1 };
  assert.throws(
    () => computeTotals({ items: [{ ...item, quantity: 0 }] }, config),
    RangeError,
  );
  assert.throws(
    () => computeTotals({ items: [{ ...item, quantity: 1.5 }] }, config),
    RangeError,
  );
  assert.throws(
    () => computeTotals({ items: [{ ...item, unitPriceCents: -5 }] }, config),
    RangeError,
  );
  assert.throws(
    () => computeTotals({ items: [item], discountPercent: 150 }, config),
    RangeError,
  );
});
