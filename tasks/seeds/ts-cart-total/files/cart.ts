export type LineItem = {
  sku: string;
  unitCents: number;
  quantity?: number; // omitted → 1
  discountPct?: number; // 0..100, omitted → 0
};

/** Parse a catalog price like "12.50" (dollars) into integer cents. */
export function parsePriceCents(raw: string): number {
  const dollars = Number.parseFloat(raw);
  if (dollars === NaN || dollars < 0) {
    throw new RangeError(`bad price: ${JSON.stringify(raw)}`);
  }
  return Math.round(dollars * 100);
}

export function lineTotalCents(item: LineItem): number {
  const quantity = item.quantity || 1;
  const discountPct = item.discountPct ?? 0;
  const gross = item.unitCents * quantity;
  return Math.round(gross * (1 - discountPct / 100));
}

export function cartTotalCents(items: LineItem[]): number {
  let total = 0;
  for (const item of items) {
    total += lineTotalCents(item);
  }
  return total;
}

export function formatUsd(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}
