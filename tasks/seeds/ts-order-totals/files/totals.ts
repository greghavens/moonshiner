// Order totals for the checkout service. All money is integer cents; every
// multiplication is rounded with Math.round at the point it produces cents,
// so totals are stable regardless of cart order.

export interface LineItem {
  sku: string;
  category: string;
  unitPriceCents: number;
  quantity: number;
}

export interface Order {
  items: LineItem[];
  /** Whole-order percentage discount, 0..100. */
  discountPercent?: number;
}

export interface PricingConfig {
  /** e.g. 0.08 for 8% */
  taxRate: number;
  shippingFlatCents: number;
}

export interface Totals {
  subtotalCents: number;
  discountCents: number;
  taxCents: number;
  shippingCents: number;
  totalCents: number;
}

function assertValidItem(item: LineItem): void {
  if (!Number.isInteger(item.quantity) || item.quantity < 1) {
    throw new RangeError(`invalid quantity for ${item.sku}: ${item.quantity}`);
  }
  if (!Number.isInteger(item.unitPriceCents) || item.unitPriceCents < 0) {
    throw new RangeError(`invalid unit price for ${item.sku}: ${item.unitPriceCents}`);
  }
}

export function computeTotals(order: Order, config: PricingConfig): Totals {
  const discountPercent = order.discountPercent ?? 0;
  if (discountPercent < 0 || discountPercent > 100) {
    throw new RangeError(`discountPercent out of range: ${discountPercent}`);
  }

  let subtotalCents = 0;
  for (const item of order.items) {
    assertValidItem(item);
    subtotalCents += item.unitPriceCents * item.quantity;
  }

  const discountCents = Math.round((subtotalCents * discountPercent) / 100);
  const taxableCents = subtotalCents - discountCents;
  const taxCents = Math.round(taxableCents * config.taxRate);
  const shippingCents = order.items.length > 0 ? config.shippingFlatCents : 0;

  return {
    subtotalCents,
    discountCents,
    taxCents,
    shippingCents,
    totalCents: subtotalCents - discountCents + taxCents + shippingCents,
  };
}
