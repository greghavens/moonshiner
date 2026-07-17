import { test } from 'node:test';
import assert from 'node:assert/strict';
import { computeTotals } from './totals.ts';

const tiers = [
  { minSubtotalCents: 0, costCents: 599 },
  { minSubtotalCents: 2500, costCents: 399 },
  { minSubtotalCents: 7500, costCents: 0 },
];

function cart(cents: number, category = 'misc') {
  return { items: [{ sku: 'X', category, unitPriceCents: cents, quantity: 1 }] };
}

// --- tiered shipping ---

test('picks the tier whose minimum the discounted subtotal reaches', () => {
  const config = { taxRate: 0, shippingFlatCents: 999, shippingTiers: tiers };
  assert.equal(computeTotals(cart(1000), config).shippingCents, 599);
  assert.equal(computeTotals(cart(2499), config).shippingCents, 599);
  assert.equal(computeTotals(cart(2500), config).shippingCents, 399); // boundary is inclusive
  assert.equal(computeTotals(cart(7499), config).shippingCents, 399);
  assert.equal(computeTotals(cart(7500), config).shippingCents, 0);
  assert.equal(computeTotals(cart(20000), config).shippingCents, 0);
});

test('tier order in the config must not matter', () => {
  const shuffled = [tiers[2], tiers[0], tiers[1]];
  const config = { taxRate: 0, shippingFlatCents: 999, shippingTiers: shuffled };
  assert.equal(computeTotals(cart(2600), config).shippingCents, 399);
  assert.equal(computeTotals(cart(100), config).shippingCents, 599);
});

test('the tier is chosen from the subtotal AFTER discount', () => {
  const config = { taxRate: 0, shippingFlatCents: 999, shippingTiers: tiers };
  const order = { ...cart(2600), discountPercent: 10 }; // 2600 -> 2340
  assert.equal(computeTotals(order, config).shippingCents, 599);
});

test('tiers without a zero-minimum tier are rejected', () => {
  const config = {
    taxRate: 0,
    shippingFlatCents: 0,
    shippingTiers: [{ minSubtotalCents: 1000, costCents: 100 }],
  };
  assert.throws(() => computeTotals(cart(50), config));
});

test('two tiers with the same minimum are rejected', () => {
  const config = {
    taxRate: 0,
    shippingFlatCents: 0,
    shippingTiers: [
      { minSubtotalCents: 0, costCents: 100 },
      { minSubtotalCents: 0, costCents: 200 },
    ],
  };
  assert.throws(() => computeTotals(cart(50), config));
});

test('an empty order ships free even with tiers configured', () => {
  const config = { taxRate: 0, shippingFlatCents: 999, shippingTiers: tiers };
  assert.equal(computeTotals({ items: [] }, config).shippingCents, 0);
});

test('flat shipping still applies when no tiers are configured', () => {
  const config = { taxRate: 0, shippingFlatCents: 450 };
  assert.equal(computeTotals(cart(100), config).shippingCents, 450);
});

// --- tax-exempt categories ---

test('items in exempt categories are excluded from the tax base', () => {
  const config = { taxRate: 0.1, shippingFlatCents: 0, taxExemptCategories: ['books'] };
  const totals = computeTotals(
    {
      items: [
        { sku: 'BK-1', category: 'books', unitPriceCents: 1200, quantity: 1 },
        { sku: 'EL-1', category: 'electronics', unitPriceCents: 1000, quantity: 1 },
      ],
    },
    config,
  );
  assert.equal(totals.taxCents, 100);
  assert.equal(totals.totalCents, 2300);
});

test('a fully exempt cart pays no tax at all', () => {
  const config = { taxRate: 0.2, shippingFlatCents: 0, taxExemptCategories: ['books', 'food'] };
  const totals = computeTotals(
    {
      items: [
        { sku: 'BK-1', category: 'books', unitPriceCents: 900, quantity: 2 },
        { sku: 'FD-1', category: 'food', unitPriceCents: 350, quantity: 1 },
      ],
    },
    config,
  );
  assert.equal(totals.taxCents, 0);
});

test('the discount is prorated so exempt items do not shrink the tax base twice', () => {
  const config = { taxRate: 0.1, shippingFlatCents: 0, taxExemptCategories: ['books'] };
  const totals = computeTotals(
    {
      items: [
        { sku: 'BK-1', category: 'books', unitPriceCents: 1200, quantity: 1 },
        { sku: 'EL-1', category: 'electronics', unitPriceCents: 1000, quantity: 1 },
      ],
      discountPercent: 10,
    },
    config,
  );
  // subtotal 2200, discount 220; taxable share 1000 - round(220 * 1000/2200) = 900
  assert.equal(totals.discountCents, 220);
  assert.equal(totals.taxCents, 90);
  assert.equal(totals.totalCents, 2070);
});

test('tiers and exemptions compose in one order', () => {
  const config = {
    taxRate: 0.08,
    shippingFlatCents: 999,
    shippingTiers: tiers,
    taxExemptCategories: ['food'],
  };
  const totals = computeTotals(
    {
      items: [
        { sku: 'FD-1', category: 'food', unitPriceCents: 800, quantity: 3 }, // 2400 exempt
        { sku: 'EL-1', category: 'electronics', unitPriceCents: 2000, quantity: 1 },
      ],
    },
    config,
  );
  // subtotal 4400 -> tier min 2500 -> 399 shipping; tax on 2000 -> 160
  assert.deepEqual(totals, {
    subtotalCents: 4400,
    discountCents: 0,
    taxCents: 160,
    shippingCents: 399,
    totalCents: 4959,
  });
});
