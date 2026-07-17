import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cartTotalCents, formatUsd, lineTotalCents, parsePriceCents } from './cart.ts';

test('parses well-formed prices to cents', () => {
  assert.equal(parsePriceCents('12.50'), 1250);
  assert.equal(parsePriceCents('0.99'), 99);
  assert.equal(parsePriceCents('0'), 0);
});

test('rejects malformed prices outright', () => {
  assert.throws(() => parsePriceCents('n/a'), RangeError);
  assert.throws(() => parsePriceCents(''), RangeError);
  assert.throws(() => parsePriceCents('-4.00'), RangeError);
});

test('a line saved for later (quantity 0) costs nothing', () => {
  const total = cartTotalCents([
    { sku: 'PEN-01', unitCents: 300, quantity: 0 },
    { sku: 'INK-02', unitCents: 1200, quantity: 2 },
  ]);
  assert.equal(total, 2400);
});

test('omitted quantity means one unit', () => {
  assert.equal(lineTotalCents({ sku: 'PAD-03', unitCents: 450 }), 450);
});

test('discounts apply per line', () => {
  assert.equal(
    lineTotalCents({ sku: 'PEN-01', unitCents: 1000, quantity: 3, discountPct: 25 }),
    2250,
  );
  assert.equal(
    lineTotalCents({ sku: 'PEN-01', unitCents: 1000, quantity: 2, discountPct: 0 }),
    2000,
  );
});

test('a cart built from parsed feed prices totals cleanly', () => {
  const feedPrices = ['4.00', '15.25'];
  const items = feedPrices.map((price, i) => ({
    sku: `FEED-${i}`,
    unitCents: parsePriceCents(price),
  }));
  assert.equal(formatUsd(cartTotalCents(items)), '$19.25');
});
