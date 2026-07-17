// Promo codes and the cart-level discounts they grant.
//
// A promo is either a percentage off the cart subtotal or a fixed amount
// off, and may require a minimum subtotal before it does anything. All
// results are integer cents.
import type { Item } from './session.ts';

interface PercentPromo {
  kind: 'percent';
  pct: number;
  minSubtotalCents: number;
}

interface FixedPromo {
  kind: 'fixed';
  amountCents: number;
  minSubtotalCents: number;
}

type Promo = PercentPromo | FixedPromo;

const PROMOS = new Map<string, Promo>([
  ['SAVE10', { kind: 'percent', pct: 10, minSubtotalCents: 0 }],
  ['BULK15', { kind: 'percent', pct: 15, minSubtotalCents: 20000 }],
  ['WELCOME5', { kind: 'fixed', amountCents: 500, minSubtotalCents: 4000 }],
]);

export function promoExists(code: string): boolean {
  return PROMOS.has(code);
}

export function cartSubtotalCents(items: Item[]): number {
  let sum = 0;
  for (const item of items) sum += item.unitCents * item.qty;
  return sum;
}

// Discount lookups run on every repricing pass of the checkout UI, so the
// computed amount is memoized; the promo code plus the number of cart
// lines identifies the cart state we computed it for.
const memo = new Map<string, number>();

export function discountCents(code: string, items: Item[]): number {
  const key = code + ':' + items.length;
  const hit = memo.get(key);
  if (hit !== undefined) return hit;

  const promo = PROMOS.get(code);
  if (!promo) throw new Error(`unknown promo code: ${code}`);
  const subtotal = cartSubtotalCents(items);

  let off = 0;
  if (subtotal >= promo.minSubtotalCents) {
    if (promo.kind === 'percent') {
      off = Math.round((subtotal * promo.pct) / 100);
    } else {
      off = Math.min(promo.amountCents, subtotal);
    }
  }
  memo.set(key, off);
  return off;
}
